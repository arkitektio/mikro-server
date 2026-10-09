"""The one mutation that makes either composition."""

import kante
import strawberry
from kante.types import Info
from pydantic import BaseModel

from core import models, types
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.scoping import get_for_org
from visualization import logic


class AutoVisualizeInputModel(BaseModel):
    coordinate_system: str
    name: str | None = None


@prose_errors
@kante.pydantic_input(
    AutoVisualizeInputModel,
    description=(
        "Show a coordinate system without choosing how. A space with exactly one metric, unit-carrying axis becomes a chart; any other space with a time or space axis "
        "becomes a scene. Either is bootstrapped from what already resides in the space, with that kind's default policy, and authors no edges. For a say in the kind or "
        "the policy, use `createChartFromCoordinateSystem` or `createSceneFromCoordinateSystem`"
    ),
)
class AutoVisualizeInput:
    """Input for showing a coordinate system as whichever composition fits it."""

    coordinate_system: strawberry.ID = strawberry.field(description="The coordinate system to show. It becomes the world of the scene or chart as it is")
    name: str | None = strawberry.field(default=None, description="The name of the scene or chart. Defaults to the coordinate system's name")


def auto_visualize(info: Info, input: AutoVisualizeInput) -> types.Visualization:
    """Bootstrap a chart or a scene over an existing coordinate system, whichever its axes call for."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    system = get_for_org(models.CoordinateSystem, info, id=model.coordinate_system)
    return logic.auto_visualize(system, name=model.name, ctx=ctx)
