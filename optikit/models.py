"""Pydantic models of a recorded microscope state.

One model family serves input and output alike: a state is a *snapshot*, so
unlike the lightpath graph nothing is generated server-side and the structure a
client sends is exactly the structure every reader gets back.

The shape is deliberately two-layered. The fields every microscope has -- a
stage pose, an environment -- are first-class and quantity-typed, so a client
can ask "where was the stage" without knowing whose hardware recorded it. What
is left is honest heterogeneity: hardware exposes arbitrary named settings, so
devices carry a list of :class:`SettingModel`, each holding exactly one value
slot (a quantity when the setting has a unit, a number/text/flag when it does
not). That keeps the state composable without inventing a hardware ontology a
real Optikit would immediately outgrow.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from kanne_server import quantities


class StageStateModel(BaseModel):
    """Where the stage was, per axis, as physical lengths."""

    x: quantities.Length | None = None
    y: quantities.Length | None = None
    z: quantities.Length | None = None


class SettingModel(BaseModel):
    """One named device setting, with exactly one value slot filled.

    A quantity when the setting carries a unit ('20 mW', '37 degC'), a bare
    number, a text, or a flag when it does not. One slot, enforced: a setting
    holding two values is two settings.
    """

    name: str
    quantity: quantities.GenericQuantity | None = None
    number: float | None = None
    text: str | None = None
    flag: bool | None = None

    @model_validator(mode="after")
    def _one_value_slot(self) -> "SettingModel":
        filled = [slot for slot in (self.quantity, self.number, self.text, self.flag) if slot is not None]
        if len(filled) > 1:
            raise ValueError(f"Setting {self.name!r} fills more than one value slot; a setting holds exactly one value, so record two settings instead.")
        return self


class DeviceStateModel(BaseModel):
    """One hardware device's recorded state: identity plus its settings."""

    label: str = Field(description="The device's identity in the setup, e.g. 'filter-wheel-1'")
    kind: str | None = Field(None, description="A free-form device kind, e.g. 'laser', 'filter-wheel'")
    settings: list[SettingModel] = Field(default_factory=list)


class OptikitStateModel(BaseModel):
    """The recorded microscope state pinned to a coordinate anchor."""

    stage: StageStateModel | None = None
    temperature: quantities.Temperature | None = None
    devices: list[DeviceStateModel] = Field(default_factory=list)
