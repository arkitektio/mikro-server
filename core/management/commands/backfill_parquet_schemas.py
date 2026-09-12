"""Record the schema of every parquet store finished before `ParquetStore.columns` existed.

`fill_info` now DESCRIBEs the object and stores what it declares, so a store finished from here
on knows its own columns. Every store written before that has `columns = NULL`, and while nothing
*breaks* -- `core.logic.tables.columns_for_store` falls back to describing on demand, and every
read path goes through the `Column` rows rather than the store -- the fallback is a live S3 round
trip on a request path, which is exactly what recording it was meant to remove.

**A command and not a data migration, deliberately.** This does S3 and DuckDB I/O. A migration
doing that fails `migrate` on any machine without datalayer credentials -- CI, a fresh checkout,
a colleague's laptop -- and holds a transaction open for as long as the object store takes. The
same reason `backfill_default_scenes` is a command.

Log-and-continue: a store whose bytes have been deleted, or whose object is not readable as
parquet, stays NULL and is counted. It renders exactly as it did before; it simply cannot be
described. The count of what remains is printed on every run, so the finish line is a number
rather than a memory.
"""

from django.core.management.base import BaseCommand

from datalayer.datalayer import Datalayer
from datalayer.models import ParquetStore


class Command(BaseCommand):
    help = "Read and record the column schema of parquet stores finished before the field existed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Describe the stores and report, without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        datalayer = Datalayer()

        pending = ParquetStore.objects.filter(populated=True, columns__isnull=True).order_by("pk")
        total = pending.count()
        if not total:
            self.stdout.write(self.style.SUCCESS("Every finished parquet store already records its schema."))
            return

        self.stdout.write(f"{total} finished parquet store(s) with no recorded schema.")

        described = 0
        failed = 0
        # `.iterator()` so a deployment with many stores does not load them all; each is a
        # network round trip anyway, so the query is never the expensive part.
        for store in pending.iterator():
            try:
                columns = datalayer.get_parquet_schema(store)
            except Exception as error:  # noqa: BLE001 - one unreadable store must not stop the rest
                failed += 1
                self.stderr.write(self.style.WARNING(f"  store {store.pk} ({store.key}): could not describe -- {error}"))
                continue

            described += 1
            if dry_run:
                self.stdout.write(f"  store {store.pk} ({store.key}): {len(columns)} column(s) -- {', '.join(column.name for column in columns)}")
                continue

            store.columns = [column.model_dump() for column in columns]
            store.save(update_fields=["columns"])

        verb = "would record" if dry_run else "recorded"
        self.stdout.write(self.style.SUCCESS(f"{verb} the schema of {described} store(s)."))
        if failed:
            self.stdout.write(
                self.style.WARNING(
                    f"{failed} store(s) could not be described and remain unrecorded. They keep working -- "
                    "`columns_for_store` describes on demand -- but each one costs an S3 round trip when it is read."
                )
            )
