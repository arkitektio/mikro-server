"""Pydantic field types that persist a quantity as a searchable dual struct.

Where :mod:`kanne_server.scalars` defines the GraphQL *wire* scalars (string in, string
out), these are the *storage* types used on the pydantic models that get dumped
into JSON blobs (e.g. ``NeuronModel.json_model``). Each is an ``Annotated[int, …]``
so that **in memory the value is a plain canonical integer** — defaults, hashing,
the strawberry bridge and the typed ``QuantityField`` columns all keep working
unchanged — while **JSON serialization expands it** to::

    { "canonical": 100000000, "given": "100 µs", "unit": "picosecond" }

- ``canonical`` — exact integer in the canonical sub-unit; numerically searchable
  (Postgres JSONB) and dedup-stable.
- ``given`` — the compact human-readable string; self-describing, unit-bearing.
- ``unit`` — the canonical sub-unit, so ``canonical`` is decodable with no external
  docs (durability: survives even if Pint can no longer parse ``given``).

The ``given`` string is *derived* from ``canonical`` at serialization time (it is
the same compact form the wire scalar emits), not the user's verbatim spelling —
preserving the exact entered unit would require the wire scalar to stop coercing
to an int, rippling through every quantity field in the codebase. See
``kanne/DESIGN.md``.

Validation is tolerant inbound: a value may arrive as the canonical ``int`` (the
GraphQL-input path), the ``{canonical, …}`` dict (JSON read-back), a Pint string,
or — for backwards compatibility — a bare legacy int. All collapse to the
canonical int in memory.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

from . import scalars as _scalars


def _markers(pint_cls: type[_scalars.PintQuantity]) -> tuple[BeforeValidator, PlainSerializer]:
    """Build the (validator, serializer) pair for one quantity dimension."""
    reference_unit = pint_cls.reference_unit
    scale = pint_cls.scale
    base_unit = _scalars.canonical_base_unit(reference_unit, scale)
    parse = _scalars.SCALAR_MAP[pint_cls].parse_value
    assert parse is not None  # every kanne scalar defines parse_value

    def validate(value: Any) -> Any:
        # Tolerant inbound coercion → canonical int (or None for optionals).
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"{pint_cls.__name__}: bool is not a quantity")
        if isinstance(value, dict):  # JSON read-back: trust the stored canonical
            return int(value["canonical"])
        if isinstance(value, int):  # canonical int (GraphQL-input path / legacy blob)
            return value
        # Pint strings ("100 µs") and pint Quantities: parse + dimension-check.
        return parse(value)

    def serialize(value: Any) -> Any:
        if value is None:
            return None
        canonical = int(value)
        return {
            "canonical": canonical,
            "given": _scalars.format_quantity(canonical, reference_unit, scale),
            "unit": base_unit,
        }

    return BeforeValidator(validate), PlainSerializer(serialize, return_type=dict, when_used="json")


# One storage type per dimension used in JSON config blobs. Assembled as literal
# ``Annotated[...]`` aliases (not a factory call) so static type checkers accept
# them in field annotations.
_duration = _markers(_scalars.Duration)
Duration = Annotated[int, _duration[0], _duration[1]]

_length = _markers(_scalars.Length)
Length = Annotated[int, _length[0], _length[1]]

_power = _markers(_scalars.Power)
Power = Annotated[int, _power[0], _power[1]]

_frequency = _markers(_scalars.Frequency)
Frequency = Annotated[int, _frequency[0], _frequency[1]]

_potential = _markers(_scalars.ElectricPotential)
ElectricPotential = Annotated[int, _potential[0], _potential[1]]

_concentration = _markers(_scalars.Concentration)
Concentration = Annotated[int, _concentration[0], _concentration[1]]

_conductance = _markers(_scalars.ElectricalConductance)
ElectricalConductance = Annotated[int, _conductance[0], _conductance[1]]

_temperature = _markers(_scalars.Temperature)
Temperature = Annotated[int, _temperature[0], _temperature[1]]

_resistivity = _markers(_scalars.Resistivity)
Resistivity = Annotated[int, _resistivity[0], _resistivity[1]]

_specific_capacitance = _markers(_scalars.SpecificCapacitance)
SpecificCapacitance = Annotated[int, _specific_capacitance[0], _specific_capacitance[1]]


def _generic_markers() -> tuple[BeforeValidator, PlainSerializer]:
    """Storage (validator, serializer) for a dimension-agnostic quantity.

    In memory the value is a normalized, re-parseable string
    (``"0.12 siemens / centimeter ** 2"``); JSON serialization expands it to
    ``{magnitude, unit, dimension}`` for durability and search. Inbound it
    accepts that string, a pint string/Quantity, or the ``{magnitude, unit}``
    dict on read-back. A bare number is rejected (a value must carry a unit).
    """

    def validate(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):  # JSON read-back
            return f'{value["magnitude"]} {value["unit"]}'
        return _scalars.generic_quantity_string(value)

    def serialize(value: Any) -> Any:
        if value is None:
            return None
        return _scalars.generic_quantity_struct(value)

    return BeforeValidator(validate), PlainSerializer(serialize, return_type=dict, when_used="json")


_generic = _generic_markers()
GenericQuantity = Annotated[str, _generic[0], _generic[1]]


def _validated_string(parse: Any) -> BeforeValidator:
    """A tolerant BeforeValidator that runs ``parse`` (or passes None through).

    Used for the metadata string types (``Unit``, ``Dimension``) — the value stays
    a plain string in memory and in JSON; ``parse`` validates/canonicalizes it.
    """

    def validate(value: Any) -> Any:
        if value is None:
            return None
        return parse(value)

    return BeforeValidator(validate)


#: A validated pint unit string (e.g. ``"mV"``, ``"S/cm2"``). See kanne_server.scalars.Unit.
Unit = Annotated[str, _validated_string(_scalars.parse_unit)]

#: A validated, canonicalized pint dimensionality (e.g. ``"[length]"``).
#: See kanne_server.scalars.Dimension.
Dimension = Annotated[str, _validated_string(_scalars.parse_dimension)]
