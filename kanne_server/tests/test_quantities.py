"""Tests for the dual-struct pydantic storage types (kanne_server.quantities).

These persist a quantity as ``{canonical, given, unit}`` in JSON while keeping a
plain canonical ``int`` in memory. See ``kanne/DESIGN.md``.
"""

from typing import Optional

import pytest
from pydantic import BaseModel, ValidationError

from kanne_server import quantities as pq


class Sample(BaseModel):
    dur: pq.Duration = 100_000_000_000          # 100 ms (canonical picoseconds)
    v: pq.ElectricPotential = -67_000_000_000_000
    temp: pq.Temperature = 309_150_000_000
    length: Optional[pq.Length] = None


def test_in_memory_value_is_canonical_int():
    # Defaults and parsed values are plain ints in memory (no validate_default needed).
    m = Sample()
    assert m.dur == 100_000_000_000
    assert isinstance(m.dur, int)
    # python-mode dump stays an int; only json mode expands to the struct.
    assert m.model_dump()["dur"] == 100_000_000_000


def test_json_dump_expands_to_struct():
    m = Sample()
    blob = m.model_dump(mode="json")
    assert blob["dur"] == {"canonical": 100_000_000_000, "given": "100 ms", "unit": "picosecond"}
    assert blob["v"]["given"] == "-67 mV"
    assert blob["temp"] == {"canonical": 309_150_000_000, "given": "309.15 K", "unit": "nanokelvin"}
    assert blob["length"] is None


def test_parses_pint_strings():
    m = Sample.model_validate({"dur": "5 ms", "v": "-65 mV", "temp": "37 degC", "length": "100 µm"})
    blob = m.model_dump(mode="json")
    assert blob["dur"]["canonical"] == 5_000_000_000
    assert blob["v"]["canonical"] == -65_000_000_000_000
    assert blob["temp"]["canonical"] == 310_150_000_000
    assert blob["length"] == {"canonical": 100_000_000, "given": "100 µm", "unit": "picometer"}


def test_reload_from_struct_round_trips():
    blob = Sample.model_validate({"dur": "5 ms", "length": "100 µm"}).model_dump(mode="json")
    again = Sample(**blob)
    assert again.dur == 5_000_000_000
    assert again.length == 100_000_000
    assert again.model_dump(mode="json") == blob


def test_reads_legacy_bare_int_blobs():
    # Rows written before the dual struct stored bare canonical ints.
    legacy = Sample(dur=5_000_000_000, length=100_000_000)
    assert legacy.dur == 5_000_000_000
    assert legacy.length == 100_000_000
    # ...and upgrade to the struct on the next dump.
    assert legacy.model_dump(mode="json")["dur"]["given"] == "5 ms"


def test_value_equality_is_unit_independent():
    # "1 ms" and "1000 µs" are the same physical value -> same canonical int
    # (this is what keeps NeuronModel dedup-by-hash correct).
    a = Sample.model_validate({"dur": "1 ms"})
    b = Sample.model_validate({"dur": "1000 µs"})
    assert a.dur == b.dur


def test_dimensional_mismatch_rejected():
    with pytest.raises(ValidationError):
        Sample.model_validate({"dur": "5 mV"})  # volts into a Duration field
