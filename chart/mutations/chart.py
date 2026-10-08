"""Charts: created over a world, renamed, deleted. A chart's world never changes."""

import kante
import strawberry
from django.db import transaction
from kante.types import Info
from pydantic import BaseModel, Field

from chart import models, types
from chart.logic import chart as chart_logic
from core import models as core_models
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.inputs.coords import PhysicalAxisInput, PhysicalAxisInputModel
from core.mutations._generic import creator_owner, make_delete
from core.scoping import get_for_org


class CreateChartInputModel(BaseModel):
    name: str
    description: str | None = None
    coordinate_system: str | None = None
    axis: PhysicalAxisInputModel | None = None


@prose_errors
@kante.pydantic_input(
    CreateChartInputModel,
    description=(
        "Input type for creating a chart: a composition of data laid out along one metric axis. The chart is created over a world -- an existing coordinate system "
        "adopted as it is, or one created from a single axis -- and starts with no layers"
    ),
)
class CreateChartInput:
    """Input for creating a chart."""

    name: str = strawberry.field(description="The name of the chart")
    description: str | None = strawberry.field(default=None, description="A free-form description of what the chart shows")
    coordinate_system: strawberry.ID | None = strawberry.field(
        default=None,
        description=(
            "An existing coordinate system to adopt as the chart's world. It must have exactly one axis, of a metric type and carrying a unit. The space is never owned by "
            "the chart: several charts can share it, it survives their deletion, and while a chart is laid out over it the space cannot be deleted. Exactly one of "
            "`coordinateSystem` and `axis`"
        ),
    )
    axis: PhysicalAxisInput | None = strawberry.field(
        default=None,
        description=(
            "The one axis of a world to create for the chart: its name, its metric type (SPACE, TIME, MICROTIME or SPECTRUM) and its unit. Convenience for "
            "`createCoordinateSystem` followed by `createChart(coordinateSystem:)`; the space made is an ordinary shared one. Exactly one of `coordinateSystem` and `axis`"
        ),
    )


def create_chart(info: Info, input: CreateChartInput) -> types.Chart:
    """Create a chart over a world: an adopted existing system or one created from a single axis."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    world = get_for_org(core_models.CoordinateSystem, info, id=model.coordinate_system) if model.coordinate_system else None
    return chart_logic.create_chart(name=model.name, description=model.description, world=world, axis=model.axis, ctx=ctx)


class ChartPolicyInputModel(BaseModel):
    nchildren: int = Field(default=32, ge=0)


@kante.pydantic_input(ChartPolicyInputModel, description="How a chart is drawn from what is already placed in a space")
class ChartPolicyInput:
    """How a chart is bootstrapped from a space."""

    nchildren: int = strawberry.field(default=32, description="Draw at most this many layers")


class CreateChartFromCoordinateSystemInputModel(BaseModel):
    coordinate_system: str
    name: str | None = None
    policy: ChartPolicyInputModel = ChartPolicyInputModel()


@kante.pydantic_input(
    CreateChartFromCoordinateSystemInputModel,
    description=(
        "Bootstrap a chart over an existing coordinate system, drawing what is already laid along its axis: each array placed in it that is a trace, each table that is a "
        "series, each annotation collection. Authors no edges -- what places each layer is the registration already in the graph -- and leaves out anything placed in the "
        "space that no layer kind reads. Rerunning makes another chart over the same space"
    ),
)
class CreateChartFromCoordinateSystemInput:
    """Input for bootstrapping a chart over an existing coordinate system."""

    coordinate_system: strawberry.ID = strawberry.field(description="The coordinate system to build the chart over. It becomes the chart's world as it is, and must have exactly one metric, unit-carrying axis")
    name: str | None = strawberry.field(default=None, description="The name of the chart. Defaults to the coordinate system's name")
    policy: ChartPolicyInput = strawberry.field(default_factory=ChartPolicyInput, description="How the chart is materialized")


def create_chart_from_coordinate_system(info: Info, input: CreateChartFromCoordinateSystemInput) -> types.Chart:
    """Bootstrap a chart over an existing coordinate system and the sources laid along its axis."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    system = get_for_org(core_models.CoordinateSystem, info, id=model.coordinate_system)
    return chart_logic.bootstrap_chart_from_system(system, name=model.name, policy=chart_logic.Policy(nchildren=model.policy.nchildren), ctx=ctx)


class UpdateChartInputModel(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None


@kante.pydantic_input(UpdateChartInputModel, description="Input for renaming or re-describing a chart. Its world is not editable: a chart along another axis is another chart")
class UpdateChartInput:
    """Input for updating a chart."""

    id: strawberry.ID = strawberry.field(description="The ID of the chart to update")
    name: str | None = strawberry.field(default=None, description="A new name. Omit to leave it as it is")
    description: str | None = strawberry.field(default=None, description="A new description. Omit to leave it as it is")


def update_chart(info: Info, input: UpdateChartInput) -> types.Chart:
    """Rename or re-describe a chart."""
    parsed = input.to_pydantic()
    chart = get_for_org(models.Chart, info, id=parsed.id)

    # An omitted field means "leave it", never "clear it".
    updated: list[str] = []
    if parsed.name is not None:
        chart.name = parsed.name
        updated.append("name")
    if parsed.description is not None:
        chart.description = parsed.description
        updated.append("description")

    if updated:
        with transaction.atomic():
            chart.save(update_fields=updated)
    return chart


class DeleteChartInputModel(BaseModel):
    id: str = Field(description="The ID of the chart to delete")


@kante.pydantic_input(DeleteChartInputModel, description="Input for deleting a chart by ID. Deletes its layers and nothing they drew: the data, and the chart's world, are untouched")
class DeleteChartInput:
    """Input for deleting a chart by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the chart to delete")


delete_chart = make_delete(models.Chart, DeleteChartInput, owner=creator_owner)
