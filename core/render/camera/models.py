"""Pydantic models of a camera pose in a scene.

One model family serves input and output alike: a pose is a *snapshot* of where
the eye was, so nothing is generated server-side and the structure a client
sends is exactly the structure every reader gets back -- the same contract the
Optikit state keeps.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from core.inputs.validators import assert_positive

#: A quaternion has four components. Named rather than inlined so the two
#: orientation checks below cannot drift apart.
_QUATERNION_LENGTH = 4


class CameraStateModel(BaseModel):
    """Where the camera is in a scene, and how it is looking at it.

    Two views of one position, neuroglancer-style: a flat cross-section and a
    volumetric projection, each with its own orientation and scale. A pose carries
    both, so one waypoint serves a 2D and a 3D viewer alike and
    ``Scene.preferredView`` decides which is used -- rather than a tour having to be
    authored twice, once per view.

    Every number is read against the scene's world coordinate system. The units are
    on that system's axes and are deliberately not repeated here.
    """

    position: dict[str, float] = Field(
        description=(
            "Where the camera is centred, keyed by the world's axis names. Keyed rather than a "
            "positional list because the world's axes are named and a tour through a timelapse "
            "moves in t as much as in z -- a list would silently depend on axis order. Axes the "
            "pose does not name are left wherever the viewer already had them."
        )
    )
    cross_section_orientation: list[float] | None = Field(
        default=None,
        description="The flat view's orientation, as a quaternion. Null to leave it to the viewer.",
    )
    cross_section_scale: float | None = Field(
        default=None,
        description="The flat view's zoom, in world units per screen pixel. Null to leave it to the viewer.",
    )
    projection_orientation: list[float] | None = Field(
        default=None,
        description="The volumetric view's orientation, as a quaternion. Null to leave it to the viewer.",
    )
    projection_scale: float | None = Field(
        default=None,
        description="The volumetric view's zoom, in world units per screen pixel. Null to leave it to the viewer.",
    )

    @model_validator(mode="after")
    def _orientations_are_quaternions(self) -> "CameraStateModel":
        """Reject an orientation that is not a quaternion.

        A three-component orientation is the plausible mistake here -- it reads like an
        euler triple and would be accepted silently by a bare ``list[float]``, then be
        meaningless to every viewer that unpacked it as x/y/z/w.
        """
        for field, value in (("crossSectionOrientation", self.cross_section_orientation), ("projectionOrientation", self.projection_orientation)):
            if value is not None and len(value) != _QUATERNION_LENGTH:
                raise ValueError(f"{field} is a quaternion, so it takes exactly {_QUATERNION_LENGTH} components (x, y, z, w), but got {len(value)}.")
        return self

    @model_validator(mode="after")
    def _zooms_are_positive(self) -> "CameraStateModel":
        """Reject a zoom of zero or less.

        A scale is world units *per screen pixel*, so it is a ratio a viewer divides by:
        zero has no view to describe, and a negative one describes a mirrored screen
        rather than a camera. Null is how a pose declines to set the zoom at all.
        """
        for field, value in (("crossSectionScale", self.cross_section_scale), ("projectionScale", self.projection_scale)):
            if value is not None:
                assert_positive(value, field=field, because="it is world units per screen pixel, which a viewer divides by")
        return self
