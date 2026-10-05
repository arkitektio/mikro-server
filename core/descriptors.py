"""The descriptors of mikro's arrays — the server-side twin of the client's vocabulary.

They are declared once, on the structures in ``mikro_server/service.py``, and read from there
by everything that states them: a signal carries the object's descriptors so rekuest can match
it against triggers and against the ``requires`` of the ports it would be fed to, and the
GraphQL types answer them as ``descriptors``, so a client can ask which actions take the object
in hand. Those ports were
declared by the mikro client's spec vocabulary (``mikro.arkitekt.specs``), so these keys and their
meaning must match ``mikro.arkitekt.specs.lens_descriptors`` exactly: one count per axis type
(zero included — ``Still`` and ``SingleChannel`` match on absence) plus the total extent along the
channel and time axes. ``@mikro/value_kind`` is deliberately absent there too: it is provenance,
carried only by a producer's ``Provides``.
"""

from collections import Counter
from collections.abc import Sequence

from arkitekt_service.service import Descriptor

KEY_BY_AXIS_TYPE = {
    "SPACE": "@mikro/n_space_axes",
    "TIME": "@mikro/n_time_axes",
    "CHANNEL": "@mikro/n_channel_axes",
    "SPECTRUM": "@mikro/n_spectrum_axes",
    "MICROTIME": "@mikro/n_microtime_axes",
}
EXTENT_KEYS = {"@mikro/n_channels": "CHANNEL", "@mikro/n_timepoints": "TIME"}
#: Every descriptor :func:`array_descriptors` produces — what mikro declares an array dataset
#: and a lens carry.
ARRAY_DESCRIPTORS = (
    *(Descriptor(key, "INT", f"How many of its axes are {axis_type} axes") for axis_type, key in KEY_BY_AXIS_TYPE.items()),
    *(Descriptor(key, "INT", f"Its total extent along its {axis_type} axes") for key, axis_type in EXTENT_KEYS.items()),
)
ARRAY_DESCRIPTOR_KEYS = tuple(descriptor.key for descriptor in ARRAY_DESCRIPTORS)


def array_descriptors(axis_types: Sequence[str], shape: Sequence[int]) -> dict[str, int]:
    """The descriptors of an array with these per-axis types and this (level-0) shape."""
    if len(axis_types) != len(shape):
        raise ValueError(f"{len(axis_types)} axis types for a {len(shape)}-dimensional shape")
    counts = Counter(axis_types)
    descriptors = {key: counts.get(axis_type, 0) for axis_type, key in KEY_BY_AXIS_TYPE.items()}
    for key, wanted in EXTENT_KEYS.items():
        descriptors[key] = sum(extent for axis_type, extent in zip(axis_types, shape) if axis_type == wanted)
    return descriptors


def dataset_descriptors(dataset) -> dict[str, int]:  # noqa: ANN001 - a core.models.ArrayDataset
    """An array dataset's descriptors, from the row (in a signal: read at commit, the axes are written after it)."""
    try:
        return array_descriptors([axis.type for axis in dataset.axes], dataset.shape_list)
    except ValueError:  # axes and shape disagree (a half-written dataset): no descriptors, still an object
        return {}


def lens_descriptors(lens) -> dict[str, int]:  # noqa: ANN001 - a core.models.Lens
    """A lens' descriptors: its dataset's axes over the shape its slices cut out.

    A lens keeps every axis of its dataset (a slice narrows an axis, it never drops one), so
    this is the client's ``lens_descriptors`` computed from the same two facts.
    """
    try:
        return array_descriptors([axis.type for axis in lens.dataset.axes], lens.shape_list)
    except ValueError:
        return {}
