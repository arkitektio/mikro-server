"""The descriptors mikro announces for a dataset — the server-side twin of the client's vocabulary.

A signal (:mod:`rekuest_service.signals`) carries the object's descriptors so rekuest can match it
against triggers and against the ``requires`` of the ports it would be fed to. Those ports were
declared by the mikro client's spec vocabulary (``mikro.arkitekt.specs``), so these keys and their
meaning must match ``mikro.arkitekt.specs.lens_descriptors`` exactly: one count per axis type
(zero included — ``Still`` and ``SingleChannel`` match on absence) plus the total extent along the
channel and time axes. ``@mikro/value_kind`` is deliberately absent there too: it is provenance,
carried only by a producer's ``Provides``.
"""

from collections import Counter
from collections.abc import Sequence

KEY_BY_AXIS_TYPE = {
    "SPACE": "@mikro/n_space_axes",
    "TIME": "@mikro/n_time_axes",
    "CHANNEL": "@mikro/n_channel_axes",
    "SPECTRUM": "@mikro/n_spectrum_axes",
    "MICROTIME": "@mikro/n_microtime_axes",
}
EXTENT_KEYS = {"@mikro/n_channels": "CHANNEL", "@mikro/n_timepoints": "TIME"}
#: Every key :func:`array_descriptors` produces — what mikro declares its dataset signals carry.
ARRAY_DESCRIPTOR_KEYS = (*KEY_BY_AXIS_TYPE.values(), *EXTENT_KEYS)


def array_descriptors(axis_types: Sequence[str], shape: Sequence[int]) -> dict[str, int]:
    """The descriptors of an array with these per-axis types and this (level-0) shape."""
    if len(axis_types) != len(shape):
        raise ValueError(f"{len(axis_types)} axis types for a {len(shape)}-dimensional shape")
    counts = Counter(axis_types)
    descriptors = {key: counts.get(axis_type, 0) for axis_type, key in KEY_BY_AXIS_TYPE.items()}
    for key, wanted in EXTENT_KEYS.items():
        descriptors[key] = sum(extent for axis_type, extent in zip(axis_types, shape) if axis_type == wanted)
    return descriptors
