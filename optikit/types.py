"""GraphQL output types for a recorded microscope state, mirrored off the same pydantic models."""

import strawberry
from strawberry.experimental import pydantic

from kanne_server import scalars as kanne_scalars
from optikit import models


@pydantic.type(models.StageStateModel, description="Where the stage was, per axis, as physical lengths")
class StageState:
    x: kanne_scalars.Length | None = strawberry.field(default=None, description="The stage x position")
    y: kanne_scalars.Length | None = strawberry.field(default=None, description="The stage y position")
    z: kanne_scalars.Length | None = strawberry.field(default=None, description="The stage z (focus) position")


@pydantic.type(models.SettingModel, description="One named device setting, exactly one value slot filled")
class Setting:
    name: str = strawberry.field(description="The setting's name on the device")
    quantity: kanne_scalars.GenericQuantity | None = strawberry.field(default=None, description="The value as a unit-carrying quantity, when the setting has one")
    number: float | None = strawberry.field(default=None, description="The value as a bare number")
    text: str | None = strawberry.field(default=None, description="The value as text")
    flag: bool | None = strawberry.field(default=None, description="The value as a flag")


@pydantic.type(models.DeviceStateModel, description="One hardware device's recorded state")
class DeviceState:
    label: str = strawberry.field(description="The device's identity in the setup")
    kind: str | None = strawberry.field(default=None, description="A free-form device kind")
    settings: list[Setting] = strawberry.field(default_factory=list, description="The device's named settings")


@pydantic.type(models.OptikitStateModel, description="The recorded microscope (Optikit) state: the hardware truth at the moment of acquisition")
class OptikitStateGraph:
    stage: StageState | None = strawberry.field(default=None, description="Where the stage was")
    temperature: kanne_scalars.Temperature | None = strawberry.field(default=None, description="The environment temperature")
    devices: list[DeviceState] = strawberry.field(default_factory=list, description="The recorded per-device states")
