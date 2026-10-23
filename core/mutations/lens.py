"""Lenses: one selection, one row, over any container that lives in a coordinate system.

``createLens`` selects over an array by slices; the five ``create<Kind>Lens`` mutations select
over the other containers by value windows. Each takes the container, or a lens over it: a
selection stated over a lens is cut from that lens -- slices compose, windows intersect -- and
is the container's row for what it ends up selecting, so a crop of a crop that equals an
existing crop is that crop. Every one is get-or-create: asking twice for the same selection
hands back the same id. ``deleteLens`` takes any kind, and refuses a lens in use or a
container's whole lens (:func:`core.logic.coordinate_system.assert_lens_deletable`).
"""

from kante.types import Info
from typing import cast
import strawberry

from core import types, models

import kante
from pydantic import BaseModel, Field
from core import base_models, inputs
from core.creation import CreationContext
from core.logic import coordinate_system as coordinate_system_logic
from core.scoping import get_for_org
from core.mutations._generic import assert_can_delete, make_delete
from core.mutations._sources import resolve_source_lens


class CreateLensInputModel(BaseModel):
    dataset: str | None = Field(default=None, description="The array dataset to select over. Exactly one of this and `lens`")
    lens: str | None = Field(default=None, description="An array lens to cut from, in place of `dataset`: a crop of a crop. The slices are stated in this lens' own index space and composed through it; the result is the dataset's lens for the voxels that keeps")
    slices: list[base_models.SliceInputModel] | None = Field(
        default=None, description="Slices selecting a window of the dataset (or of the lens), one per axis to restrict. Omit, or pass an empty list, for everything: the dataset's whole lens, or the lens cut from. The same selection, however spelled, is the same lens"
    )
    box: base_models.LensBoxInputModel | None = Field(default=None, description="In place of `slices`: a box in any coordinate system the dataset reaches, cut to the voxels whose cells it meets. With `dataset` only: a box names its own frame, it is not relative to a crop")
    annotation: str | None = Field(default=None, description="In place of `slices`: an annotation, cut to the voxels under its stored box (crop to the ROI). With `dataset`, of the whole dataset; with `lens`, what that lens selects under the box. The cut is recorded: the annotation lists the lens as one of its `derivedLenses`")
    name: str | None = Field(default=None, description="What to call this selection. Applied when the lens has no name yet; a lens that already makes this selection and is named keeps its name: finding a selection is not a request to rename it, `updateLens` is")


_OVER = "Exactly one of `dataset` and `lens`: the array dataset to select over, or an array lens to select *within* -- the slices are then stated in that lens' own index space and composed through it, and the result is the dataset's lens for the voxels that keeps"


@kante.pydantic_input(CreateLensInputModel, description="Input for the lens making a selection over an array dataset -- or within one of its lenses -- by slices")
class CreateLensInput:
    dataset: strawberry.ID | None = strawberry.field(default=None, description=_OVER)
    lens: strawberry.ID | None = strawberry.field(default=None, description="An array lens to cut from, in place of `dataset`: a crop of a crop. The slices are stated in this lens' index space")
    slices: list[inputs.SliceInput] | None = strawberry.field(
        default=None, description="Optional slices selecting a window of the dataset (or of the lens), one per axis to restrict. Omit (or pass an empty list) for everything -- the dataset's whole lens, or the lens cut from. The same selection, however spelled, is the same lens"
    )
    box: inputs.LensBoxInput | None = strawberry.field(default=None, description="In place of `slices`: a box in any coordinate system the dataset reaches, cut to the voxels whose cells it meets. With `dataset` only -- a box names its own frame, it is not relative to a crop")
    annotation: strawberry.ID | None = strawberry.field(default=None, description="In place of `slices`: an annotation, cut to the voxels under its stored box -- crop to the ROI. With `dataset`, of the whole dataset; with `lens`, what that lens selects under the box: a channel it pins stays pinned, a range it keeps is intersected, a shape that misses it is refused. The cut is recorded: the annotation lists the lens as one of its `derivedLenses`",
    )
    name: str | None = strawberry.field(default=None, description="What to call this selection. Applied when the lens has no name yet; a lens that already makes this selection and is named keeps its name -- finding a selection is not a request to rename it, `updateLens` is")


def create_lens(
    info: Info,
    input: CreateLensInput,
) -> types.ArrayLens:
    """The array lens making this selection: the one that exists, or a new one with its coordinate system and its edge back into the dataset."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    selections = [field for field in ("slices", "box", "annotation") if getattr(model, field)]
    if len(selections) > 1:
        raise ValueError(f"A lens makes one selection: give `slices`, `box` or `annotation`, not {' and '.join(f'`{field}`' for field in selections)}.")
    if model.box is not None:
        if model.dataset is None or model.lens is not None:
            raise ValueError("`box` names its own frame and is cut from the whole dataset: give it with `dataset`, not with `lens`.")
        dataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
        system = get_for_org(models.CoordinateSystem, info, id=model.box.coordinate_system)
        lens = coordinate_system_logic.create_lens_from_box(dataset, system, model.box.windows, ctx)
    elif model.annotation is not None:
        # The shape's box names its own frame either way; over a lens only the *answer* is
        # narrowed, to what that lens selects under it.
        parent = cast(models.ArrayLens, resolve_source_lens(info, kind=models.ArrayLens, lens=model.lens, container_model=models.ArrayDataset, container=model.dataset, what="dataset", role="a crop to an annotation is cut within"))
        annotation = get_for_org(models.Annotation, info, id=model.annotation)
        lens = coordinate_system_logic.create_lens_from_annotation(parent.dataset, annotation, ctx, within=parent if model.lens is not None else None)
    else:
        parent = resolve_source_lens(info, kind=models.ArrayLens, lens=model.lens, container_model=models.ArrayDataset, container=model.dataset, what="dataset", role="a selection by slices is cut from")
        lens = coordinate_system_logic.create_sub_lens(parent, model.slices or [], ctx)
    name = (model.name or "").strip() or None
    if name and lens.name is None:
        lens.name = name
        lens.save(update_fields=["name"])
    return lens


_WINDOWS = "Optional windows, one per axis to restrict, each keeping the positions whose value lies between its min and its max (an omitted side is open). Stated over a lens, they narrow that lens' windows. Omit for everything -- the container's whole lens, which exists from creation, or the lens cut from. A window keeps and re-indexes nothing, so the lens lives in the container's own coordinate system"
_WITHIN = "A lens of this kind to select within, in place of the container: the windows then intersect with its own, and the result is the container's lens for what that keeps"


def _windowed(info: Info, *, kind: type, container_model: type, container: str | None, lens: str | None, windows: list | None, what: str) -> "models.Lens":
    """The windowed lens of one kind: over the container, or within one of its lenses."""
    parent = resolve_source_lens(info, kind=kind, lens=lens, container_model=container_model, container=container, what=what, role="a selection by windows is cut from")
    return coordinate_system_logic.create_sub_windowed_lens(parent, windows or [], CreationContext.from_info(info))


class CreateTableLensInputModel(BaseModel):
    table_dataset: str | None = Field(default=None, description="The table dataset to select over. Exactly one of this and `lens`")
    lens: str | None = Field(default=None, description="A lens over the table dataset to select within, in place of the container: the windows then intersect with its own, and the result is the table dataset's lens for what that keeps")
    windows: list[base_models.WindowInputModel] | None = Field(default=None, description="Windows, one per axis to restrict, each keeping the positions whose value lies between its min and its max (an omitted side is open). Omit for everything: the table dataset's whole lens, or the lens cut from")


@kante.pydantic_input(CreateTableLensInputModel, description="Input for the lens making a selection over a table dataset, by windows on its coordinate columns")
class CreateTableLensInput:
    table_dataset: strawberry.ID | None = strawberry.field(default=None, description="The table dataset to select over. Exactly one of this and `lens`")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_WITHIN)
    windows: list[inputs.WindowInput] | None = strawberry.field(default=None, description=_WINDOWS)


def create_table_lens(info: Info, input: CreateTableLensInput) -> types.TableLens:
    """The table lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    return _windowed(info, kind=models.TableLens, container_model=models.TableDataset, container=model.table_dataset, lens=model.lens, windows=model.windows, what="tableDataset")


class CreateSparseLensInputModel(BaseModel):
    sparse_dataset: str | None = Field(default=None, description="The sparse dataset to select over. Exactly one of this and `lens`")
    lens: str | None = Field(default=None, description="A lens over the sparse dataset to select within, in place of the container: the windows then intersect with its own, and the result is the sparse dataset's lens for what that keeps")
    windows: list[base_models.WindowInputModel] | None = Field(default=None, description="Windows, one per axis to restrict, each keeping the positions whose value lies between its min and its max (an omitted side is open). Omit for everything: the sparse dataset's whole lens, or the lens cut from")


@kante.pydantic_input(CreateSparseLensInputModel, description="Input for the lens making a selection over a sparse dataset, by windows on its axes")
class CreateSparseLensInput:
    sparse_dataset: strawberry.ID | None = strawberry.field(default=None, description="The sparse dataset to select over. Exactly one of this and `lens`")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_WITHIN)
    windows: list[inputs.WindowInput] | None = strawberry.field(default=None, description=_WINDOWS)


def create_sparse_lens(info: Info, input: CreateSparseLensInput) -> types.SparseLens:
    """The sparse lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    return _windowed(info, kind=models.SparseLens, container_model=models.SparseDataset, container=model.sparse_dataset, lens=model.lens, windows=model.windows, what="sparseDataset")


class CreateMeshLensInputModel(BaseModel):
    mesh_collection: str | None = Field(default=None, description="The mesh collection to select over. Exactly one of this and `lens`")
    lens: str | None = Field(default=None, description="A lens over the mesh collection to select within, in place of the container: the windows then intersect with its own, and the result is the mesh collection's lens for what that keeps")
    windows: list[base_models.WindowInputModel] | None = Field(
        default=None, description="Windows, one per axis to restrict, each keeping the positions whose value lies between its min and its max (an omitted side is open). Omit for everything: the mesh collection's whole lens, or the lens cut from"
    )


@kante.pydantic_input(CreateMeshLensInputModel, description="Input for the lens making a selection over a mesh collection, by windows in its own space")
class CreateMeshLensInput:
    mesh_collection: strawberry.ID | None = strawberry.field(default=None, description="The mesh collection to select over. Exactly one of this and `lens`")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_WITHIN)
    windows: list[inputs.WindowInput] | None = strawberry.field(default=None, description=_WINDOWS)


def create_mesh_lens(info: Info, input: CreateMeshLensInput) -> types.MeshLens:
    """The mesh lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    return _windowed(info, kind=models.MeshLens, container_model=models.MeshCollection, container=model.mesh_collection, lens=model.lens, windows=model.windows, what="meshCollection")


class CreateCadModelLensInputModel(BaseModel):
    cad_model: str | None = Field(default=None, description="The CAD model to select over. Exactly one of this and `lens`")
    lens: str | None = Field(default=None, description="A lens over the CAD model to select within, in place of the container: the windows then intersect with its own, and the result is the model's lens for what that keeps")
    windows: list[base_models.WindowInputModel] | None = Field(
        default=None, description="Windows, one per axis to restrict, in the model's own axes and unit: a clip box. Omit for everything: the model's whole lens, or the lens cut from"
    )


@kante.pydantic_input(CreateCadModelLensInputModel, description="Input for the lens making a selection over a CAD model, by windows in its own space: a clip box")
class CreateCadModelLensInput:
    cad_model: strawberry.ID | None = strawberry.field(default=None, description="The CAD model to select over. Exactly one of this and `lens`")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_WITHIN)
    windows: list[inputs.WindowInput] | None = strawberry.field(default=None, description=_WINDOWS)


def create_cad_model_lens(info: Info, input: CreateCadModelLensInput) -> types.CadModelLens:
    """The CAD model lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    return _windowed(info, kind=models.CadModelLens, container_model=models.CadModel, container=model.cad_model, lens=model.lens, windows=model.windows, what="cadModel")


class CreateNetworkLensInputModel(BaseModel):
    network_collection: str | None = Field(default=None, description="The network collection to select over. Exactly one of this and `lens`")
    lens: str | None = Field(default=None, description="A lens over the network collection to select within, in place of the container: the windows then intersect with its own, and the result is the network collection's lens for what that keeps")
    windows: list[base_models.WindowInputModel] | None = Field(
        default=None, description="Windows, one per axis to restrict, each keeping the positions whose value lies between its min and its max (an omitted side is open). Omit for everything: the network collection's whole lens, or the lens cut from"
    )


@kante.pydantic_input(CreateNetworkLensInputModel, description="Input for the lens making a selection over a network collection, by windows in its own space")
class CreateNetworkLensInput:
    network_collection: strawberry.ID | None = strawberry.field(default=None, description="The network collection to select over. Exactly one of this and `lens`")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_WITHIN)
    windows: list[inputs.WindowInput] | None = strawberry.field(default=None, description=_WINDOWS)


def create_network_lens(info: Info, input: CreateNetworkLensInput) -> types.NetworkLens:
    """The network lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    return _windowed(info, kind=models.NetworkLens, container_model=models.NetworkCollection, container=model.network_collection, lens=model.lens, windows=model.windows, what="networkCollection")


class CreateAnnotationLensInputModel(BaseModel):
    annotation_collection: str | None = Field(default=None, description="The annotation collection to select over. Exactly one of this and `lens`")
    lens: str | None = Field(default=None, description="A lens over the annotation collection to select within, in place of the container: the windows then intersect with its own, and the result is the annotation collection's lens for what that keeps")
    windows: list[base_models.WindowInputModel] | None = Field(
        default=None, description="Windows, one per axis to restrict, each keeping the positions whose value lies between its min and its max (an omitted side is open). Omit for everything: the annotation collection's whole lens, or the lens cut from"
    )


@kante.pydantic_input(CreateAnnotationLensInputModel, description="Input for the lens making a selection over an annotation collection, by windows in its drawing space")
class CreateAnnotationLensInput:
    annotation_collection: strawberry.ID | None = strawberry.field(default=None, description="The annotation collection to select over. Exactly one of this and `lens`")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_WITHIN)
    windows: list[inputs.WindowInput] | None = strawberry.field(default=None, description=_WINDOWS)


def create_annotation_lens(info: Info, input: CreateAnnotationLensInput) -> types.AnnotationLens:
    """The annotation lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    return _windowed(info, kind=models.AnnotationLens, container_model=models.AnnotationCollection, container=model.annotation_collection, lens=model.lens, windows=model.windows, what="annotationCollection")


class DeleteLensInputModel(BaseModel):
    id: str = Field(description="The ID of the lens to delete")


@kante.pydantic_input(DeleteLensInputModel, description="Input for deleting a lens by ID")
class DeleteLensInput:
    """Input for deleting a lens by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the lens to delete, of any kind. Refused while a layer draws through it, and for a container's whole lens")


def lens_owner(item: "models.Lens") -> tuple:
    """Whoever made the selection, or whoever owns what it selects over; an org admin either way.

    A crop someone cut over another person's dataset is theirs to rename, nominate for and
    delete; the dataset's owner may do the same to any lens over their data, because it is
    their data's cards a lens changes. A whole lens carries its container's provenance, so
    both halves name the same people.
    """
    container = item.container
    return (item.creator_id, item.created_through_by_id, getattr(container, "creator_id", None), getattr(container, "created_through_by_id", None))


# A sliced array lens takes its own space and edge with it, unless something else is attached to that space.
delete_lens = make_delete(models.Lens, DeleteLensInput, owner=lens_owner, delete=coordinate_system_logic.delete_lens)


class UpdateLensInputModel(BaseModel):
    id: str = Field(description="The ID of the lens to update")
    name: str | None = Field(default=None, description="The new name, or null (or blank) to clear it")


@kante.pydantic_input(UpdateLensInputModel, description="Input for renaming a lens -- the whole of what is editable about one")
class UpdateLensInput:
    """Input for renaming a lens."""

    id: strawberry.ID = strawberry.field(description="The ID of the lens to update")
    name: str | None = strawberry.field(default=None, description="What to call this selection. Null or blank clears the name, and the lens reads as its selection again")


def update_lens(info: Info, input: UpdateLensInput) -> types.Lens:
    """Rename a lens, or clear its name.

    The name is the only thing a lens has that is not derived from its container and its
    selection, so it is the only thing that can change: a different selection is a different
    lens. Guarded as the nomination is: the lens' maker or the container's owner.
    """
    model = input.to_pydantic()
    lens = get_for_org(models.Lens, info, id=model.id)
    assert_can_delete(info, lens, lens_owner)
    lens.name = (model.name or "").strip() or None
    lens.save(update_fields=["name"])
    return lens


class SetLensDefaultSceneInputModel(BaseModel):
    """A lens and the scene it nominates, or null to clear."""

    lens: str = Field(description="The lens to nominate a scene for")
    scene: str | None = Field(default=None, description="The scene to nominate, or null to clear the nomination")


@kante.pydantic_input(SetLensDefaultSceneInputModel, description="Nominate the scene to open for a lens, and take its thumbnail from")
class SetLensDefaultSceneInput:
    """Input for nominating a lens' default scene."""

    lens: strawberry.ID = strawberry.field(description="The lens to nominate a scene for")
    scene: strawberry.ID | None = strawberry.field(default=None, description="The scene to nominate. Null clears the nomination, and a crop or a window then falls back to its container's whole lens'. On a whole lens this is the container's nomination")


def nominate_lens_default_scene(info: Info, lens: "models.Lens", scene: "models.Scene | None") -> "models.Lens":
    """Write the nomination a lens answers `defaultScene` with, having checked the caller may.

    The lens' own column, for every kind and whether or not it is whole: a container's
    nomination *is* its whole lens', so `setDefaultScene` on a dataset and this on its whole
    lens are one write. Guarded by `lens_owner` -- the lens' maker or the container's owner --
    since it is the container's cards this changes.
    """
    assert_can_delete(info, lens, lens_owner)
    lens.default_scene = scene
    lens.save(update_fields=["default_scene"])
    return lens


def set_lens_default_scene(info: Info, input: SetLensDefaultSceneInput) -> types.Lens:
    """Nominate the scene to open for a lens, or clear the nomination.

    No check that the scene shows the lens, for the reason `set_default_scene` gives: the
    nomination is bookkeeping, and validating it against `scenes` would make it a claim about
    placement.
    """
    model = input.to_pydantic()
    lens = get_for_org(models.Lens, info, id=model.lens)
    scene = get_for_org(models.Scene, info, id=model.scene) if model.scene is not None else None
    return nominate_lens_default_scene(info, lens, scene)
