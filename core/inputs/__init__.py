"""GraphQL input types shared across mutations.

Input classes live here (not in ``core.mutations``) so the service layer in
``core.logic`` can reference them without importing mutation modules.
"""

from typing import List
import strawberry
import kante
from pydantic import BaseModel, Field
from core import base_models

class AssociateInputModel(BaseModel):
    selfs: List[str] = Field(description="The IDs of the items to associate")
    other: str = Field(description="The ID of the target item")


@kante.pydantic_input(AssociateInputModel, description="An input for associating a set of items with another item, e.g. putting images into a dataset")
class AssociateInput:
    """Input for associating a set of items with another item"""

    selfs: List[strawberry.ID] = strawberry.field(description="The IDs of the items to associate")
    other: strawberry.ID = strawberry.field(description="The ID of the target item")


class DesociateInputModel(BaseModel):
    selfs: List[str] = Field(description="The IDs of the items to release")
    other: str = Field(description="The ID of the target item")


@kante.pydantic_input(DesociateInputModel, description="An input for releasing a set of items from another item, e.g. removing images from a dataset")
class DesociateInput:
    """Input for releasing a set of items from another item"""

    selfs: List[strawberry.ID] = strawberry.field(description="The IDs of the items to release")
    other: strawberry.ID = strawberry.field(description="The ID of the target item")


@kante.pydantic_input(base_models.SliceInputModel, description="Input type for a slice along one axis of a dataset")
class SliceInput:
    """Input for a slice along a single axis of a dataset"""

    axis: str = strawberry.field(description="The name of the axis the slice acts on, e.g. 'x', 'y', 'z', 'c', or 't'")
    start: int | None = strawberry.field(default=None, description="The starting index of the slice, or None to start from the beginning")
    stop: int | None = strawberry.field(default=None, description="The stopping index of the slice, or None to go to the end")
    step: int | None = strawberry.field(default=None, description="The step size of the slice, or None to use the default step")


@kante.pydantic_input(base_models.WindowInputModel, description="Input type for a window along one axis of a container that is not an array: the positions kept, by value")
class WindowInput:
    """Input for a value window along a single axis of a table, sparse dataset, mesh, network or annotation collection"""

    axis: str = strawberry.field(description="The name of the axis the window constrains, e.g. 't' for a table's time column")
    min: float | None = strawberry.field(default=None, description="The smallest position kept, inclusive, or None for no lower bound")
    max: float | None = strawberry.field(default=None, description="The largest position kept, inclusive, or None for no upper bound")
