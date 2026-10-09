"""Lenses: one selection, one row, over any container that lives in a coordinate system.

``createLens`` selects over an array by slices; the five ``create<Kind>Lens`` mutations select
over the other containers by value windows. Every one is get-or-create: asking twice for the
same selection hands back the same id. ``deleteLens`` takes any kind, and refuses a lens in use
or a container's whole lens (:func:`core.logic.coordinate_system.assert_lens_deletable`).
"""

from kante.types import Info
import strawberry

from core import types, models

import kante
from pydantic import BaseModel, Field
from core import base_models, inputs
from core.creation import CreationContext
from core.logic import coordinate_system as coordinate_system_logic
from core.scoping import get_for_org
from core.mutations import array_dataset as array_dataset_mutations
from core.mutations._generic import assert_can_delete, make_delete


class CreateLensInputModel(BaseModel):
    dataset: str
    slices: list[base_models.SliceInputModel] | None = None
    name: str | None = None


@kante.pydantic_input(CreateLensInputModel, description="Input for the lens making a selection over an array dataset, by slices")
class CreateLensInput:
    dataset: strawberry.ID = strawberry.field(description="The array dataset to select over")
    slices: list[inputs.SliceInput] = strawberry.field(default=None, description="Optional slices selecting a window of the dataset, one per axis to restrict. Omit (or pass an empty list) for the lens that selects everything -- the dataset's whole lens, which exists from creation. The same selection, however spelled, is the same lens")
    name: str | None = strawberry.field(default=None, description="What to call this selection. Applied when the lens has no name yet; a lens that already makes this selection and is named keeps its name -- finding a selection is not a request to rename it, `updateLens` is")


def create_lens(
    info: Info,
    input: CreateLensInput,
) -> types.ArrayLens:
    """The array lens making this selection: the one that exists, or a new one with its coordinate system and its edge back into the dataset."""
    model = input.to_pydantic()

    dataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
    ctx = CreationContext.from_info(info)

    lens = coordinate_system_logic.create_lens(dataset, model.slices or [], ctx)
    name = (model.name or "").strip() or None
    if name and lens.name is None:
        lens.name = name
        lens.save(update_fields=["name"])
    return lens


_WINDOWS = "Optional windows, one per axis to restrict, each keeping the positions whose value lies between its min and its max (an omitted side is open). Omit for the lens that selects everything -- the container's whole lens, which exists from creation. A window keeps and re-indexes nothing, so the lens lives in the container's own coordinate system"


class CreateTableLensInputModel(BaseModel):
    table_dataset: str
    windows: list[base_models.WindowInputModel] | None = None


@kante.pydantic_input(CreateTableLensInputModel, description="Input for the lens making a selection over a table dataset, by windows on its coordinate columns")
class CreateTableLensInput:
    table_dataset: strawberry.ID = strawberry.field(description="The table dataset to select over")
    windows: list[inputs.WindowInput] = strawberry.field(default=None, description=_WINDOWS)


def create_table_lens(info: Info, input: CreateTableLensInput) -> types.TableLens:
    """The table lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    table = get_for_org(models.TableDataset, info, id=model.table_dataset)
    return coordinate_system_logic.create_windowed_lens(table, model.windows or [], CreationContext.from_info(info))


class CreateSparseLensInputModel(BaseModel):
    sparse_dataset: str
    windows: list[base_models.WindowInputModel] | None = None


@kante.pydantic_input(CreateSparseLensInputModel, description="Input for the lens making a selection over a sparse dataset, by windows on its axes")
class CreateSparseLensInput:
    sparse_dataset: strawberry.ID = strawberry.field(description="The sparse dataset to select over")
    windows: list[inputs.WindowInput] = strawberry.field(default=None, description=_WINDOWS)


def create_sparse_lens(info: Info, input: CreateSparseLensInput) -> types.SparseLens:
    """The sparse lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    dataset = get_for_org(models.SparseDataset, info, id=model.sparse_dataset)
    return coordinate_system_logic.create_windowed_lens(dataset, model.windows or [], CreationContext.from_info(info))


class CreateMeshLensInputModel(BaseModel):
    mesh_collection: str
    windows: list[base_models.WindowInputModel] | None = None


@kante.pydantic_input(CreateMeshLensInputModel, description="Input for the lens making a selection over a mesh collection, by windows in its own space")
class CreateMeshLensInput:
    mesh_collection: strawberry.ID = strawberry.field(description="The mesh collection to select over")
    windows: list[inputs.WindowInput] = strawberry.field(default=None, description=_WINDOWS)


def create_mesh_lens(info: Info, input: CreateMeshLensInput) -> types.MeshLens:
    """The mesh lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    collection = get_for_org(models.MeshCollection, info, id=model.mesh_collection)
    return coordinate_system_logic.create_windowed_lens(collection, model.windows or [], CreationContext.from_info(info))


class CreateNetworkLensInputModel(BaseModel):
    network_collection: str
    windows: list[base_models.WindowInputModel] | None = None


@kante.pydantic_input(CreateNetworkLensInputModel, description="Input for the lens making a selection over a network collection, by windows in its own space")
class CreateNetworkLensInput:
    network_collection: strawberry.ID = strawberry.field(description="The network collection to select over")
    windows: list[inputs.WindowInput] = strawberry.field(default=None, description=_WINDOWS)


def create_network_lens(info: Info, input: CreateNetworkLensInput) -> types.NetworkLens:
    """The network lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    collection = get_for_org(models.NetworkCollection, info, id=model.network_collection)
    return coordinate_system_logic.create_windowed_lens(collection, model.windows or [], CreationContext.from_info(info))


class CreateAnnotationLensInputModel(BaseModel):
    annotation_collection: str
    windows: list[base_models.WindowInputModel] | None = None


@kante.pydantic_input(CreateAnnotationLensInputModel, description="Input for the lens making a selection over an annotation collection, by windows in its drawing space")
class CreateAnnotationLensInput:
    annotation_collection: strawberry.ID = strawberry.field(description="The annotation collection to select over")
    windows: list[inputs.WindowInput] = strawberry.field(default=None, description=_WINDOWS)


def create_annotation_lens(info: Info, input: CreateAnnotationLensInput) -> types.AnnotationLens:
    """The annotation lens keeping these windows: the one that exists, or a new one."""
    model = input.to_pydantic()
    collection = get_for_org(models.AnnotationCollection, info, id=model.annotation_collection)
    return coordinate_system_logic.create_windowed_lens(collection, model.windows or [], CreationContext.from_info(info))


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
    scene: strawberry.ID | None = strawberry.field(default=None, description="The scene to nominate. Null clears the nomination, and a lens that selects something then falls back to its container's")


def nominate_lens_default_scene(info: Info, lens: "models.Lens", scene: "models.Scene | None") -> "models.Lens":
    """Write the nomination a lens answers `defaultScene` with, having checked the caller may.

    **A whole lens writes its container's nomination.** It selects everything, so the scene to
    open for it is the scene to open for the container; a column on the lens would be a second
    copy of one answer, free to disagree with the first. Only an array dataset nominates a
    scene today, so a whole lens over anything else is refused in prose.

    Guarded by `lens_owner` either way -- the lens' maker or the container's owner -- and for a
    whole lens by `nominate_default_scene`'s own guard, since it is the container's cards this
    changes.
    """
    if lens.is_whole:
        container = lens.container
        if not isinstance(container, models.ArrayDataset):
            raise ValueError(f"Lens {lens.pk} selects the whole of {type(container).__name__} {container.pk}, which nominates no scene; a selection over it may.")
        array_dataset_mutations.nominate_default_scene(info, container, scene)
        return lens
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
