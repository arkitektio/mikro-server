"""Round-trip tests for the Pint-backed quantity scalars."""

import pytest

from kanne_server.scalars import (
    SCALAR_MAP,
    Duration,
    ElectricalConductance,
    ElectricCurrent,
    ElectricPotential,
    Frequency,
    Length,
    Temperature,
)


def _parse(cls: object, raw: object) -> object:
    parse_value = SCALAR_MAP[cls].parse_value
    assert parse_value is not None
    return parse_value(raw)


def _serialize(cls: object, value: object) -> object:
    serialize = SCALAR_MAP[cls].serialize
    assert serialize is not None
    return serialize(value)


def test_duration_normalizes_to_picoseconds() -> None:
    # canonical = round(value_in_seconds * 1e12) -> picoseconds
    assert _parse(Duration, "5 ms") == 5_000_000_000
    assert _parse(Duration, "25 us") == 25_000_000
    assert _parse(Duration, "1 ns") == 1_000
    assert _parse(Duration, "1 ps") == 1


def test_electric_potential_normalizes_to_femtovolts() -> None:
    assert _parse(ElectricPotential, "-65 mV") == -65_000_000_000_000
    assert _parse(ElectricPotential, "1 uV") == 1_000_000_000


def test_electric_current_preserves_picoamps() -> None:
    # the whole point: pico-scale currents must survive (femtoamp canonical)
    assert _parse(ElectricCurrent, "5 pA") == 5_000
    assert _parse(ElectricCurrent, "1 fA") == 1


def test_length_normalizes_to_picometers() -> None:
    assert _parse(Length, "1.5 um") == 1_500_000
    assert _parse(Length, "1 nm") == 1_000


def test_frequency_normalizes_to_nanohertz() -> None:
    assert _parse(Frequency, "20 kHz") == 20_000_000_000_000
    assert _parse(Frequency, "1 Hz") == 1_000_000_000


def test_conductance_preserves_picosiemens() -> None:
    assert _parse(ElectricalConductance, "0.5 uS") == 500_000_000
    assert _parse(ElectricalConductance, "1 pS") == 1_000


def test_temperature_converts_offset_units() -> None:
    # 37 degC == 310.15 K -> nanokelvin (default nano scale)
    assert _parse(Temperature, "37 degC") == 310_150_000_000


def test_serialize_returns_compact_quantity() -> None:
    # Rescaled to the SI prefix with the fewest zero digits, no trailing ".0".
    assert _serialize(Duration, 5_000_000_000) == "5 ms"
    assert _serialize(ElectricPotential, -65_000_000_000_000) == "-65 mV"
    assert _serialize(Frequency, 1_000_000_000) == "1 Hz"
    # whole numbers drop the ".0", zero stays in the reference unit
    assert _serialize(Duration, 1_000_000_000_000) == "1 s"
    assert _serialize(Duration, 0) == "0 s"


def test_dimensional_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        _parse(Duration, "5 V")
    with pytest.raises(ValueError):
        _parse(ElectricPotential, "5 ms")


def test_dimensionless_input_raises() -> None:
    with pytest.raises(ValueError):
        _parse(Duration, "5")
