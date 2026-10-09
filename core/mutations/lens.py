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
from core.mutations._generic import make_delete


class CreateLensInputModel(BaseModel):
    dataset: str
    slices: list[base_models.SliceInputModel] | None = None


@kante.pydantic_input(CreateLensInputModel, description="Input for the lens making a selection over an array dataset, by slices")
class CreateLensInput:
    dataset: strawberry.ID = strawberry.field(description="The array dataset to select over")
    slices: list[inputs.SliceInput] = strawberry.field(default=None, description="Optional slices selecting a window of the dataset, one per axis to restrict. Omit (or pass an empty list) for the lens that selects everything -- the dataset's whole lens, which exists from creation. The same selection, however spelled, is the same lens")


def create_lens(
    info: Info,
    input: CreateLensInput,
) -> types.ArrayLens:
    """The array lens making this selection: the one that exists, or a new one with its coordinate system and its edge back into the dataset."""
    model = input.to_pydantic()

    dataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
    ctx = CreationContext.from_info(info)

    return coordinate_system_logic.create_lens(dataset, model.slices or [], ctx)


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
    return coordinate_system_logic.create_windowed_lens(table, model.windows or [])


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
    return coordinate_system_logic.create_windowed_lens(dataset, model.windows or [])


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
    return coordinate_system_logic.create_windowed_lens(collection, model.windows or [])


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
    return coordinate_system_logic.create_windowed_lens(collection, model.windows or [])


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
    return coordinate_system_logic.create_windowed_lens(collection, model.windows or [])


class DeleteLensInputModel(BaseModel):
    id: str = Field(description="The ID of the lens to delete")


@kante.pydantic_input(DeleteLensInputModel, description="Input for deleting a lens by ID")
class DeleteLensInput:
    """Input for deleting a lens by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the lens to delete, of any kind. Refused while a layer draws through it, and for a container's whole lens")


def lens_owner(item: "models.Lens") -> tuple:
    """Owner ids inherited from the container the lens selects over."""
    container = item.container
    return (getattr(container, "creator_id", None), getattr(container, "created_through_by_id", None))


delete_lens = make_delete(models.Lens, DeleteLensInput, owner=lens_owner)
