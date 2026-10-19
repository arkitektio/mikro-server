"""The metrics' GraphQL types."""

import datetime
from typing import Optional

import kante
import strawberry
from strawberry import auto
from strawberry.scalars import JSON

from core.types._shared import DESCRIPTORS_DESCRIPTION, OrgScoped, resolve_descriptors
from core.types.auth import User
from metric import filters, models

_TARGET_COLUMNS = [f"{field}_id" for field in models.TARGET_FIELDS.values()]


@kante.django_type(models.MetricTarget, description="An object metrics are recorded about: its structure and id, as rekuest's signals and STRUCTURE ports spell them, and a uuid of its own")
class MetricTarget(OrgScoped):
    id: auto

    @kante.django_field(only=_TARGET_COLUMNS, description="The structure of the object: @mikro/annotation, @mikro/arraydataset, @mikro/tabledataset or @mikro/sparsedataset")
    def identifier(self) -> str:
        return self.identifier  # type: ignore[attr-defined]

    @kante.django_field(only=_TARGET_COLUMNS, description="The id of the object within its structure")
    def object(self) -> strawberry.ID:
        return strawberry.ID(self.object)  # type: ignore[attr-defined]

    @kante.django_field(only=["uuid"], description="The object's uuid as a metric target: what a reader of the change feed keys it on")
    def uuid(self) -> str:
        return str(self.uuid)  # type: ignore[attr-defined]


@kante.django_type(
    models.Metric,
    filters=filters.MetricFilter,
    ordering=filters.MetricOrder,
    pagination=True,
    description=(
        "One value recorded about an annotation or a dataset, by an app or a person: a quantity (`magnitude` in `unit`) or a `code` a process emitted. "
        "Immutable: a correction is a new metric that `supersedes` it. What a metric means is not said here: kraph, reading every metric from mikro's change feed, says it"
    ),
)
class Metric(OrgScoped):
    """One recorded value."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, only=["stored_descriptors"], description=DESCRIPTORS_DESCRIPTION)

    id: auto
    key: str = kante.django_field(description="What was measured, namespaced by who measured it: `ais_suite.measure_profile/threshold_distance`, `user:<sub>/spine_count`")
    magnitude: float | None = kante.django_field(description="(quantity) The number, in `unit`")
    unit: str | None = kante.django_field(description="(quantity) The unit of `magnitude`")
    code: str | None = kante.django_field(description="(code) The category a process emitted")
    target: MetricTarget = kante.django_field(description="The object this value is about")
    supersedes: Optional["Metric"] = kante.django_field(description="The metric this one corrects, if any")
    producer_app: str | None = kante.django_field(description="The identifier of the app that recorded it; null when recorded by hand")
    producer_version: str | None = kante.django_field(description="The release of that app")
    creator: User | None = kante.django_field(description="The user it was recorded as")
    created_at: datetime.datetime

    @kante.django_field(only=["uuid"], description="What a reader of the change feed keys this metric on")
    def uuid(self) -> str:
        return str(self.uuid)  # type: ignore[attr-defined]
