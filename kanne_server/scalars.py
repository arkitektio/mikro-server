"""Dimension-typed GraphQL scalars backed by Pint.

Every quantity is declared as a :class:`PintQuantity` subclass carrying a single
``reference_unit`` (its SI reference, e.g. ``"second"``, ``"volt"``). On the wire
a quantity is a Pint string in any dimensionally-compatible unit (``"5 ms"``,
``"-70 mV"``, ``"1 kHz"``). Internally it is normalized to an **integer count of
a per-quantity canonical sub-unit** (``round(value_in_reference * scale)``),
chosen small enough for electrophysiology — picoseconds, femtovolts,
femtoamps, attofarads, .... This is exact (no float drift) and, as a Python
``int`` / BigInteger, lossless across any realistic range. On the way out it
serializes to a compact Pint string, rescaled to the SI prefix with the fewest
leading/trailing zeros (``"5 ms"``, ``"-65 mV"``, ``"1 Hz"``).

The subclasses double as field annotations; each registers its GraphQL scalar
into :data:`SCALAR_MAP`, which is merged into the schema's
``StrawberryConfig.scalar_map`` in ``elektro_server/schema.py``.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, ClassVar

import pint
import strawberry
from strawberry.types.scalar import ScalarDefinition

from .registry import get_registry

#: Maps each PintQuantity subclass to its strawberry scalar definition.
SCALAR_MAP: dict[object, ScalarDefinition] = {}


def _build_scalar(cls: type["PintQuantity"]) -> ScalarDefinition:
    reference_unit = cls.reference_unit
    scale = cls.scale
    name = cls.__name__

    def parse_value(raw: object) -> int:
        ureg = get_registry()
        try:
            quantity = ureg.Quantity(raw)
        except Exception as exc:  # pragma: no cover - pint raises various types
            raise ValueError(f"{name}: cannot parse quantity {raw!r}: {exc}") from exc
        try:
            magnitude = quantity.to(reference_unit).magnitude
        except pint.DimensionalityError as exc:
            raise ValueError(
                f"{name} expects a quantity compatible with {reference_unit!r}: {exc}"
            ) from exc
        return int(round(magnitude * scale))

    def serialize(value: object) -> str:
        if hasattr(value, "to"):  # already a pint Quantity
            quantity = value.to(reference_unit).to_compact()
            return f"{quantity:~g}"
        # an int expressed in the canonical sub-unit (reference / scale)
        return format_quantity(value, reference_unit, scale)

    return strawberry.scalar(
        name=name,
        description=inspect.getdoc(cls) or "",
        serialize=serialize,
        parse_value=parse_value,
    )


class PintQuantity:
    """Base class for Pint-backed quantity scalars.

    Subclasses declare a :attr:`reference_unit` and a :attr:`scale`. The scalar
    normalizes input to ``round(value_in_reference_unit * scale)`` — an integer
    count of ``reference_unit / scale``, i.e. the canonical sub-unit — and
    serializes back to ``reference_unit``.

    ``scale`` is chosen per quantity so the canonical sub-unit is small enough to
    losslessly capture the smallest values seen in electrophysiology (femtoamps,
    attofarads, picoseconds, ...). The default is nano (``1e9``).
    """

    #: SI reference unit for this quantity (e.g. ``"second"``).
    reference_unit: ClassVar[str]
    #: Integer canonical = round(value_in_reference * scale). Larger => finer.
    scale: ClassVar[int] = 1_000_000_000  # nano

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "reference_unit", None) is None:
            raise TypeError(f"{cls.__name__} must define a reference_unit")
        SCALAR_MAP[cls] = _build_scalar(cls)


# Common scale factors, named by the SI sub-prefix they produce.
_MICRO = 1_000_000
_NANO = 1_000_000_000
_PICO = 1_000_000_000_000
_FEMTO = 1_000_000_000_000_000
_ATTO = 1_000_000_000_000_000_000

#: SI prefix that a given scale produces (inverse of the sub-unit size).
_SCALE_PREFIX: dict[int, str] = {
    1: "",
    _MICRO: "micro",
    _NANO: "nano",
    _PICO: "pico",
    _FEMTO: "femto",
    _ATTO: "atto",
}


def format_quantity(value: Any, reference_unit: str, scale: int) -> str:
    """Render an integer canonical sub-unit count as a compact Pint string.

    ``value`` is an integer count of ``reference_unit / scale`` (e.g. picoseconds
    for seconds at pico scale). For simple units the result is rescaled to the SI
    prefix with the fewest zero digits (``5_000_000_000`` seconds-at-pico →
    ``"5 ms"``). Compound reference units (``ohm * centimeter``,
    ``microfarad / centimeter ** 2``) are left in their reference unit rather than
    ``to_compact``-ed, which would otherwise mangle them into rescaled SI base
    forms like ``"354 m * mΩ"``.
    """
    ureg = get_registry()
    quantity = (value / scale) * ureg(reference_unit)
    if not any(ch in reference_unit for ch in " */"):
        quantity = quantity.to_compact()
    return f"{quantity:~g}"


def canonical_base_unit(reference_unit: str, scale: int) -> str:
    """Name of the canonical sub-unit an integer is counted in (e.g. ``"picosecond"``).

    Falls back to a descriptive ``"<reference_unit> / <scale>"`` form for compound
    reference units or scales without a named SI prefix, so the value stays
    self-describing in every case.
    """
    prefix = _SCALE_PREFIX.get(scale)
    if prefix is None or any(ch in reference_unit for ch in " */"):
        return f"{reference_unit} / {scale}"
    return f"{prefix}{reference_unit}"


class Duration(PintQuantity):
    """A quantity of time (``"5 ms"``, ``"2 s"``, ``"1 hour"``)."""

    reference_unit = "second"
    scale = _PICO


class Frequency(PintQuantity):
    """A quantity of frequency (``"50 Hz"``, ``"1 kHz"``)."""

    reference_unit = "hertz"


class Length(PintQuantity):
    """A spatial length (``"2.5 µm"``, ``"1 mm"``, ``"3 m"``)."""

    reference_unit = "meter"
    scale = _PICO


class Area(PintQuantity):
    """An area (``"4 µm ** 2"``, ``"2 mm^2"``)."""

    reference_unit = "meter ** 2"


class Volume(PintQuantity):
    """A volume (``"5 µL"``, ``"2 mL"``)."""

    reference_unit = "liter"


class Velocity(PintQuantity):
    """A velocity / speed (``"3 µm/s"``, ``"2 m/s"``)."""

    reference_unit = "meter / second"


class Mass(PintQuantity):
    """A mass (``"5 mg"``, ``"2 kg"``)."""

    reference_unit = "gram"


class Temperature(PintQuantity):
    """A temperature (``"310 K"``, ``"37 degC"``)."""

    reference_unit = "kelvin"


class AmountOfSubstance(PintQuantity):
    """An amount of substance (``"5 mmol"``, ``"2 mol"``)."""

    reference_unit = "mole"


class Concentration(PintQuantity):
    """A molar concentration (``"5 nM"``, ``"2 µM"``, ``"1 mM"``)."""

    reference_unit = "molar"
    scale = _PICO


class ElectricCurrent(PintQuantity):
    """An electric current (``"5 pA"``, ``"2 nA"``)."""

    reference_unit = "ampere"
    scale = _FEMTO


class ElectricPotential(PintQuantity):
    """An electric potential / voltage (``"-70 mV"``, ``"5 V"``)."""

    reference_unit = "volt"
    scale = _FEMTO


class ElectricCharge(PintQuantity):
    """An electric charge (``"5 pC"``, ``"2 nC"``)."""

    reference_unit = "coulomb"
    scale = _FEMTO


class Capacitance(PintQuantity):
    """A capacitance (``"5 pF"``, ``"100 nF"``)."""

    reference_unit = "farad"
    scale = _ATTO


class ElectricalConductance(PintQuantity):
    """An electrical conductance (``"5 nS"``, ``"2 µS"``)."""

    reference_unit = "siemens"
    scale = _FEMTO


class ElectricalResistance(PintQuantity):
    """An electrical resistance (``"100 MΩ"``, ``"5 GΩ"``)."""

    reference_unit = "ohm"
    scale = _MICRO


class Resistivity(PintQuantity):
    """An axial resistivity (NEURON ``Ra``; ``"35.4 ohm*cm"``, ``"100 ohm*cm"``)."""

    reference_unit = "ohm * centimeter"


class SpecificCapacitance(PintQuantity):
    """A specific membrane capacitance (NEURON ``cm``; ``"1 uF/cm^2"``)."""

    reference_unit = "microfarad / centimeter ** 2"


class Power(PintQuantity):
    """A power (``"5 mW"``, ``"2 W"``)."""

    reference_unit = "watt"


class Energy(PintQuantity):
    """An energy (``"5 mJ"``, ``"2 J"``)."""

    reference_unit = "joule"


class Pressure(PintQuantity):
    """A pressure (``"5 kPa"``, ``"2 bar"``)."""

    reference_unit = "pascal"


# --- Dimension-agnostic quantity ---------------------------------------------
#
# The scalars above are dimension-*locked*: each has a fixed reference_unit and
# stores an integer canonical. A GenericQuantity has no fixed dimension — it
# accepts any unit-bearing quantity of any dimension and keeps whatever unit the
# value carries. It is used where the expected dimension is only known at
# runtime (e.g. a mechanism parameter value whose unit the mechanism declares).

#: A letter immediately followed by digits denotes an exponent ("cm2" -> "cm**2"),
#: which pint writes as "**". The lookbehind avoids mangling scientific notation.
_COMPACT_EXPONENT_RE = re.compile(r"(?<![0-9.])([a-zA-Z])(\d+)")


def normalize_compact_units(expression: str) -> str:
    """Rewrite compact exponent spelling (``"S/cm2"`` -> ``"S/cm**2"``) for pint."""
    return _COMPACT_EXPONENT_RE.sub(r"\1**\2", expression)


def _render_dimensionality(dims: Any) -> str:
    """Deterministic string form of a pint dimensionality (matches core.units)."""
    positive = sorted((d, e) for d, e in dims.items() if e > 0)
    negative = sorted((d, e) for d, e in dims.items() if e < 0)
    if not positive and not negative:
        return "dimensionless"

    def term(d: str, e: Any) -> str:
        e = abs(e)
        return d if e == 1 else f"{d} ** {e:g}"

    rendered = " * ".join(term(d, e) for d, e in positive) if positive else "1"
    for d, e in negative:
        rendered += f" / {term(d, e)}"
    return rendered


#: Deliberate opt-out: a quantity in *arbitrary units* (``"a.u."``) — a normalized
#: signal with no defined physical dimension (fluorescence, absorbance, ...). Explicit,
#: so a typo'd real unit still fails; only these exact spellings decline dimensional
#: validation on purpose. ``"au"`` is intentionally excluded (it is pint's astronomical
#: unit).
ARBITRARY_UNIT = "a.u."
ARBITRARY_DIMENSION = "[arbitrary]"
_ARBITRARY_SPELLINGS = frozenset(
    {"a.u.", "a.u", "arb", "arbitrary unit", "arbitrary units", "arbitrary_unit"}
)
_ARBITRARY_VALUE_RE = re.compile(
    r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(\S.*?)\s*$"
)


def is_arbitrary_unit(text: object) -> bool:
    """True if ``text`` is a recognized spelling of arbitrary units (``"a.u."``)."""
    return isinstance(text, str) and text.strip().lower() in _ARBITRARY_SPELLINGS


def arbitrary_magnitude(raw: object) -> "float | None":
    """The magnitude if ``raw`` is a value in arbitrary units (``"5 a.u."``), else None."""
    if not isinstance(raw, str):
        return None
    match = _ARBITRARY_VALUE_RE.match(raw)
    if match and is_arbitrary_unit(match.group(2)):
        return float(match.group(1))
    return None


def parse_generic_quantity(raw: object) -> "pint.Quantity":
    """Parse a unit-bearing quantity string into a pint Quantity.

    Rejects bare numbers (a quantity must be given in string form, e.g.
    ``"0.12 S/cm2"``) and anything pint cannot parse.
    """
    if isinstance(raw, bool):
        raise ValueError("GenericQuantity: a boolean is not a quantity")
    if isinstance(raw, (int, float)):
        raise ValueError(
            f"GenericQuantity: a bare number ({raw!r}) is not a quantity; provide "
            "it as a unit-bearing string, e.g. '0.12 S/cm2' (or '0.5 dimensionless')"
        )
    if hasattr(raw, "to"):  # already a pint Quantity
        return raw
    ureg = get_registry()
    try:
        return ureg.Quantity(normalize_compact_units(raw))
    except Exception as exc:  # pragma: no cover - pint raises various types
        raise ValueError(f"GenericQuantity: cannot parse quantity {raw!r}: {exc}") from exc


def generic_quantity_string(raw: object) -> str:
    """Normalized, re-parseable in-memory form (``"0.12 siemens / centimeter ** 2"``)."""
    magnitude = arbitrary_magnitude(raw)
    if magnitude is not None:
        return f"{magnitude} {ARBITRARY_UNIT}"
    q = parse_generic_quantity(raw)
    return f"{q.magnitude} {q.units}"


def generic_quantity_struct(raw: object) -> dict:
    """The stored dual struct ``{magnitude, unit, dimension}`` for a quantity."""
    magnitude = arbitrary_magnitude(raw)
    if magnitude is not None:
        return {"magnitude": magnitude, "unit": ARBITRARY_UNIT, "dimension": ARBITRARY_DIMENSION}
    q = parse_generic_quantity(raw)
    return {
        "magnitude": float(q.magnitude),
        "unit": str(q.units),
        "dimension": _render_dimensionality(q.dimensionality),
    }


class GenericQuantity:
    """A physical quantity of any dimension (``"0.12 S/cm2"``, ``"-54.3 mV"``, ``"2 mM"``).

    Unlike the dimension-locked scalars, this keeps whatever unit the value
    carries and does not normalize to a canonical integer — its dimension is
    whatever the value has. A bare number without a unit is rejected. On the wire
    it is a string; validating it against an expected dimension (e.g. a declared
    mechanism parameter) is the caller's job.
    """


def _build_generic_scalar() -> ScalarDefinition:
    def parse_value(raw: object) -> str:
        return generic_quantity_string(raw)

    def serialize(value: object) -> str:
        if hasattr(value, "to"):  # a pint Quantity
            return f"{value.magnitude} {value.units}"
        return str(value)  # an already-normalized in-memory string

    return strawberry.scalar(
        name="GenericQuantity",
        description=inspect.getdoc(GenericQuantity) or "",
        serialize=serialize,
        parse_value=parse_value,
    )


SCALAR_MAP[GenericQuantity] = _build_generic_scalar()


# --- Unit and Dimension scalars ----------------------------------------------
#
# These validate the *metadata* of a quantity rather than a value: a Unit is a
# parseable pint unit ("mV", "S/cm2"); a Dimension is a parseable pint
# dimensionality ("[length]", "[current] / [length] ** 2", "dimensionless").


def parse_unit(raw: object) -> str:
    """Validate that ``raw`` is a parseable pint unit and return it.

    Compact-exponent spelling ("cm2") is accepted and the given spelling is
    otherwise preserved. A quantity ("5 mV"), a bare number, an empty string, an
    unknown unit, or a dimension ("[length]") are all rejected.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Unit: expected a unit string, got {raw!r}")
    text = raw.strip()
    if not text:
        raise ValueError("Unit: an empty string is not a unit")
    if is_arbitrary_unit(text):
        return ARBITRARY_UNIT
    try:
        # Validate against the pint-normalized form, but keep the given spelling
        # ("S/cm2" stays "S/cm2" for the UI; consumers normalize when parsing).
        get_registry().Unit(normalize_compact_units(text))
    except Exception as exc:  # pragma: no cover - pint raises various types
        raise ValueError(f"Unit: {raw!r} is not a valid unit: {exc}") from exc
    return text


def parse_dimension(raw: object) -> str:
    """Validate that ``raw`` is a parseable pint dimensionality and return its
    canonical, order-stable form.

    A unit expression is accepted and reduced to its dimensionality; the
    ``"dimensionless"`` sentinel (which pint's parser cannot resolve) and the
    empty string both canonicalize to ``"dimensionless"``.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Dimension: expected a dimension string, got {raw!r}")
    text = raw.strip()
    if is_arbitrary_unit(text) or text == ARBITRARY_DIMENSION:
        return ARBITRARY_DIMENSION
    if not text or text == "dimensionless":
        return "dimensionless"
    try:
        dims = get_registry().get_dimensionality(normalize_compact_units(text))
    except Exception as exc:  # pragma: no cover - pint raises various types
        raise ValueError(f"Dimension: {raw!r} is not a valid pint dimension: {exc}") from exc
    return _render_dimensionality(dims)


class Unit:
    """A physical unit of measurement (``"mV"``, ``"S/cm2"``, ``"second"``).

    A string scalar validating that the value parses as a pint unit — a bad unit,
    a bare quantity (``"5 mV"``), or a dimension is rejected. Compact exponent
    spelling (``"cm2"``) is accepted and the given spelling is preserved.
    """


class Dimension:
    """A physical dimension (``"[length]"``, ``"[current] / [length] ** 2"``, ``"dimensionless"``).

    A string scalar validating that the value is a parseable pint dimensionality and
    serializing it to a canonical, order-stable string (the quantity-compatibility
    key). A unit expression is accepted and reduced to its dimensionality.
    """


def _build_string_scalar(cls: type, parse: "Callable[[object], str]") -> ScalarDefinition:
    def parse_value(raw: object) -> str:
        return parse(raw)

    def serialize(value: object) -> str:
        return str(value)

    return strawberry.scalar(
        name=cls.__name__,
        description=inspect.getdoc(cls) or "",
        serialize=serialize,
        parse_value=parse_value,
    )


SCALAR_MAP[Unit] = _build_string_scalar(Unit, parse_unit)
SCALAR_MAP[Dimension] = _build_string_scalar(Dimension, parse_dimension)
