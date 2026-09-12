"""Deriving everything a client needs to reduce one axis of a lens to a phasor.

A phasor is the discrete Fourier transform of a pixel's profile along a continuously
sampled axis, evaluated at one harmonic::

    g = sum_k I_k cos(n w t_k) / sum_k I_k
    s = sum_k I_k sin(n w t_k) / sum_k I_k

Which means a client needs four things the array itself does not carry: how many bins the
axis has, how wide a bin is, what period the transform runs over, and -- for a lifetime
phasor -- the laser repetition rate the period should agree with. None of them is stored
anywhere as such. They are *derived*:

* the bin count from the lens' shape,
* the bin width from the axis' PHYSICAL calibration -- the ``scale`` of the edge mapping
  the dataset's pixels into a calibrated space, in that space's own unit,
* the laser rate from the lightpath graph anchored to the data.

Turning a calibration edge into "the width of one tau bin" means composing the edge's
matrix and reading the diagonal at that axis' position, which is real arithmetic and
belongs on the server rather than in every client that wants a lifetime. Hence this
module. It is pure -- specs, params and dicts, never the ORM -- so it is testable without
a database, matching :mod:`core.logic.coords`.
"""

from typing import Sequence

from core import enums
from core.logic import coords as coords_logic


def phasor_axis(axes: Sequence[coords_logic.AxisSpec]) -> coords_logic.AxisSpec | None:
    """The axis a phasor may be taken over, or None if there is none.

    The first MICROTIME or SPECTRUM axis. A dataset carrying both -- a spectrally resolved
    FLIM cube -- is legal, and a client picks between them by name.
    """
    return next((axis for axis in axes if coords_logic.is_phasor_axis(axis.type)), None)


def flatten_edge(kind: str, params: dict, children: Sequence[tuple[str, dict]] = ()) -> list[tuple[str, dict]]:
    """One transformation as the (kind, params) pairs :func:`core.logic.coords.compose` wants.

    A SEQUENCE keeps its parameters on its *children* rather than in its own ``params``
    (see ``core.logic.graph._sequence``), so composing it from its own params alone yields
    the identity -- a bin width of exactly 1.0, which looks like an uncalibrated axis rather
    than like a bug. Flattening here is what stops that.
    """
    if kind == enums.TransformKindChoices.SEQUENCE.value and children:
        return list(children)
    return [(kind, params)]


def axis_scale(edges: Sequence[tuple[str, dict]], axis_index: int, axis_count: int) -> float | None:
    """The scale factor one calibration edge applies along one axis: the physical size of a pixel.

    The diagonal of the composed matrix at that axis' position. None when the edge is not
    affine (a displacement field cannot be read as a per-axis spacing) or does not scale the
    axis at all.
    """
    try:
        matrix = coords_logic.compose(edges, axis_count)
    except coords_logic.NonAffineTransformError:
        return None

    scale = matrix[axis_index][axis_index]
    return scale if scale else None


def quantity(magnitude: float, unit: str) -> str:
    """A magnitude and a unit as the pint string a kanne GenericQuantity parses.

    Deliberately dimension-agnostic: over a MICROTIME axis this is a duration
    ("0.098 nanosecond"), over a SPECTRUM axis a wavelength ("5 nanometer"). The axis' unit
    is what says which, and the axis' *type* is what guarantees the unit has the right
    dimension (``core.logic.coords.assert_unit_matches_type``).
    """
    return f"{magnitude} {unit}"


def laser_frequency(graph: dict) -> int | None:
    """The repetition rate of the pulsed source in a lightpath graph, in its canonical unit.

    A FLIM phasor's angular frequency is the laser's: the arrival-time axis is a sampling of
    one excitation period. That rate is already modelled -- ``LaserElementModel`` carries a
    ``repetition_rate`` -- so it is read from there rather than copied onto the render node,
    where two layers over one dataset would be free to disagree about it.

    None when the lightpath has no laser, or the laser has no stated rate: an uncalibrated
    phasor still renders, its hue is just not traceable to an absolute lifetime.
    """
    for element in graph.get("elements", []) or []:
        rate = element.get("repetition_rate")
        if rate is None:
            continue
        # Quantities are stored as the {canonical, given, unit} dual struct, but a graph
        # written before the struct existed (or by hand) may hold the bare canonical int.
        if isinstance(rate, dict):
            canonical = rate.get("canonical")
            if canonical is not None:
                return int(canonical)
        elif isinstance(rate, int):
            return rate
    return None
