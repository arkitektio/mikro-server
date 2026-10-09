"""A lens selects over any container, one row per selection, held to it by the database.

`Lens` becomes a polymorphic base with one child table per container kind; every row written
so far was an array lens and becomes an `ArrayLens`. Before the unique constraint lands, the
rows written under the old rule -- a lens per scene bootstrap, a lens per `createLens` call,
slices spelled as the caller spelled them -- are folded onto one row per (dataset, normalized
slices), repointing the layers that drew through the duplicates. A sliced duplicate's own space
goes with it when nothing else is attached to that space; otherwise the space stays, as a bare
space with the edge it had.

Database only, so a migration rather than a job: the one piece of service code it reads is
`core.logic.coords.normalize_slices`, a pure function that defines the stored spelling.
"""

from __future__ import annotations

import json

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q

from core.logic.coords import lens_to_parent, normalize_slices


class _Slice:
    def __init__(self, entry: dict) -> None:
        self.axis = entry.get("axis")
        self.start = entry.get("start")
        self.stop = entry.get("stop")
        self.step = entry.get("step")


def _dataset_facts(apps, dataset_id: int) -> tuple[list[int], list[str]]:
    """The level-0 shape and the axis names of a dataset, from the historical rows."""
    ArrayDataset = apps.get_model("core", "ArrayDataset")
    DataArray = apps.get_model("core", "DataArray")
    Axis = apps.get_model("core", "Axis")
    dataset = ArrayDataset.objects.get(pk=dataset_id)
    base = DataArray.objects.filter(dataset_id=dataset_id).order_by("level").first()
    shape = base.shape if base is not None and isinstance(base.shape, list) else []
    names = list(Axis.objects.filter(coordinate_system_id=dataset.coordinate_system_id).order_by("order").values_list("name", flat=True)) if dataset.coordinate_system_id else []
    return shape, names


def _system_is_private(apps, system_id: int, own_edge_ids: set[int]) -> bool:
    """Whether a sliced lens' space has nothing attached but the lens itself and its own edge."""
    Transformation = apps.get_model("core", "Transformation")
    for app_label, model_name in (
        ("core", "ArrayDataset"),
        ("core", "DataArray"),
        ("core", "MeshCollection"),
        ("core", "NetworkCollection"),
        ("core", "TableDataset"),
        ("core", "AnnotationCollection"),
        ("core", "SparseDataset"),
    ):
        if apps.get_model(app_label, model_name).objects.filter(coordinate_system_id=system_id).exists():
            return False
    if apps.get_model("core", "Lens").objects.filter(coordinate_system_id=system_id).count() > 1:
        return False
    if apps.get_model("core", "Scene").objects.filter(world_id=system_id).exists() or apps.get_model("chart", "Chart").objects.filter(world_id=system_id).exists():
        return False
    if Transformation.objects.filter(Q(input_id=system_id) | Q(output_id=system_id), parent__isnull=True).exclude(pk__in=own_edge_ids).exists():
        return False
    if Transformation.objects.filter(field_id=system_id).exists() or apps.get_model("core", "AnnotationCollection").objects.filter(bbox_system_id=system_id).exists():
        return False
    return True


def fold_and_convert(apps, schema_editor) -> None:
    """Normalize, fold duplicates, then make every lens an array lens with its organization."""
    Lens = apps.get_model("core", "Lens")
    ArrayLens = apps.get_model("core", "ArrayLens")
    ArrayDataset = apps.get_model("core", "ArrayDataset")
    Layer = apps.get_model("core", "Layer")
    ChartLayer = apps.get_model("chart", "ChartLayer")
    Transformation = apps.get_model("core", "Transformation")
    CoordinateSystem = apps.get_model("core", "CoordinateSystem")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # The content type rows are written by `post_migrate`, so on a fresh database they are not
    # there yet: make the one this migration needs.
    array_ctype, _ = ContentType.objects.get_or_create(app_label="core", model="arraylens")

    for dataset_id in Lens.objects.order_by().values_list("dataset_id", flat=True).distinct():
        dataset = ArrayDataset.objects.get(pk=dataset_id)
        shape, names = _dataset_facts(apps, dataset_id)
        intrinsic_id = dataset.coordinate_system_id

        groups: dict[str, list] = {}
        for lens in Lens.objects.filter(dataset_id=dataset_id).order_by("pk"):
            stored = lens.slices if isinstance(lens.slices, list) else []
            try:
                normalized = normalize_slices(shape, names, [_Slice(entry) for entry in stored]) if names and len(names) == len(shape) else stored
            except ValueError:
                normalized = stored
            if normalized != stored:
                lens.slices = normalized
                lens.save(update_fields=["slices"])
                # The lens' own edge was written from the spelling; it is derived from the slices.
                if lens.coordinate_system_id and lens.coordinate_system_id != intrinsic_id:
                    kind, params = lens_to_parent(names, [_Slice(entry) for entry in normalized])
                    Transformation.objects.filter(input_id=lens.coordinate_system_id, output_id=intrinsic_id, parent__isnull=True, kind=kind).exclude(params=params).update(params=params)
            if not normalized and lens.coordinate_system_id is None:
                lens.coordinate_system_id = intrinsic_id
                lens.save(update_fields=["coordinate_system"])
            groups.setdefault(json.dumps(normalized, sort_keys=True), []).append(lens)

        for group in groups.values():
            keeper, *duplicates = group
            for duplicate in duplicates:
                Layer.objects.filter(lens_id=duplicate.pk).update(lens_id=keeper.pk)
                ChartLayer.objects.filter(lens_id=duplicate.pk).update(lens_id=keeper.pk)
                system_id = duplicate.coordinate_system_id
                own_edges = set(Transformation.objects.filter(input_id=system_id, output_id=intrinsic_id, parent__isnull=True).values_list("pk", flat=True)) if system_id and system_id != intrinsic_id else set()
                duplicate.delete()
                if system_id and system_id != intrinsic_id and _system_is_private(apps, system_id, own_edges):
                    Transformation.objects.filter(pk__in=own_edges).delete()
                    CoordinateSystem.objects.filter(pk=system_id).delete()

    for lens in Lens.objects.order_by("pk").iterator():
        lens.organization_id = ArrayDataset.objects.filter(pk=lens.dataset_id).values_list("organization_id", flat=True).first()
        lens.polymorphic_ctype_id = array_ctype.pk
        lens.save(update_fields=["organization", "polymorphic_ctype"])
        # The child row for an existing parent: built *from* the parent's values, so that the
        # save re-writes the parent with what it already holds and inserts only the child.
        child = ArrayLens(lens_ptr=lens)
        child.__dict__.update({key: value for key, value in lens.__dict__.items() if key != "_state"})
        child.slices = lens.slices if isinstance(lens.slices, list) else []
        child.dataset_id = lens.dataset_id
        child.save()


class Migration(migrations.Migration):
    dependencies = [
        ("authentikate", "0007_user_profile"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("chart", "0001_initial"),
        ("core", "0002_a_spec_counts_only_axes_with_extent"),
    ]

    operations = [
        migrations.AddField(
            model_name="lens",
            name="organization",
            field=models.ForeignKey(null=True, help_text="The organization of the container this lens selects over", on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="authentikate.organization"),
        ),
        migrations.AddField(
            model_name="lens",
            name="polymorphic_ctype",
            field=models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="polymorphic_%(app_label)s.%(class)s_set+", to="contenttypes.contenttype"),
        ),
        migrations.AlterField(
            model_name="lens",
            name="coordinate_system",
            field=models.ForeignKey(blank=True, help_text="The coordinate system this lens' selection is expressed in: a sliced array lens' own crop space, with a stored edge carrying the shift; otherwise the container's own system", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lenses", to="core.coordinatesystem"),
        ),
        migrations.CreateModel(
            name="ArrayLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("slices", models.JSONField(default=list, help_text="The selection this lens makes over its dataset, as a list of per-dimension slices in the one normalized spelling")),
                # Related name set once the base's `dataset` is gone: two reverse accessors of
                # one name on ArrayDataset would clash for the duration of the move.
                ("dataset", models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="core.arraydataset")),
            ],
            bases=("core.lens",),
        ),
        migrations.CreateModel(
            name="TableLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("table_dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.tabledataset")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="SparseLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("sparse_dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.sparsedataset")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="MeshLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("mesh_collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.meshcollection")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="NetworkLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("network_collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.networkcollection")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="AnnotationLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("annotation_collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.annotationcollection")),
            ],
            bases=("core.lens", models.Model),
        ),
        # Nothing to undo: the fold is what the rows always meant, and the children are the rows.
        migrations.RunPython(fold_and_convert, migrations.RunPython.noop),
        migrations.RemoveField(model_name="lens", name="slices"),
        migrations.RemoveField(model_name="lens", name="dataset"),
        migrations.AlterField(
            model_name="arraylens",
            name="dataset",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.arraydataset"),
        ),
        migrations.AlterField(
            model_name="lens",
            name="organization",
            field=models.ForeignKey(help_text="The organization of the container this lens selects over", on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="authentikate.organization"),
        ),
        migrations.DeleteModel(name="HistoricalLens"),
        migrations.AddConstraint(model_name="arraylens", constraint=models.UniqueConstraint(fields=("dataset", "slices"), name="one_array_lens_per_selection")),
        migrations.AddConstraint(model_name="tablelens", constraint=models.UniqueConstraint(fields=("table_dataset", "windows"), name="one_table_lens_per_selection")),
        migrations.AddConstraint(model_name="sparselens", constraint=models.UniqueConstraint(fields=("sparse_dataset", "windows"), name="one_sparse_lens_per_selection")),
        migrations.AddConstraint(model_name="meshlens", constraint=models.UniqueConstraint(fields=("mesh_collection", "windows"), name="one_mesh_lens_per_selection")),
        migrations.AddConstraint(model_name="networklens", constraint=models.UniqueConstraint(fields=("network_collection", "windows"), name="one_network_lens_per_selection")),
        migrations.AddConstraint(model_name="annotationlens", constraint=models.UniqueConstraint(fields=("annotation_collection", "windows"), name="one_annotation_lens_per_selection")),
    ]
