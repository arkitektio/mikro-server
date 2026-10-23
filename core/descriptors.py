"""The descriptors of mikro's containers and lenses: the server-side twin of the client's vocabulary.

They are declared once, as data, in ``mikro_server.contract`` (their keys are in
``mikro_server.vocabulary``). One function per kind computes them, bound as the structure's
``describe`` in ``mikro_server.service``, and what it computes is **stored** on the row
(``stored_descriptors``) whenever the row is saved or restored: a signal carries the stored value
so rekuest can match it against triggers and against the ``requires`` of the ports it would be
fed to, the GraphQL types answer it as ``descriptors``, and a ``requires`` filter reads it in SQL
(``core.logic.descriptor_filter``). The three cannot disagree.

What a save of the row cannot see is restored by whoever writes it (:func:`restore`): a
container's axes and level-0 array are written after its row (``whole_lens`` restores the
container when it mints the lens every container ends its creation with), a derivation edge
decides a dataset's value kind, a shape drawn changes a collection's count.

**A container and its whole lens describe identically**, and a lens that selects something
describes the selection: a crop's channel extent is the crop's, a windowed annotation lens
counts the shapes in its windows, and ``@mikro/spec`` is the spec of what is selected -- one
plane of a stack is an IMAGE, one frame of a tracking table is not TIMED. ``@mikro/is_whole`` is the one key that tells them apart.

Every describer is total: a half-written row (axes and shape that disagree) answers ``{}``,
still an object, rather than failing the save that announced it.

What each costs beyond the row (paid when the row is stored, never when it is read): an array's value kind is two queries (its primary derivation);
a lens reached through ``Lens.objects.with_sources()`` has its container, that container's
axes, a table's columns and a sparse dataset's layouts in hand; a *windowed* annotation lens
reads the collection's annotations to count the ones in its windows.
"""

from collections import Counter
from collections.abc import Sequence

from core import enums, models
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from mikro_server.vocabulary import ANNOTATION_KIND, ARRAY_AXIS_TYPES, CONTAINER_KIND, DTYPE, EXTENT_KEYS, GEOMETRY_AXIS_TYPES, IS_WHOLE, KEY_BY_AXIS_TYPE, N_ANNOTATIONS, RECORD_AXIS_TYPES, SPEC, VALUE_KIND

Descriptors = dict[str, int | str | bool | list[str]]


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


def array_dtype(dataset: models.ArrayDataset) -> str:
    """The element type of a dataset's values: its level-0 zarr store's, empty while it has none."""
    arrays = list(dataset.data_arrays.all())
    if not arrays:
        return ""
    store = min(arrays, key=lambda array: array.level).store
    return (store.dtype if store is not None else None) or ""


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
    # From the axes rather than `stored_spec`: the same two facts the counts are read off,
    # so the two agree whenever this is asked.
    spec = [member.value for member in coords_logic.specs_for_axes(dataset.axis_specs, dataset.shape_list)]
    return {**counted, VALUE_KIND: array_value_kind(dataset), DTYPE: array_dtype(dataset), SPEC: spec, IS_WHOLE: True}


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
    return {**counted, VALUE_KIND: array_value_kind(lens.dataset), DTYPE: array_dtype(lens.dataset), SPEC: [member.value for member in lens.derive_spec()], IS_WHOLE: lens.is_whole}


def table_descriptors(table: models.TableDataset, *, is_whole: bool = True, pinned: Sequence[str] = ()) -> Descriptors:
    """A table's descriptors: where its rows sit, and what its schema says the rows carry. No row count: the server never scans a table.

    ``pinned`` are the coordinate columns a lens' windows hold to one value: they are what
    makes a selection's spec differ from its table's. The counts stay the table's -- a
    window keeps every column, as a crop keeps every axis.
    """
    columns = table.column_specs
    roles = Counter(column.role for column in columns)
    spec = coords_logic.specs_for_columns(columns, pinned)
    return {
        **axis_count_descriptors([axis.type for axis in table.axes], RECORD_AXIS_TYPES),
        "@mikro/n_coordinate_columns": roles.get(enums.ColumnRoleChoices.COORDINATE.value, 0),
        "@mikro/n_attribute_columns": roles.get(enums.ColumnRoleChoices.ATTRIBUTE.value, 0),
        # Read off the spec, so the two spellings of one fact cannot part ways.
        "@mikro/has_track_ids": enums.TableDatasetSpec.TRACKED in spec,
        "@mikro/has_group_ids": enums.TableDatasetSpec.GROUPED in spec,
        "@mikro/has_labels": roles.get(enums.ColumnRoleChoices.LABEL.value, 0) > 0,
        SPEC: [member.value for member in spec],
        IS_WHOLE: is_whole,
    }


def sparse_descriptors(dataset: models.SparseDataset, *, is_whole: bool = True, windows: Sequence[coords_logic.WindowLike] = ()) -> Descriptors:
    """A sparse matrix's descriptors: its rank, which axes enumerate, how many of them a contiguous read can select along, and what these windows leave of it."""
    axes = dataset.axes
    return {
        **axis_count_descriptors([axis.type for axis in axes], RECORD_AXIS_TYPES),
        "@mikro/n_axes": len(axes),
        "@mikro/n_layouts": len(dataset.arrays.all()),
        SPEC: [member.value for member in dataset.spec_for(windows)],
        IS_WHOLE: is_whole,
    }


def mesh_descriptors(collection: models.MeshCollection, *, is_whole: bool = True) -> Descriptors:
    """A mesh collection's descriptors: the axes of its space. Its store's counts are declared, never authoritative, and are not stated."""
    return {**axis_count_descriptors([axis.type for axis in collection.coordinate_system.axes.all()], GEOMETRY_AXIS_TYPES), IS_WHOLE: is_whole}


def cad_model_descriptors(cad_model: models.CadModel, *, is_whole: bool = True) -> Descriptors:
    """A CAD model's descriptors: the axes of its space."""
    return {**axis_count_descriptors([axis.type for axis in cad_model.coordinate_system.axes.all()], GEOMETRY_AXIS_TYPES), IS_WHOLE: is_whole}


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
    return {**axis_count_descriptors(axis_types, GEOMETRY_AXIS_TYPES), N_ANNOTATIONS: collection.annotations.count() if not windows else len(annotations_in_windows(collection, windows)), IS_WHOLE: is_whole}


def annotation_shape_descriptors(annotation: models.Annotation) -> Descriptors:
    """One annotation's descriptors: the shape it is drawn as, and the axes of the space its collection draws in."""
    system = annotation.collection.coordinate_system_or_none
    axis_types = [axis.type for axis in system.axes.all()] if system is not None else []
    return {ANNOTATION_KIND: str(annotation.kind).upper(), **axis_count_descriptors(axis_types, GEOMETRY_AXIS_TYPES)}


def lens_descriptors(lens: models.Lens) -> Descriptors:
    """A lens' descriptors: its container's vocabulary, computed over what it selects."""
    if isinstance(lens, models.ArrayLens):
        return array_lens_descriptors(lens)
    if isinstance(lens, models.TableLens):
        return table_descriptors(lens.table_dataset, is_whole=lens.is_whole, pinned=coords_logic.pinned_axes(lens.windows_list))
    if isinstance(lens, models.SparseLens):
        return sparse_descriptors(lens.sparse_dataset, is_whole=lens.is_whole, windows=lens.windows_list)
    if isinstance(lens, models.MeshLens):
        return mesh_descriptors(lens.mesh_collection, is_whole=lens.is_whole)
    if isinstance(lens, models.NetworkLens):
        return network_descriptors(lens.network_collection, is_whole=lens.is_whole)
    if isinstance(lens, models.CadModelLens):
        return cad_model_descriptors(lens.cad_model, is_whole=lens.is_whole)
    if isinstance(lens, models.AnnotationLens):
        return annotation_descriptors(lens.annotation_collection, lens.windows_list, is_whole=lens.is_whole)
    return {}


def file_descriptors(file: models.File) -> Descriptors:
    """A file's descriptors: its media type, its extension and its size -- what an importer is picked by."""
    return {"@mikro/content_type": file.content_type or "", "@mikro/extension": file_extension(file.name), "@mikro/size_bytes": file.size or 0}


#: Extensions of two parts, which name a format the last part alone does not.
DOUBLE_EXTENSIONS = ("ome.tif", "ome.tiff", "ome.zarr", "ome.btf", "tar.gz", "nii.gz")


def file_extension(name: str) -> str:
    """A file name's extension, lowercased and without the dot: ``ome.tif`` for ``Cells.OME.TIF``, empty for none."""
    base = name.rsplit("/", 1)[-1].lower()
    for double in DOUBLE_EXTENSIONS:
        if base.endswith(f".{double}"):
            return double
    stem, dot, extension = base.rpartition(".")
    return extension if dot and stem else ""


def coordinate_system_descriptors(system: models.CoordinateSystem) -> Descriptors:
    """A space's descriptors: how many axes of each type it has. Its axes are written after it, by writers that restore it."""
    return axis_count_descriptors([axis.type for axis in system.axes.all()], tuple(KEY_BY_AXIS_TYPE))


def transformation_descriptors(edge: models.Transformation) -> Descriptors:
    """An edge's descriptors: the kind of map, how it is known, what it did to values, and the rank of the spaces it joins."""
    return {
        "@mikro/transform_kind": str(edge.kind),
        "@mikro/validity": str(edge.validity),
        "@mikro/value_relation": str(edge.value_relation or ""),
        "@mikro/n_input_axes": edge.input.axes.count(),
        "@mikro/n_output_axes": edge.output.axes.count(),
    }


def coordinate_anchor_descriptors(anchor: models.CoordinateAnchor) -> Descriptors:
    """An anchor's descriptors: the kind of container it is pinned into."""
    kind = "ARRAY" if anchor.dataset_id else "TABLE" if anchor.table_id else "SPARSE" if anchor.sparse_id else ""
    return {CONTAINER_KIND: kind}


def file_link_descriptors(link: models.FileLink) -> Descriptors:
    """A file link's descriptors: which side was made from which, and the kind of container on the other side."""
    kind = "ARRAY" if link.dataset_id else "TABLE" if link.table_dataset_id else "MESH" if link.mesh_collection_id else "ANNOTATION" if link.annotation_collection_id else ""
    return {"@mikro/link_direction": str(link.direction), CONTAINER_KIND: kind}


def scene_descriptors(scene: models.Scene) -> Descriptors:
    """A scene's descriptors: how its layers are blended and the view it asks to be opened in."""
    return {"@mikro/blending": str(scene.blending), "@mikro/preferred_view": str(scene.preferred_view)}


# --- restoring what a save cannot see -------------------------------------------


def restore(target: object, *, emit: bool = True) -> None:
    """Store the descriptors of a row, or of every row of a queryset, again: for what was written after it.

    Inside the transaction that created the row this announces nothing more -- its CREATED reads
    what is stored at the commit; on a row made before, a change is announced as UPDATED, unless
    ``emit`` is False (a setup job, which runs while the service is stopped).
    """
    from mikro_server.service import service  # it binds the models this module describes

    service.restore(target, emit=emit)


def stamp[M](rows: list[M]) -> list[M]:
    """Describe rows about to be bulk-written, which no save sees."""
    from mikro_server.service import service  # it binds the models this module describes

    return service.stamp(rows)


def restore_value_kind(system_ids: Sequence[int]) -> None:
    """Restore the datasets whose own grid is one of these spaces, and their array lenses: a derivation edge leaving them was drawn, changed or removed."""
    if not system_ids:
        return
    dataset_ids = list(models.ArrayDataset.objects.filter(coordinate_system_id__in=system_ids).values_list("pk", flat=True))
    if dataset_ids:
        restore(models.ArrayDataset.objects.filter(pk__in=dataset_ids).order_by("pk"))
        restore(models.ArrayLens.objects.filter(dataset_id__in=dataset_ids).order_by("pk"))


def restore_annotation_counts(collection_ids: Sequence[int]) -> None:
    """Restore these annotation collections and their lenses: shapes were drawn into them, moved or removed."""
    if not collection_ids:
        return
    restore(models.AnnotationCollection.objects.filter(pk__in=collection_ids).order_by("pk"))
    restore(models.AnnotationLens.objects.filter(annotation_collection_id__in=collection_ids).order_by("pk"))
