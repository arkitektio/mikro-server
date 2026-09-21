"""A recorded setting says exactly one thing, and a recorded device says at least one.

The docstring and the SDL always said "exactly one value slot"; the validator enforced "at
most one", so a setting with nothing in it was accepted, and so was a device with no settings
-- a microscope asserted to have been involved and then nothing said about it. The Python
client guarded both; the rules are the server's, so they are enforced here and the client's
copy is gone.
"""

import pytest
from pydantic import ValidationError

from optikit.models import DeviceStateModel, OptikitStateModel, SettingModel


def test_one_filled_slot_is_a_setting() -> None:
    """Each of the four slots alone is a setting."""
    assert SettingModel(name="power", number=20.0).number == 20.0
    assert SettingModel(name="position", text="GFP").text == "GFP"
    assert SettingModel(name="shutter", flag=True).flag is True


def test_two_filled_slots_are_two_settings() -> None:
    """A setting holding two values is two settings, and is refused."""
    with pytest.raises(ValidationError, match="more than one value slot"):
        SettingModel(name="power", number=20.0, text="20")


def test_an_empty_setting_is_refused() -> None:
    """Exactly one slot, not at most one: a setting with nothing to say is refused."""
    with pytest.raises(ValidationError, match="fills no value slot"):
        SettingModel(name="power")


def test_a_device_with_no_settings_is_refused() -> None:
    """A device asserted to be involved and then silent about is left out instead."""
    with pytest.raises(ValidationError, match="records no settings"):
        DeviceStateModel(label="laser-488", kind="laser")


def test_a_state_with_no_devices_is_still_a_state() -> None:
    """Stage and environment are first-class; a state can be only those."""
    state = OptikitStateModel(stage={"x": "1 um"}, devices=[])
    assert state.devices == []
