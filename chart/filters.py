import kante
import strawberry
import strawberry_django
from django.db.models import Q
from kante.types import Info
from strawberry import auto
from strawberry_django.filters import FilterLookup
from typing import Optional

from chart import models
from core.filters import IdsFilterMixin


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
