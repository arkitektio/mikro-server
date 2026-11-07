"""Grids: created over a world, bootstrapped from one, renamed, deleted. A grid's world never changes."""

import kante
import strawberry
from django.db import transaction
from kante.types import Info
from pydantic import BaseModel, Field

from core import models as core_models
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.mutations._generic import creator_owner, make_delete
from core.scoping import get_for_org
from grid import models, types
from grid.logic import grid as grid_logic


class CreateGridInputModel(BaseModel):
    name: str
    coordinate_system: str
    description: str | None = None


@prose_errors
@kante.pydantic_input(
    CreateGridInputModel,
    description=(
        "Input for creating a grid over an existing space whose one or two enumerating axes (INDEX, CHANNEL, CATEGORY) are its rows and columns -- a sparse dataset's "
        "own space, a long-form table's. Adopted as it is, never minted. It starts with no layers"
    ),
)
class CreateGridInput:
    """Input for creating a grid."""

    name: str = strawberry.field(description="The name of the grid")
    coordinate_system: strawberry.ID = strawberry.field(description="The space to lay out: one or two enumerating axes, rows then columns. Never owned by the grid")
    description: str | None = strawberry.field(default=None, description="A free-form description of what the grid shows")


def create_grid(info: Info, input: CreateGridInput) -> types.Grid:
    """Create a grid over an existing space."""
    model = input.to_pydantic()
    world = get_for_org(core_models.CoordinateSystem, info, id=model.coordinate_system)
    return grid_logic.create_grid(name=model.name, description=model.description, world=world, ctx=CreationContext.from_info(info))


class GridPolicyInputModel(BaseModel):
    nchildren: int = Field(default=32, ge=0)


@kante.pydantic_input(GridPolicyInputModel, description="How a grid is drawn from what is already placed in a space")
class GridPolicyInput:
    """How a grid is bootstrapped from a space."""

    nchildren: int = strawberry.field(default=32, description="Draw at most this many layers")


class CreateGridFromCoordinateSystemInputModel(BaseModel):
    coordinate_system: str
    name: str | None = None
    policy: GridPolicyInputModel = GridPolicyInputModel()


@kante.pydantic_input(
    CreateGridFromCoordinateSystemInputModel,
    description=(
        "Bootstrap a grid over an existing space, drawing what already covers its axes: each sparse matrix and array as a heatmap, each table as cells coloured by its one "
        "numeric column. Authors no edges, and leaves out whatever its kind refuses"
    ),
)
class CreateGridFromCoordinateSystemInput:
    """Input for bootstrapping a grid over an existing coordinate system."""

    coordinate_system: strawberry.ID = strawberry.field(description="The space to lay out: one or two enumerating axes")
    name: str | None = strawberry.field(default=None, description="The name of the grid. Defaults to the coordinate system's name")
    policy: GridPolicyInput = strawberry.field(default_factory=GridPolicyInput, description="How the grid is materialized")


def create_grid_from_coordinate_system(info: Info, input: CreateGridFromCoordinateSystemInput) -> types.Grid:
    """Bootstrap a grid over an existing coordinate system and what covers its axes."""
    model = input.to_pydantic()
    system = get_for_org(core_models.CoordinateSystem, info, id=model.coordinate_system)
    return grid_logic.bootstrap_grid_from_system(system, name=model.name, policy=grid_logic.Policy(nchildren=model.policy.nchildren), ctx=CreationContext.from_info(info))


class UpdateGridInputModel(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None


@kante.pydantic_input(UpdateGridInputModel, description="Input for renaming or re-describing a grid. Its world is not editable: a grid over another space is another grid")
class UpdateGridInput:
    """Input for updating a grid."""

    id: strawberry.ID = strawberry.field(description="The ID of the grid to update")
    name: str | None = strawberry.field(default=None, description="A new name. Omit to leave it as it is")
    description: str | None = strawberry.field(default=None, description="A new description. Omit to leave it as it is")


def update_grid(info: Info, input: UpdateGridInput) -> types.Grid:
    """Rename or re-describe a grid."""
    parsed = input.to_pydantic()
    grid = get_for_org(models.Grid, info, id=parsed.id)
    updated: list[str] = []
    if parsed.name is not None:
        grid.name = parsed.name
        updated.append("name")
    if parsed.description is not None:
        grid.description = parsed.description
        updated.append("description")
    if updated:
        with transaction.atomic():
            grid.save(update_fields=updated)
    return grid


class DeleteGridInputModel(BaseModel):
    id: str = Field(description="The ID of the grid to delete")


@kante.pydantic_input(DeleteGridInputModel, description="Input for deleting a grid by ID. Deletes its layers and nothing they drew: the data, and the grid's world, are untouched")
class DeleteGridInput:
    """Input for deleting a grid by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the grid to delete")


delete_grid = make_delete(models.Grid, DeleteGridInput, owner=creator_owner)
