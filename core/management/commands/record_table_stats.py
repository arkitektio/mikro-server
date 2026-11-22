"""Read the parquet footer of every table that has no row count yet.

`createTableDataset` reads a table's footer as it makes it (`core.logic.table_stats`); a table
made before that, or one whose parquet could not be read at that moment, has none. This reads
them: a footer per table, never a row. **A job, not a data migration**: it does S3 and DuckDB
I/O, which `migrate` must not depend on. Safe beside a serving release and safe to run again --
a table that has its count is skipped, and one that still cannot be read is counted and left.
"""

from django.core.management.base import BaseCommand

from core import models
from core.logic import table_stats


class Command(BaseCommand):
    help = "Read the row count and per-column range and null count off the parquet footer of every table that has none."

    def handle(self, *args: object, **options: object) -> None:
        pending = models.TableDataset.objects.filter(row_count__isnull=True, store__populated=True).select_related("store").order_by("pk")
        total = pending.count()
        if not total:
            self.stdout.write(self.style.SUCCESS("Every table records its footer."))
            return
        recorded = sum(1 for table in pending.iterator() if table_stats.record_footer(table))
        self.stdout.write(self.style.SUCCESS(f"Read the footer of {recorded} of {total} table(s)."))
        if recorded < total:
            self.stdout.write(self.style.WARNING(f"{total - recorded} table(s) could not be read and keep no row count; run this again once their parquet is reachable."))
