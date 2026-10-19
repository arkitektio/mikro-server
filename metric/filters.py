from typing import Optional

import kante
import strawberry
import strawberry_django
from django.db.models import Q, QuerySet
from kante.types import Info
from strawberry import auto
from strawberry_django.filters import FilterLookup

from core.filters import REQUIRES_DESCRIPTION, IdsFilterMixin
from core.logic import descriptor_filter
from core.logic.descriptor_filter import DescriptorConstraintInput
from metric import models


@strawberry.input(description="An object metrics are about: its structure and id, as a signal spells them")
class MetricTargetFilter:
    identifier: str = strawberry.field(description="@mikro/annotation, @mikro/arraydataset, @mikro/tabledataset or @mikro/sparsedataset")
    object: strawberry.ID = strawberry.field(description="The id of the object within its structure")


@kante.filter_type(models.Metric)
class MetricFilter(IdsFilterMixin):
    id: auto
    key: Optional[FilterLookup[str]]

    @kante.filter_field(description="Filter to the metrics about objects of this structure: @mikro/annotation, @mikro/arraydataset, @mikro/tabledataset or @mikro/sparsedataset")
    def target_identifier(self, info: Info, value: str, prefix: str) -> Q:
        field = models.TARGET_FIELDS.get(value)
        if field is None:
            raise ValueError(f"A metric is about one of {', '.join(models.TARGET_FIELDS)}, not a {value}")
        return Q(**{f"{prefix}target__{field}__isnull": False})

    @kante.filter_field(description="Filter to the metrics about one object")
    def target(self, info: Info, value: MetricTargetFilter, prefix: str) -> Q:
        field = models.TARGET_FIELDS.get(value.identifier)
        if field is None:
            raise ValueError(f"A metric is about one of {', '.join(models.TARGET_FIELDS)}, not a {value.identifier}")
        return Q(**{f"{prefix}target__{field}_id": value.object})

    @kante.filter_field(description="Filter to the metrics correcting nothing, or only to corrections")
    def is_correction(self, info: Info, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}supersedes__isnull": not value})

    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.Metric, queryset, value, prefix)


@strawberry_django.order_type(models.Metric)
class MetricOrder:
    created_at: auto
    key: auto
    id: auto
