"""Delete the S3 objects behind stores whose data was deleted, after a grace period.

Deleting a `File` or an `ArrayDataset` does no S3 work -- a request must never block on a zarr with
a hundred thousand chunks, and bytes must never be destroyed inside a transaction that might
roll back. The delete flags the stores it orphaned; this collects them.

**Only ever purge rows with `orphaned_at` set.** Sweeping every *unreferenced* store would be
simpler and self-healing, and it would destroy live uploads: an upload grant creates the store
row before the client has uploaded and long before any data row attaches, so "unreferenced" is
the normal state of an upload in flight, not a sign of garbage. The flag is what separates
"nothing wants this any more" from "nothing wants this yet".

Run it from cron. Nothing is freed until it runs.
"""

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.logic import storage
from datalayer.models import DatalayerStore

#: Used when `DATALAYER_STORE_GRACE_DAYS` is unset. A week is long enough that an accidental
#: `deleteArrayDataset` is noticed and recoverable, and short enough that storage is not paid for
#: indefinitely.
DEFAULT_GRACE_DAYS = 7


class Command(BaseCommand):
    """Purge the S3 objects behind stores that have been orphaned longer than the grace period."""

    help = "Delete S3 objects for stores orphaned longer than the grace period, and their rows."

    def add_arguments(self, parser) -> None:  # noqa: ANN001 - Django's ArgumentParser
        """Declare the command's options."""
        parser.add_argument("--dry-run", action="store_true", help="Report what would be purged without deleting anything.")
        parser.add_argument("--older-than", type=int, default=None, metavar="DAYS", help=f"Grace period in days, overriding DATALAYER_STORE_GRACE_DAYS (default {DEFAULT_GRACE_DAYS}).")
        parser.add_argument("--limit", type=int, default=None, metavar="N", help="Purge at most N stores this run.")

    def handle(self, *args, **options) -> None:
        """Purge every eligible store, re-checking each for referrers first."""
        grace_days = options["older_than"] if options["older_than"] is not None else getattr(settings, "DATALAYER_STORE_GRACE_DAYS", DEFAULT_GRACE_DAYS)
        cutoff = timezone.now() - datetime.timedelta(days=grace_days)
        dry_run = options["dry_run"]

        candidates = DatalayerStore.objects.filter(orphaned_at__isnull=False, orphaned_at__lt=cutoff).order_by("orphaned_at")
        if options["limit"]:
            candidates = candidates[: options["limit"]]

        purged = objects_deleted = revived = failed = 0

        # The manager is polymorphic, so each row arrives as its real subclass and `is_prefix`
        # can be read straight off it -- a zarr knows to list-and-batch, everything else does
        # not have to care.
        for store in candidates:
            referrers = storage.referrers_of(store)
            if referrers:
                # Re-attached, or shared with a row that outlived the deleted one. The flag was
                # only ever a candidate marker; this is the check that makes it safe.
                described = ", ".join(f"{name} x{count}" for name, count in referrers)
                self.stdout.write(f"keep    {store.bucket}/{store.key} -- still referenced by {described}")
                revived += 1
                if not dry_run:
                    DatalayerStore.objects.filter(pk=store.pk).update(orphaned_at=None)
                continue

            kind = "prefix" if store.is_prefix else "object"
            if dry_run:
                self.stdout.write(f"would purge {kind} {store.bucket}/{store.key} (orphaned {store.orphaned_at:%Y-%m-%d})")
                purged += 1
                continue

            try:
                # Bytes first, then the row. Bytes gone with the row left behind is retried
                # harmlessly next run; the row gone with bytes left is a leak nothing points at.
                count = store.purge_bytes()
                store_pk, bucket, key = store.pk, store.bucket, store.key
                DatalayerStore.objects.filter(pk=store_pk).delete()
            except Exception as error:  # noqa: BLE001 - one bad store must not stop the sweep
                self.stderr.write(f"FAILED  {store.bucket}/{store.key}: {error}")
                failed += 1
                continue

            self.stdout.write(f"purged  {kind} {bucket}/{key} ({count} object{'s' if count != 1 else ''})")
            purged += 1
            objects_deleted += count

        verb = "would purge" if dry_run else "purged"
        self.stdout.write(self.style.SUCCESS(f"{verb} {purged} store(s), {objects_deleted} object(s); kept {revived} still-referenced; {failed} failed"))
        if not purged and not revived:
            self.stdout.write(f"nothing orphaned longer than {grace_days} day(s)")
