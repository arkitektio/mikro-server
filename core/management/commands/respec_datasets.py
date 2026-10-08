"""Rewrite the stored spec of the datasets created before an axis of one position stopped counting.

A dataset's spec used to be read off its axes alone, so a stack stored with a single plane was
a VOLUME and a single frame a TIMESERIES. It is now read off the axes and the level-0 shape
together (:func:`core.logic.coords.specs_for_axes`), and every dataset created from then on is
written that way. The rows written before keep the old answer until this is run.

**A command and not a data migration**: it rewrites existing data once, which a migration is
not for -- `migrate` runs for every build and must leave a schema the previous release still
runs on. Safe to run again: a dataset whose spec already agrees is not written.

A dataset with no level-0 array, or whose shape and axes disagree in rank, is left as it is
and counted: there is nothing to read its extents from.

Written with ``update`` rather than ``save``: this corrects a derived column, it is not an
edit of the dataset, and it must not enter its history as one.
"""

from django.core.management.base import BaseCommand

from core import models
from core.logic import coords as coords_logic


class Command(BaseCommand):
    help = "Recompute every array dataset's spec from its axes and its level-0 shape."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change, without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        shapes = dict(models.DataArray.objects.filter(level=0).values_list("dataset_id", "shape"))
        corrected = 0
        unreadable = 0
        for dataset in models.ArrayDataset.objects.exclude(coordinate_system=None).order_by("pk").iterator():
            shape = shapes.get(dataset.pk)
            axes = [
                coords_logic.AxisSpec(name=name, type=axis_type)
                for name, axis_type in models.Axis.objects.filter(coordinate_system_id=dataset.coordinate_system_id).order_by("order").values_list("name", "type")
            ]
            if not isinstance(shape, list) or len(shape) != len(axes):
                unreadable += 1
                continue

            spec = [member.value for member in coords_logic.specs_for_axes(axes, shape)]
            if spec == dataset.stored_spec:
                continue
            corrected += 1
            self.stdout.write(f"  dataset {dataset.pk} ({dataset.name}): {dataset.stored_spec} -> {spec}")
            if not dry_run:
                models.ArrayDataset.objects.filter(pk=dataset.pk).update(stored_spec=spec)

        verb = "would be corrected" if dry_run else "corrected"
        self.stdout.write(self.style.SUCCESS(f"{corrected} dataset(s) {verb}; {unreadable} with no level-0 shape to read left as they are."))
