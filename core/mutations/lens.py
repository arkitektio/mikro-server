from kante.types import Info
import strawberry

from core import types, models

import kante
from pydantic import BaseModel, Field
from core import base_models, inputs
from core.creation import CreationContext
from core.logic import coordinate_system as coordinate_system_logic
from core.scoping import get_for_org
from core.mutations._generic import make_delete, dataset_owner


class CreateLensInputModel(BaseModel):
    dataset: str
    slices: list[base_models.SliceInputModel] | None = None


@kante.pydantic_input(CreateLensInputModel, description="Input type for creating an image from an array-like object")
class CreateLensInput:
    dataset: strawberry.ID = strawberry.field(description="The ID of an existing dataset to create the lens from. If not provided, a new dataset will be created for the lens")
    slices: list[inputs.SliceInput] = strawberry.field(default=None, description="Optional slices selecting a window of the dataset, one per axis to restrict. Omit (or pass an empty list) for a lens that selects everything; a sliced lens gets its own coordinate system and the edge recording the shift")


def create_lens(
    info: Info,
    input: CreateLensInput,
) -> types.Lens:
    """Create a lens, its coordinate system, and the edge placing it back in its dataset.

    The lens' shape and axes are not written: they follow from the dataset and the
    slices, and a second copy could only drift from the first.
    """
    model = input.to_pydantic()

    dataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
    ctx = CreationContext.from_info(info)

    return coordinate_system_logic.create_lens(dataset, model.slices or [], ctx)


class DeleteLensInputModel(BaseModel):
    id: str = Field(description="The ID of the lens to delete")


@kante.pydantic_input(DeleteLensInputModel, description="Input for deleting a lens by ID")
class DeleteLensInput:
    """Input for deleting a lens by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the lens to delete")


delete_lens = make_delete(models.Lens, DeleteLensInput, owner=dataset_owner)
