import kante
import strawberry
import strawberry_django
from django.db.models import Q, QuerySet
from kante.types import Info
from strawberry import auto
from strawberry_django.filters import FilterLookup
from typing import Optional

from core.filters import REQUIRES_DESCRIPTION, CreatedAtFilterMixin, IdsFilterMixin
from core.logic import descriptor_filter
from core.logic.descriptor_filter import DescriptorConstraintInput
from grid import models


@kante.filter_type(models.Grid)
class GridFilter(IdsFilterMixin, CreatedAtFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]

    @kante.filter_field(description="Search by name (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})

    @kante.filter_field(description="Filter to the grids laid out over this coordinate system")
    def coordinate_system(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}world_id": value})

    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.Grid, queryset, value, prefix)


@kante.filter_type(models.GridLayer)
class GridLayerFilter(IdsFilterMixin):
    id: auto
    kind: auto

    @kante.filter_field(description="Filter by the grid this layer is drawn in")
    def grid(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}grid_id": value})


@strawberry_django.order_type(models.Grid)
class GridOrder:
    created_at: auto
    name: auto
    id: auto


@strawberry_django.order_type(models.GridLayer)
class GridLayerOrder:
    order: auto
    id: auto
