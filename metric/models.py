"""A metric: one value recorded about one of mikro's objects, by an app or a person.

A metric is an **observation** -- what a process computed, what someone counted. What it
*means* (which term it is an instance of, which assertion it supports) is not mikro's to say:
that is kraph's, which reads every metric through the change feed (``metric.feed``). So there is
no free-text label or kind here, only a producer-namespaced ``key``
(``ais_suite.measure_profile/threshold_distance``, ``user:<sub>/spine_count``).

A metric is **immutable**. A correction is a new metric that ``supersedes`` the old one; there is
no update, and the feed knows only CREATED and DELETED.

Its value is either a quantity (``magnitude`` in ``unit``, a unit pint reads) or an opaque code a
process emitted (``segmenter_v2/class_3``). A person choosing a category is an assertion, not an
observation, and goes to kraph.
"""

from authentikate.models import Organization
from arkitekt_service.service.described import Described
from django.contrib.auth import get_user_model
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now


class UuidV7(models.Func):
    """``uuidv7()`` (Postgres 18+): a time-ordered uuid, minted by the database."""

    function = "uuidv7"
    template = "%(function)s()"
    output_field = models.UUIDField()


#: Which structures a metric may be about, and the field of :class:`MetricTarget` naming each.
TARGET_FIELDS = {
    "@mikro/annotation": "annotation",
    "@mikro/arraydataset": "array_dataset",
    "@mikro/tabledataset": "table_dataset",
    "@mikro/sparsedataset": "sparse_dataset",
}


class MetricTarget(models.Model):
    """One object that metrics are recorded about: its structure and id, and a uuid of its own.

    Made the first time a metric names the object, never before: most objects carry no metric,
    and none of their creation paths knows this table. Exactly one of the foreign keys is set,
    and it is PROTECT: an object something was measured about cannot be deleted while the
    measurement stands -- whatever deletes it, a cascade from its collection included.

    The ``uuid`` is what a reader keys the object on; ``identifier`` and ``object`` are its
    readable address, spelled as rekuest's signals and STRUCTURE ports spell them.
    """

    uuid = models.UUIDField(db_default=UuidV7(), unique=True, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="metric_targets")
    annotation = models.OneToOneField("core.Annotation", on_delete=models.PROTECT, null=True, blank=True, related_name="metric_target")
    array_dataset = models.OneToOneField("core.ArrayDataset", on_delete=models.PROTECT, null=True, blank=True, related_name="metric_target")
    table_dataset = models.OneToOneField("core.TableDataset", on_delete=models.PROTECT, null=True, blank=True, related_name="metric_target")
    sparse_dataset = models.OneToOneField("core.SparseDataset", on_delete=models.PROTECT, null=True, blank=True, related_name="metric_target")
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(annotation__isnull=False, array_dataset__isnull=True, table_dataset__isnull=True, sparse_dataset__isnull=True)
                    | Q(annotation__isnull=True, array_dataset__isnull=False, table_dataset__isnull=True, sparse_dataset__isnull=True)
                    | Q(annotation__isnull=True, array_dataset__isnull=True, table_dataset__isnull=False, sparse_dataset__isnull=True)
                    | Q(annotation__isnull=True, array_dataset__isnull=True, table_dataset__isnull=True, sparse_dataset__isnull=False)
                ),
                name="metric_target_is_one_object",
            )
        ]

    @property
    def identifier(self) -> str:
        """The structure of the object: ``@mikro/annotation``."""
        return next(identifier for identifier, field in TARGET_FIELDS.items() if getattr(self, f"{field}_id") is not None)

    @property
    def object(self) -> str:
        """The object's id within its structure, as a signal spells it."""
        return str(getattr(self, f"{TARGET_FIELDS[self.identifier]}_id"))

    def __str__(self) -> str:
        return f"{self.identifier}:{self.object}"


class Metric(Described):
    """One value recorded about one object. Immutable: corrected by a metric that supersedes it."""

    uuid = models.UUIDField(db_default=UuidV7(), unique=True, editable=False, help_text="What a reader keys this metric on: stable across everything but a restore")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="metrics")
    target = models.ForeignKey(MetricTarget, on_delete=models.PROTECT, related_name="metrics", help_text="The object this value is about")
    key = models.CharField(max_length=500, help_text="What was measured, namespaced by who measured it: `ais_suite.measure_profile/threshold_distance`, `user:<sub>/spine_count`")
    magnitude = models.FloatField(null=True, blank=True, help_text="(quantity) The number, in `unit`")
    unit = models.CharField(max_length=200, null=True, blank=True, help_text="(quantity) The unit of `magnitude`, as pint reads it; `dimensionless` for a count")
    code = models.CharField(max_length=500, null=True, blank=True, help_text="(code) An opaque category a process emitted: `segmenter_v2/class_3`")
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        help_text="The metric this one corrects. PROTECT: a corrected metric stays while its correction does",
    )
    producer_app = models.CharField(max_length=1000, null=True, blank=True, help_text="The identifier of the app that recorded it; null when recorded by hand")
    producer_version = models.CharField(max_length=1000, null=True, blank=True, help_text="The release of that app")
    producer_client = models.CharField(max_length=1000, null=True, blank=True, help_text="The OAuth client it was recorded through")
    creator = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name="metrics", help_text="The user it was recorded as")
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this metric was recorded in, if any",
    )
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_through_%(class)ss",
        help_text="Who assigned that task",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(magnitude__isnull=False, unit__isnull=False, code__isnull=True) | Q(magnitude__isnull=True, unit__isnull=True, code__isnull=False)),
                name="metric_is_quantity_or_code",
            )
        ]
        indexes = [
            models.Index(fields=["target", "key"], name="metric_target_key_idx"),
            models.Index(fields=["organization", "key"], name="metric_org_key_idx"),
            GinIndex(fields=["stored_descriptors"], name="metric_desc_gin", opclasses=["jsonb_path_ops"]),
        ]

    def __str__(self) -> str:
        value = self.code if self.code is not None else f"{self.magnitude} {self.unit}"
        return f"{self.key} = {value} ({self.target})"
