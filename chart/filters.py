import kante
import strawberry
import strawberry_django
from django.db.models import Q, QuerySet
from kante.types import Info
from strawberry import auto
from strawberry_django.filters import FilterLookup
from typing import Optional

from chart import models
from core.filters import REQUIRES_DESCRIPTION, IdsFilterMixin
from core.logic import descriptor_filter
from core.logic.descriptor_filter import DescriptorConstraintInput


@kante.filter_type(models.Chart)
class ChartFilter(IdsFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]

    @kante.filter_field(description="Search by name (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})

    @kante.filter_field(description="Filter to the charts laid out over this coordinate system")
    def coordinate_system(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}world_id": value})

    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.Chart, queryset, value, prefix)


@kante.filter_type(models.ChartLayer)
class ChartLayerFilter(IdsFilterMixin):
    id: auto
    kind: auto

    @kante.filter_field(description="Filter by the chart this layer is drawn in")
    def chart(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}chart_id": value})


@strawberry_django.order_type(models.Chart)
class ChartOrder:
    created_at: auto
    name: auto
    id: auto


@strawberry_django.order_type(models.ChartLayer)
class ChartLayerOrder:
    order: auto
    id: auto
