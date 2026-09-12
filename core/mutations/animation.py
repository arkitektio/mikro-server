from kante.types import Info
import strawberry
import kante
from pydantic import BaseModel, Field, field_validator
from django.db import transaction

from core import types, models, enums
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.inputs.validators import assert_not_negative
from core.scoping import get_for_org
from core.mutations._generic import make_delete, self_owner
from core.render.camera.inputs import CameraStateInput
from core.render.camera.models import CameraStateModel


class AnimationWaypointInputModel(BaseModel):
    camera: CameraStateModel = Field(description="Where the camera is at this stop")
    name: str | None = Field(default=None, description="What this stop shows")
    duration_ms: int | None = Field(default=None, description="How long the viewer takes to travel to this stop, in milliseconds")
    easing: enums.Easing | None = Field(default=None, description="How the viewer eases the camera along that travel")

    @field_validator("duration_ms")
    @classmethod
    def _travels_forwards_in_time(cls, duration_ms: int | None) -> int | None:
        """Reject a negative travel time; zero stays legal as an instant cut.

        The column is a ``PositiveIntegerField``, so a negative value is refused today by a
        Postgres check constraint -- as a 500 naming the constraint, several frames after
        the caller's mistake.
        """
        if duration_ms is not None:
            assert_not_negative(duration_ms, field="durationMs", because="it is how long the viewer travels to this stop")
        return duration_ms


@prose_errors
@kante.pydantic_input(AnimationWaypointInputModel, description="One camera pose in a tour, and how the viewer travels to it. Its position in the tour is its position in the `waypoints` list -- there is no order field to pass")
class AnimationWaypointInput:
    """One camera pose in a tour, and how the viewer travels to it."""

    camera: CameraStateInput = strawberry.field(description="Where the camera is at this stop")
    name: str | None = strawberry.field(default=None, description="What this stop shows, e.g. 'the nucleus'")
    duration_ms: int | None = strawberry.field(default=None, description="How long the viewer takes to travel TO this stop, in milliseconds, and never negative. Zero is an instant cut. Defaults to 1000. Ignored for the first stop, which is where the tour starts")
    easing: enums.Easing | None = strawberry.field(default=None, description="How the viewer eases the camera along that travel. Defaults to EASE_IN_OUT")


def _assert_the_tour_has_a_stop(waypoints: list) -> None:
    """Reject a tour with no stops.

    An empty list is written without complaint today -- the position check iterates
    nothing, the bulk create writes nothing -- and the caller gets a successfully-created
    animation that no viewer can play. Omitting `waypoints` on an update is how you leave a
    tour's stops alone, and `deleteAnimation` is how you remove it; neither of those is
    spelled with an empty list.
    """
    if not waypoints:
        raise ValueError("A tour is its stops, so `waypoints` needs at least one. Omit the field to leave a tour's stops as they are, or delete the tour with `deleteAnimation`.")


class CreateAnimationInputModel(BaseModel):
    scene: str = Field(description="The ID of the scene this tour flies through")
    name: str = Field(description="The name of the tour")
    description: str | None = Field(default=None, description="What the tour shows")
    waypoints: list[AnimationWaypointInputModel] = Field(description="The poses the viewer pans through, in tour order")

    @field_validator("waypoints")
    @classmethod
    def _has_a_stop(cls, waypoints: list) -> list:
        _assert_the_tour_has_a_stop(waypoints)
        return waypoints


@prose_errors
@kante.pydantic_input(CreateAnimationInputModel, description="Input for creating a named camera tour of a scene. The waypoints are given in tour order and that order is what is stored -- a tour is authored as a whole, never a stop at a time")
class CreateAnimationInput:
    """Input for creating a named camera tour of a scene."""

    scene: strawberry.ID = strawberry.field(description="The ID of the scene this tour flies through")
    name: str = strawberry.field(description="The name of the tour, e.g. 'overview' or 'dive to the mitochondria'")
    description: str | None = strawberry.field(default=None, description="What the tour shows")
    waypoints: list[AnimationWaypointInput] = strawberry.field(description="The poses the viewer pans through, in tour order. The list order is the tour order")


class UpdateAnimationInputModel(BaseModel):
    id: str = Field(description="The ID of the tour to update")
    name: str | None = Field(default=None, description="The name of the tour")
    description: str | None = Field(default=None, description="What the tour shows")
    waypoints: list[AnimationWaypointInputModel] | None = Field(default=None, description="The poses, in tour order. Replaces the tour's stops entirely")

    @field_validator("waypoints")
    @classmethod
    def _has_a_stop(cls, waypoints: list | None) -> list | None:
        if waypoints is not None:
            _assert_the_tour_has_a_stop(waypoints)
        return waypoints


@prose_errors
@kante.pydantic_input(UpdateAnimationInputModel, description="Input for re-authoring a camera tour. Passing `waypoints` replaces every stop -- which is also how a tour is reordered, since a stop's position in the tour is its position in this list")
class UpdateAnimationInput:
    """Input for re-authoring a camera tour."""

    id: strawberry.ID = strawberry.field(description="The ID of the tour to update")
    name: str | None = strawberry.field(default=None, description="The name of the tour. Omit to leave it as it is")
    description: str | None = strawberry.field(default=None, description="What the tour shows. Omit to leave it as it is")
    waypoints: list[AnimationWaypointInput] | None = strawberry.field(
        default=None,
        description="The poses, in tour order. Omit to leave the stops as they are; pass a list to replace every one of them. Reordering a tour is re-authoring this list",
    )


class DeleteAnimationInputModel(BaseModel):
    id: str = Field(description="The ID of the tour to delete")


@kante.pydantic_input(DeleteAnimationInputModel, description="Input for deleting a camera tour by ID")
class DeleteAnimationInput:
    """Input for deleting a camera tour by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the tour to delete")


delete_animation = make_delete(models.Animation, DeleteAnimationInput, owner=self_owner)


def _assert_positions_are_on_the_world(scene: "models.Scene", waypoints: list[AnimationWaypointInputModel]) -> None:
    """Reject a camera position naming an axis the scene's world does not have.

    A position is keyed by axis name, so a typo ('zz') is not a type error -- it would sit
    in the JSON meaning nothing until someone played the tour and the camera did not move
    the way its author meant. The world's axes are the only names that mean anything here,
    so this is checkable at authoring time and is checked.
    """
    world_axes = {axis.name for axis in scene.world.axes.all()}
    for index, waypoint in enumerate(waypoints):
        unknown = sorted(set(waypoint.camera.position) - world_axes)
        if unknown:
            raise ValueError(f"Waypoint {index} positions the camera on {unknown}, which the scene's world does not have. Its axes are {sorted(world_axes)}.")


def _write_waypoints(animation: "models.Animation", waypoints: list[AnimationWaypointInputModel]) -> None:
    """Write a tour's stops, enumerating them so `order` is the position in the authored list.

    `order` is never supplied by a caller, for the same reason `Axis.order` is not: it is
    what makes "the third stop" a well-defined statement, and a client-supplied index is
    free to collide or leave gaps. Enumeration is also what keeps the (animation, order)
    uniqueness safe without deferral -- the whole list is rewritten, so two stops are never
    swapped in place.
    """
    models.AnimationWaypoint.objects.bulk_create(
        [
            models.AnimationWaypoint(
                animation=animation,
                order=index,
                name=waypoint.name or "",
                camera=waypoint.camera.model_dump(mode="json"),
                duration_ms=waypoint.duration_ms if waypoint.duration_ms is not None else 1000,
                easing=(waypoint.easing or enums.Easing.EASE_IN_OUT).value,
            )
            for index, waypoint in enumerate(waypoints)
        ]
    )


def create_animation(info: Info, input: CreateAnimationInput) -> types.Animation:
    """Author a named camera tour of a scene."""
    parsed = input.to_pydantic()
    scene = get_for_org(models.Scene, info, id=parsed.scene)
    _assert_positions_are_on_the_world(scene, parsed.waypoints)

    ctx = CreationContext.from_info(info)
    with transaction.atomic():
        animation = models.Animation.objects.create(
            scene=scene,
            name=parsed.name,
            description=parsed.description,
            creator=ctx.user,
            organization=ctx.organization,
            **ctx.provenance_kwargs(),
        )
        _write_waypoints(animation, parsed.waypoints)
    return animation


def update_animation(info: Info, input: UpdateAnimationInput) -> types.Animation:
    """Re-author a camera tour: rename it, or replace its stops.

    Passing `waypoints` replaces every stop rather than merging: a tour is a sequence, and
    the only coherent edits to one are "these are the stops now". That is also why there is
    no reorder mutation and no per-stop mutation -- reordering is re-authoring the list,
    and it re-enumerates `order` from zero.
    """
    parsed = input.to_pydantic()
    animation = get_for_org(models.Animation, info, id=parsed.id)

    if parsed.waypoints is not None:
        _assert_positions_are_on_the_world(animation.scene, parsed.waypoints)

    updated: list[str] = []
    if parsed.name is not None:
        animation.name = parsed.name
        updated.append("name")
    if parsed.description is not None:
        animation.description = parsed.description
        updated.append("description")

    with transaction.atomic():
        if updated:
            animation.save(update_fields=updated)
        if parsed.waypoints is not None:
            animation.waypoints.all().delete()
            _write_waypoints(animation, parsed.waypoints)
    return animation
