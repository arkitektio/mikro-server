"""The descriptors of mikro's containers and lenses: the server-side twin of the client's vocabulary.

They are declared once, as data, in ``mikro_server.contract`` (their keys are in
``mikro_server.vocabulary``), and read from there by everything that states them: a signal
carries the object's descriptors so rekuest can match it against triggers and against the
``requires`` of the ports it would be fed to, and the GraphQL types answer them as
``descriptors``, so a client can ask which actions take the object in hand. One function per
kind computes them, bound as the structure's ``describe`` in ``mikro_server.service``, so the
field and the signal cannot disagree.

**A container and its whole lens describe identically**, and a lens that selects something
describes the selection: a crop's channel extent is the crop's, a windowed annotation lens
counts the shapes in its windows. ``@mikro/is_whole`` is the one key that tells them apart.

Every describer is total: a half-written row (axes and shape that disagree) answers ``{}``,
still an object, rather than failing the save that announced it.

What each costs beyond the row: an array's value kind is two queries (its primary derivation);
a lens reached through ``Lens.objects.with_sources()`` has its container, that container's
axes, a table's columns and a sparse dataset's layouts in hand; a *windowed* annotation lens
reads the collection's annotations to count the ones in its windows.
"""

from collections import Counter
from collections.abc import Sequence

from core import enums, models
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from mikro_server.vocabulary import ARRAY_AXIS_TYPES, EXTENT_KEYS, GEOMETRY_AXIS_TYPES, IS_WHOLE, KEY_BY_AXIS_TYPE, RECORD_AXIS_TYPES, VALUE_KIND

Descriptors = dict[str, int | str | bool]


def axis_count_descriptors(axis_types: Sequence[str], counted: Sequence[str]) -> dict[str, int]:
    """One count per axis type in ``counted``, zero included: a spec matches on absence too."""
    counts = Counter(axis_types)
    return {KEY_BY_AXIS_TYPE[axis_type]: counts.get(axis_type, 0) for axis_type in counted}


def array_descriptors(axis_types: Sequence[str], shape: Sequence[int]) -> dict[str, int]:
    """The client's seven array descriptors: per-axis-type counts and the channel and time extents, from the axis types and the (level-0) shape."""
    if len(axis_types) != len(shape):
        raise ValueError(f"{len(axis_types)} axis types for a {len(shape)}-dimensional shape")
    descriptors = axis_count_descriptors(axis_types, ARRAY_AXIS_TYPES)
    for key, wanted in EXTENT_KEYS.items():
        descriptors[key] = sum(extent for axis_type, extent in zip(axis_types, shape) if axis_type == wanted)
    return descriptors


def array_value_kind(dataset: models.ArrayDataset) -> str:
    """What an array's values mean, from what its author stated -- never guessed from the pixels.

    A derivation declared CATEGORIZED makes a label map, whatever its shape, so it is asked
    first; a DISPLACEMENT value axis makes a vector field, a COORDINATE value axis a coordinate
    field; everything else is intensity. The same signal a bootstrapped scene reads to draw a
    label layer, so a port requiring ``categorical`` and a scene builder cannot disagree.
    """
    if dataset.pk in graph_logic.categorized_dataset_ids([dataset.pk]):
        return "categorical"
    spec = dataset.spec
    if enums.ArrayDatasetSpec.DISPLACEMENT_FIELD in spec:
        return "vector"
    if enums.ArrayDatasetSpec.COORDINATE_FIELD in spec:
        return "coordinates"
    return "intensity"


def dataset_descriptors(dataset: models.ArrayDataset) -> Descriptors:
    """An array dataset's descriptors, from the row (in a signal: read at commit, the axes are written after it)."""
    try:
        counted = array_descriptors([axis.type for axis in dataset.axes], dataset.shape_list)
    except ValueError:  # axes and shape disagree (a half-written dataset): no descriptors, still an object
        return {}
    return {**counted, VALUE_KIND: array_value_kind(dataset), IS_WHOLE: True}


def array_lens_descriptors(lens: models.ArrayLens) -> Descriptors:
    """An array lens' descriptors: its dataset's axes over the shape its slices cut out.

    A lens keeps every axis of its dataset (a slice narrows an axis, it never drops one), so
    this is the client's ``lens_descriptors`` computed from the same two facts, with the
    value kind of the dataset it is cut from.
    """
    try:
        counted = array_descriptors([axis.type for axis in lens.dataset.axes], lens.shape_list)
    except ValueError:
        return {}
    return {**counted, VALUE_KIND: array_value_kind(lens.dataset), IS_WHOLE: lens.is_whole}


def table_descriptors(table: models.TableDataset, *, is_whole: bool = True) -> Descriptors:
    """A table's descriptors: where its rows sit, and what its schema says the rows carry. No row count: the server never scans a table."""
    roles = Counter(column.role for column in table.columns.all())
    return {
        **axis_count_descriptors([axis.type for axis in table.axes], RECORD_AXIS_TYPES),
        "@mikro/n_coordinate_columns": roles.get(enums.ColumnRoleChoices.COORDINATE.value, 0),
        "@mikro/n_attribute_columns": roles.get(enums.ColumnRoleChoices.ATTRIBUTE.value, 0),
        "@mikro/has_track_ids": roles.get(enums.ColumnRoleChoices.TRACK_ID.value, 0) > 0,
        "@mikro/has_group_ids": roles.get(enums.ColumnRoleChoices.GROUP_ID.value, 0) > 0,
        "@mikro/has_labels": roles.get(enums.ColumnRoleChoices.LABEL.value, 0) > 0,
        IS_WHOLE: is_whole,
    }


def sparse_descriptors(dataset: models.SparseDataset, *, is_whole: bool = True) -> Descriptors:
    """A sparse matrix's descriptors: its rank, which axes enumerate, and how many of them a contiguous read can select along."""
    axes = dataset.axes
    return {
        **axis_count_descriptors([axis.type for axis in axes], RECORD_AXIS_TYPES),
        "@mikro/n_axes": len(axes),
        "@mikro/n_layouts": len(dataset.arrays.all()),
        IS_WHOLE: is_whole,
    }


def mesh_descriptors(collection: models.MeshCollection, *, is_whole: bool = True) -> Descriptors:
    """A mesh collection's descriptors: the axes of its space. Its store's counts are declared, never authoritative, and are not stated."""
    return {**axis_count_descriptors([axis.type for axis in collection.coordinate_system.axes.all()], GEOMETRY_AXIS_TYPES), IS_WHOLE: is_whole}


def network_descriptors(collection: models.NetworkCollection, *, is_whole: bool = True) -> Descriptors:
    """A network collection's descriptors: the axes of its space, and whether its manifest declares per-node value columns."""
    return {
        **axis_count_descriptors([axis.type for axis in collection.coordinate_system.axes.all()], GEOMETRY_AXIS_TYPES),
        "@mikro/has_attributes": bool(collection.store.attributes),
        IS_WHOLE: is_whole,
    }


def annotations_in_windows(collection: models.AnnotationCollection, windows: Sequence[coords_logic.WindowLike]) -> list[models.Annotation]:
    """A collection's annotations lying in these windows, judged the way the overlap filter judges a shape: by the box of its vertices and pins.

    Every row for a whole lens. For a windowed one each shape's box is compared in the drawing
    space -- the collection's own units, which is what a window is stated in -- so a shape
    that is drawn is kept, not a shape whose stored bbox says so. The one implementation
    behind `AnnotationLens.annotations` and the `@mikro/n_annotations` descriptor.
    """
    rows = collection.annotations.order_by("pk")
    if not windows:
        return list(rows)
    system = collection.coordinate_system_or_none
    if system is None:
        return []
    names = [axis.name for axis in system.axes.all()]
    bounds = coords_logic.window_bounds(windows)
    kept = []
    for annotation in rows:
        pins = {name: float(value) for name, value in (annotation.coordinates or {}).items()}
        box = coords_logic.annotation_box(names, annotation.vectors or [], pins, padded=False)
        if box is not None and coords_logic.boxes_overlap(box, bounds):
            kept.append(annotation)
    return kept
def annotation_descriptors(collection: models.AnnotationCollection, windows: Sequence[coords_logic.WindowLike] = (), *, is_whole: bool = True) -> Descriptors:
    """An annotation collection's descriptors: the axes of its drawing space and how many shapes the selection holds."""
    system = collection.coordinate_system_or_none
    axis_types = [axis.type for axis in system.axes.all()] if system is not None else []
    return {**axis_count_descriptors(axis_types, GEOMETRY_AXIS_TYPES), "@mikro/n_annotations": collection.annotations.count() if not windows else len(annotations_in_windows(collection, windows)), IS_WHOLE: is_whole}


def lens_descriptors(lens: models.Lens) -> Descriptors:
    """A lens' descriptors: its container's vocabulary, computed over what it selects."""
    if isinstance(lens, models.ArrayLens):
        return array_lens_descriptors(lens)
    if isinstance(lens, models.TableLens):
        return table_descriptors(lens.table_dataset, is_whole=lens.is_whole)
    if isinstance(lens, models.SparseLens):
        return sparse_descriptors(lens.sparse_dataset, is_whole=lens.is_whole)
    if isinstance(lens, models.MeshLens):
        return mesh_descriptors(lens.mesh_collection, is_whole=lens.is_whole)
    if isinstance(lens, models.NetworkLens):
        return network_descriptors(lens.network_collection, is_whole=lens.is_whole)
    if isinstance(lens, models.AnnotationLens):
        return annotation_descriptors(lens.annotation_collection, lens.windows_list, is_whole=lens.is_whole)
    return {}
