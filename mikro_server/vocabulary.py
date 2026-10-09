"""The descriptors of mikro's containers and lenses, as data: the server-side twin of the client's vocabulary.

Declared once, on the structures in ``mikro_server.contract`` (``hosts``), and read from there
by everything that states them: a signal carries the object's descriptors so rekuest can match
it against triggers and against the ``requires`` of the ports it would be fed to, and the
GraphQL types answer them as ``descriptors``, so a client can ask which actions take the object
in hand. The array keys were declared first by the mikro client's spec vocabulary
(``mikro.arkitekt.specs``), so those keys and their meaning match
``mikro.arkitekt.specs.lens_descriptors`` exactly: one count per axis type (zero included --
``Still`` and ``SingleChannel`` match on absence) plus the total extent along the channel and
time axes. The other kinds say what is cheaply true of them from their schema and their axes;
nothing here needs a store opened.

A container and the lens that selects all of it carry the same keys, so an action typed on
either reads one vocabulary; ``@mikro/is_whole`` is what tells the two apart, and what tells
a crop from the whole image.

Nothing here imports Django: the contract says these before the service has a config, and
``core.descriptors`` computes them from the same keys.
"""

from collections.abc import Sequence

from arkitekt_service.contract import Descriptor

#: One count per axis type an object may carry. Which of them a kind counts is the kind's
#: ``*_AXIS_TYPES`` below.
KEY_BY_AXIS_TYPE = {
    "SPACE": "@mikro/n_space_axes",
    "TIME": "@mikro/n_time_axes",
    "CHANNEL": "@mikro/n_channel_axes",
    "SPECTRUM": "@mikro/n_spectrum_axes",
    "MICROTIME": "@mikro/n_microtime_axes",
    "INDEX": "@mikro/n_index_axes",
}
#: The axis types an array counts: the client's five, and no more, so the seven array keys stay
#: the ones its specs were written against.
ARRAY_AXIS_TYPES: tuple[str, ...] = ("SPACE", "TIME", "CHANNEL", "SPECTRUM", "MICROTIME")
#: What a record container -- a table, a sparse matrix -- counts: where its rows sit, and how
#: many of its axes enumerate rather than measure.
RECORD_AXIS_TYPES: tuple[str, ...] = ("SPACE", "TIME", "INDEX")
#: What a geometry container -- a mesh, a network, an annotation collection -- counts.
GEOMETRY_AXIS_TYPES: tuple[str, ...] = ("SPACE", "TIME")

EXTENT_KEYS = {"@mikro/n_channels": "CHANNEL", "@mikro/n_timepoints": "TIME"}

IS_WHOLE = "@mikro/is_whole"
VALUE_KIND = "@mikro/value_kind"
#: What an array's values mean, decided from facts its author stated: a derivation declared
#: CATEGORIZED makes a label map, a DISPLACEMENT value axis a vector field, a COORDINATE value
#: axis a coordinate field; everything else is intensity.
VALUE_KINDS = ("categorical", "vector", "coordinates", "intensity")


def _axis_counts(axis_types: Sequence[str]) -> list[Descriptor]:
    return [Descriptor(key=KEY_BY_AXIS_TYPE[axis_type], type="INT", description=f"How many of its axes are {axis_type} axes") for axis_type in axis_types]


_IS_WHOLE = Descriptor(key=IS_WHOLE, type="BOOL", description="Whether this selects everything: true of a container and of its whole lens, false of a crop or a window")

#: Every descriptor ``core.descriptors.array_descriptors`` produces, plus the value kind and
#: whether it is whole -- what mikro declares an array dataset and an array lens carry.
ARRAY_DESCRIPTORS = [
    *_axis_counts(ARRAY_AXIS_TYPES),
    *(Descriptor(key=key, type="INT", description=f"Its total extent along its {axis_type} axes") for key, axis_type in EXTENT_KEYS.items()),
    Descriptor(key=VALUE_KIND, type="STRING", description="What its values mean: categorical (a label map, from a derivation declared CATEGORIZED), vector (a DISPLACEMENT value axis), coordinates (a COORDINATE value axis) or intensity"),
    _IS_WHOLE,
]
#: A table and a table lens: where its rows sit, and what its schema says the rows carry. No
#: row count -- the server never scans a table.
TABLE_DESCRIPTORS = [
    *_axis_counts(RECORD_AXIS_TYPES),
    Descriptor(key="@mikro/n_coordinate_columns", type="INT", description="How many of its columns place a row (role COORDINATE): the axes of its space"),
    Descriptor(key="@mikro/n_attribute_columns", type="INT", description="How many of its columns are measurements or properties (role ATTRIBUTE)"),
    Descriptor(key="@mikro/has_track_ids", type="BOOL", description="Whether a column groups rows into trajectories (role TRACK_ID)"),
    Descriptor(key="@mikro/has_group_ids", type="BOOL", description="Whether a column groups rows into connected objects (role GROUP_ID)"),
    Descriptor(key="@mikro/has_labels", type="BOOL", description="Whether a column carries a per-row text label (role LABEL)"),
    _IS_WHOLE,
]
#: A sparse matrix and a sparse lens: its rank, which axes enumerate, and how many of them a
#: contiguous read can select along.
SPARSE_DESCRIPTORS = [
    *_axis_counts(RECORD_AXIS_TYPES),
    Descriptor(key="@mikro/n_axes", type="INT", description="Its rank: how many axes the matrix has"),
    Descriptor(key="@mikro/n_layouts", type="INT", description="How many of its axes a contiguous read can select along: the layouts its store holds"),
    _IS_WHOLE,
]
MESH_DESCRIPTORS = [*_axis_counts(GEOMETRY_AXIS_TYPES), _IS_WHOLE]
NETWORK_DESCRIPTORS = [
    *_axis_counts(GEOMETRY_AXIS_TYPES),
    Descriptor(key="@mikro/has_attributes", type="BOOL", description="Whether its manifest declares per-node value columns"),
    _IS_WHOLE,
]
ANNOTATION_DESCRIPTORS = [
    *_axis_counts(GEOMETRY_AXIS_TYPES),
    Descriptor(key="@mikro/n_annotations", type="INT", description="How many annotations the selection holds: every one of the collection's, or, for a windowed lens, those whose extent lies in its windows"),
    _IS_WHOLE,
]

#: The vocabulary per lens kind (``core.enums.LensKind`` values), which is also the vocabulary
#: of the container each kind selects over.
DESCRIPTORS_BY_KIND: dict[str, list[Descriptor]] = {
    "ARRAY": ARRAY_DESCRIPTORS,
    "TABLE": TABLE_DESCRIPTORS,
    "SPARSE": SPARSE_DESCRIPTORS,
    "MESH": MESH_DESCRIPTORS,
    "NETWORK": NETWORK_DESCRIPTORS,
    "ANNOTATION": ANNOTATION_DESCRIPTORS,
}
