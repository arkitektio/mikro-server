"""The one mutation that makes either composition."""

import kante
import strawberry
from kante.types import Info
from django.db import transaction
from pydantic import BaseModel, model_validator

from core import models, types
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.mutations import lens as lens_mutations
from core.scoping import get_for_org
from visualization import logic


class AutoVisualizeInputModel(BaseModel):
    coordinate_system: str | None = None
    lens: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _names_one_thing(self) -> "AutoVisualizeInputModel":
        if (self.coordinate_system is None) == (self.lens is None):
            raise ValueError("Name exactly one of `coordinateSystem` (show what resides in a space) or `lens` (show one selection, and nothing else of its container).")
        return self


@prose_errors
@kante.pydantic_input(
    AutoVisualizeInputModel,
    description=(
        "Show a coordinate system, or one lens, without choosing how. A space with exactly one metric, unit-carrying axis becomes a chart; any other space with a time or space axis "
        "becomes a scene. Over a `coordinateSystem`, either is bootstrapped from what already resides in the space; over a `lens`, it shows that lens' selection and nothing else of "
        "its container, the choice read off the space the selection lives in. Both use that kind's default policy and register nothing into any space. Provide exactly one of the two. For a say in "
        "the kind or the policy, use `createChartFromCoordinateSystem`, `createSceneFromCoordinateSystem` or `createSceneFromLens`"
    ),
)
class AutoVisualizeInput:
    """Input for showing a coordinate system, or one lens, as whichever composition fits it."""

    coordinate_system: strawberry.ID | None = strawberry.field(default=None, description="The coordinate system to show. It becomes the world of the scene or chart as it is. Provide exactly one of this and `lens`")
    lens: strawberry.ID | None = strawberry.field(
        default=None,
        description=(
            "The lens to show, of any kind: a crop or a whole array, a window over a table, a mesh, a network or an annotation collection. This is how a selection is shown -- showing a "
            "sliced lens' *space* draws its whole container again. A scene is the one `createSceneFromLens` makes with its defaults, nomination included; a chart is laid along the "
            "lens' own space with the lens as its one layer. Provide exactly one of this and `coordinateSystem`"
        ),
    )
    name: str | None = strawberry.field(default=None, description="The name of the scene or chart. Defaults to the coordinate system's name, or to the lens' container's label with the lens' name when it has one")


def auto_visualize(info: Info, input: AutoVisualizeInput) -> types.Visualization:
    """Bootstrap a chart or a scene over an existing coordinate system or of one lens, whichever its axes call for."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    if model.lens is None:
        system = get_for_org(models.CoordinateSystem, info, id=model.coordinate_system)
        return logic.auto_visualize(system, name=model.name, ctx=ctx)

    lens = get_for_org(models.Lens, info, id=model.lens)
    with transaction.atomic():
        shown = logic.auto_visualize_lens(lens, name=model.name, ctx=ctx)
        # What `createSceneFromLens` does by default: the scene becomes the one the lens opens
        # on when it nominates nothing yet, skipped silently for a caller who may not write it.
        if isinstance(shown, models.Scene) and lens.default_scene_id is None:
            try:
                lens_mutations.nominate_lens_default_scene(info, lens, shown)
            except PermissionError:
                pass
    return shown
