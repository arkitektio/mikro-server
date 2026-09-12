"""GraphQL input types for a recorded microscope state, mirrored off the pydantic models."""

import strawberry
from strawberry.experimental import pydantic

from kanne_server import scalars as kanne_scalars
from optikit import models


@pydantic.input(models.StageStateModel, description="Where the stage was, per axis, as physical lengths (e.g. '100.5 um')")
class StageStateInput:
    x: kanne_scalars.Length | None = strawberry.field(default=None, description="The stage x position, e.g. '100.5 um'")
    y: kanne_scalars.Length | None = strawberry.field(default=None, description="The stage y position")
    z: kanne_scalars.Length | None = strawberry.field(default=None, description="The stage z (focus) position")


@pydantic.input(models.SettingModel, description="One named device setting with exactly one value slot filled: a quantity when the setting carries a unit, else a number, text or flag. A setting holding two values is two settings")
class SettingInput:
    name: str = strawberry.field(description="The setting's name on the device, e.g. 'power', 'position', 'gain'")
    quantity: kanne_scalars.GenericQuantity | None = strawberry.field(default=None, description="The value as a unit-carrying quantity, e.g. '20 mW', '488 nm'")
    number: float | None = strawberry.field(default=None, description="The value as a bare number, for a unitless setting")
    text: str | None = strawberry.field(default=None, description="The value as text, e.g. a named position 'GFP'")
    flag: bool | None = strawberry.field(default=None, description="The value as a flag, e.g. shutter open")


@pydantic.input(models.DeviceStateModel, description="One hardware device's recorded state: its identity in the setup plus its settings at this coordinate")
class DeviceStateInput:
    label: str = strawberry.field(description="The device's identity in the setup, e.g. 'filter-wheel-1'")
    kind: str | None = strawberry.field(default=None, description="A free-form device kind, e.g. 'laser', 'filter-wheel', 'detector'")
    settings: list[SettingInput] = strawberry.field(default_factory=list, description="The device's named settings, one value slot each")


@pydantic.input(models.OptikitStateModel, description="The recorded microscope (Optikit) state: the hardware truth at the moment of acquisition. The common facts (stage, environment) are first-class and quantity-typed; everything else is per-device named settings")
class OptikitStateInput:
    stage: StageStateInput | None = strawberry.field(default=None, description="Where the stage was")
    temperature: kanne_scalars.Temperature | None = strawberry.field(default=None, description="The environment temperature, e.g. '37 degC'")
    devices: list[DeviceStateInput] = strawberry.field(default_factory=list, description="The recorded per-device states")
