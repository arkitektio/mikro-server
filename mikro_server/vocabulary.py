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

Every descriptor is stored on its row (``stored_descriptors``) and recomputed whenever the row is
saved or restored -- the value kind when a derivation edge is drawn, the count of shapes when one
is drawn -- so a ``requires`` filter is one indexed query for every key.

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
#: the ones its specs were written against. ``@mikro/spec`` is beside them and the server's own.
ARRAY_AXIS_TYPES: tuple[str, ...] = ("SPACE", "TIME", "CHANNEL", "SPECTRUM", "MICROTIME")
#: What a record container -- a table, a sparse matrix -- counts: where its rows sit, and how
#: many of its axes enumerate rather than measure.
RECORD_AXIS_TYPES: tuple[str, ...] = ("SPACE", "TIME", "INDEX")
#: What a geometry container -- a mesh, a network, an annotation collection -- counts.
GEOMETRY_AXIS_TYPES: tuple[str, ...] = ("SPACE", "TIME")

EXTENT_KEYS = {"@mikro/n_channels": "CHANNEL", "@mikro/n_timepoints": "TIME"}

IS_WHOLE = "@mikro/is_whole"
#: What the object structurally is, as the values of its kind's spec enum
#: (``core.enums.ArrayDatasetSpec``, ``TableDatasetSpec``, ``SparseDatasetSpec``): the same
#: list its ``spec`` field answers, so a port can require a member of it. A lens states the
#: spec of its selection.
SPEC = "@mikro/spec"
VALUE_KIND = "@mikro/value_kind"
#: The shape one annotation is drawn as, as the names of ``core.enums.AnnotationKind``.
ANNOTATION_KIND = "@mikro/annotation_kind"
N_ANNOTATIONS = "@mikro/n_annotations"

#: The element type of an array's level-0 zarr store, as numpy spells it (uint8, uint16, float32, ...).
DTYPE = "@mikro/dtype"
CONTAINER_KIND = "@mikro/container_kind"


def _axis_counts(axis_types: Sequence[str]) -> list[Descriptor]:
    return [Descriptor(key=KEY_BY_AXIS_TYPE[axis_type], type="INT", description=f"How many of its axes are {axis_type} axes") for axis_type in axis_types]


_IS_WHOLE = Descriptor(key=IS_WHOLE, type="BOOL", description="Whether this selects everything: true of a container and of its whole lens, false of a crop or a window")


def _spec(members: str, selection: str) -> Descriptor:
    return Descriptor(key=SPEC, type="LIST", description=f"What it structurally is, as the members of its `spec`: {members}. Of a lens, the spec of what it selects: {selection}")


#: Every descriptor ``core.descriptors.array_descriptors`` produces, plus the value kind and
#: whether it is whole -- what mikro declares an array dataset and an array lens carry.
ARRAY_DESCRIPTORS = [
    *_axis_counts(ARRAY_AXIS_TYPES),
    *(Descriptor(key=key, type="INT", description=f"Its total extent along its {axis_type} axes") for key, axis_type in EXTENT_KEYS.items()),
    Descriptor(key=VALUE_KIND, type="STRING", description="What its values mean: categorical (a label map, from a derivation declared CATEGORIZED), vector (a DISPLACEMENT value axis), coordinates (a COORDINATE value axis) or intensity"),
    Descriptor(key=DTYPE, type="STRING", description="The element type of its values, as numpy spells it (uint8, uint16, float32, ...): an action typed on integers (labels, thresholds) or on floats asks for it"),
    _spec("one spatial member (SCALAR, PROFILE, IMAGE, VOLUME, HYPERVOLUME), a modifier per non-spatial axis it has more than one position along, and TRACE for a curve", "an axis counts only when the lens keeps more than one position along it"),
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
    _spec("one placement member (RECORDS, POINTS_1D, POINTS_2D, POINTS_3D, POINTS_ND), a modifier per declared fact (TIMED, TRACKED, GROUPED, KEYED, NODE_TABLE, EDGE_TABLE), and SERIES for what a chart draws whole", "a coordinate column a window pins to one value no longer counts"),
    _IS_WHOLE,
]
#: A sparse matrix and a sparse lens: its rank, which axes enumerate, and how many of them a
#: contiguous read can select along.
SPARSE_DESCRIPTORS = [
    *_axis_counts(RECORD_AXIS_TYPES),
    Descriptor(key="@mikro/n_axes", type="INT", description="Its rank: how many axes the matrix has"),
    Descriptor(key="@mikro/n_layouts", type="INT", description="How many of its axes a contiguous read can select along: the layouts its store holds"),
    _spec("one rank member (SCALAR, VECTOR, MATRIX, TENSOR) by how many axes have more than one position, and SLICE for what a picker reads", "an axis a window pins to one position no longer counts"),
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
    Descriptor(key=N_ANNOTATIONS, type="INT", description="How many annotations the selection holds: every one of the collection's, or, for a windowed lens, those whose extent lies in its windows"),
    _IS_WHOLE,
]
#: One shape, not a selection of them: what it is drawn as, and the axes of the space it is
#: drawn in (its collection's), which is what tells a box on a plane from one in a volume.
ANNOTATION_SHAPE_DESCRIPTORS = [
    Descriptor(key=ANNOTATION_KIND, type="STRING", description="The shape it is drawn as: POINT, MULTI_POINT, LINE, PATH, POLYGON, RECTANGLE, CUBE, CIRCLE, ELLIPSE, SPHERE or ELLIPSOID"),
    *_axis_counts(GEOMETRY_AXIS_TYPES),
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


#: A scene: how it composes, and how it asks to be opened.
SCENE_DESCRIPTORS = [
    Descriptor(key="@mikro/blending", type="STRING", description="How its layers are blended: additive, multiplicative or normal"),
    Descriptor(key="@mikro/preferred_view", type="STRING", description="The view it asks to be opened in: two_d, three_d or auto"),
]
#: A chart: the one metric axis it lays its data out along.
CHART_DESCRIPTORS = [
    Descriptor(key="@mikro/axis_type", type="STRING", description="What its axis measures: SPACE, TIME, MICROTIME or SPECTRUM"),
    Descriptor(key="@mikro/axis_unit", type="STRING", description="The unit of its axis"),
]
#: A metric: what was measured, its value, and the object it is about. Few and flat, because a
#: metric is written by the thousand: a trigger reacts to a key, a value past a threshold, a code.
METRIC_DESCRIPTORS = [
    Descriptor(key="@mikro/metric_key", type="STRING", description="What was measured, namespaced by who measured it: ais_suite.measure_profile/threshold_distance"),
    Descriptor(key="@mikro/magnitude", type="FLOAT", description="Its number, for a quantity; absent for a code"),
    Descriptor(key="@mikro/unit", type="STRING", description="The unit of its magnitude, for a quantity; absent for a code"),
    Descriptor(key="@mikro/metric_code", type="STRING", description="The category a process emitted, for a code; absent for a quantity"),
    Descriptor(key="@mikro/target_identifier", type="STRING", description="The structure of the object it is about: @mikro/annotation, @mikro/arraydataset, @mikro/tabledataset or @mikro/sparsedataset"),
    Descriptor(key="@mikro/target_object", type="STRING", description="The id of the object it is about"),
]
#: A file as it was uploaded: what an importer is chosen by.
FILE_DESCRIPTORS = [
    Descriptor(key="@mikro/content_type", type="STRING", description="Its media type, empty when unknown"),
    Descriptor(
        key="@mikro/extension",
        type="STRING",
        description="Its file name's extension, lowercased and without the dot (czi, nd2, lif, tif, ome.tif, ome.zarr), empty when it has none: what picks an importer when the media type is only application/octet-stream",
    ),
    Descriptor(key="@mikro/size_bytes", type="INT", description="Its size in bytes, 0 when unknown"),
]
#: A space of the coordinate graph: how many axes of each type it has.
COORDINATE_SYSTEM_DESCRIPTORS = _axis_counts(tuple(KEY_BY_AXIS_TYPE))
#: An edge of the coordinate graph: what kind of map it is, how sure anyone is of it, and between spaces of how many axes.
TRANSFORMATION_DESCRIPTORS = [
    Descriptor(
        key="@mikro/transform_kind",
        type="STRING",
        description="What kind of map it is: IDENTITY, SCALE, TRANSLATION, MAP_AXIS, AFFINE, ROTATION, SEQUENCE, BY_DIMENSION, FIELD or UNMAPPABLE",
    ),
    Descriptor(key="@mikro/validity", type="STRING", description="How it is known: MANUAL, INFERRED, VALIDATED or UNKNOWN. A refining action asks for INFERRED or UNKNOWN"),
    Descriptor(key="@mikro/value_relation", type="STRING", description="What the data it derives did to the values: IDENTICAL, TRANSFORMED or CATEGORIZED, empty when it is a placement, not a derivation"),
    Descriptor(key="@mikro/n_input_axes", type="INT", description="How many axes the space it maps from has"),
    Descriptor(key="@mikro/n_output_axes", type="INT", description="How many axes the space it maps to has"),
]
_CONTAINER_KIND = Descriptor(key=CONTAINER_KIND, type="STRING", description="Which kind of container it belongs to: ARRAY, TABLE, SPARSE, MESH or ANNOTATION")
#: A position in a container that metadata is pinned to.
COORDINATE_ANCHOR_DESCRIPTORS = [_CONTAINER_KIND]
#: A file and the container holding the same data.
FILE_LINK_DESCRIPTORS = [
    Descriptor(key="@mikro/link_direction", type="STRING", description="Which was made from which: SOURCE (the container from the file) or RENDITION (the file from the container)"),
    _CONTAINER_KIND,
]
