import datetime
from collections.abc import Callable
from enum import Enum
from typing import Protocol

import strawberry
import strawberry_django
from django.db.models import Avg, Count, Max, Min, Model, QuerySet, Sum
from django.db.models.functions import (
    TruncDay,
    TruncHour,
    TruncMonth,
    TruncQuarter,
    TruncWeek,
    TruncYear,
)
from django.db.models.functions.datetime import TruncBase
from kante import Info

from core.scoping import for_org

# ---------- Resolver spec ----------
ResolverSpec = dict[str, tuple[Callable[[QuerySet, str], object], object, str]]


class _StatsRoot(Protocol):
    """The instance a generated stats type resolves on: its selection and the per-field aggregate."""

    _qs: QuerySet

    def _get_field_summary(self, mf: str) -> dict[str, object]: ...


# --------- Bucket return type ---------
@strawberry.type
class TimeBucket:
    ts: datetime.datetime  # bucket timestamp
    count: int
    distinctCount: int
    max: float | None
    min: float | None
    avg: float | None
    sum: float | None


@strawberry.enum
class Granularity(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


def create_stats_type(
    model: type[Model],
    *,
    filters: type | None = None,  # strawberry_django filter class for the model
    allowed_fields: dict[str, str]
    | None = None,  # GraphQL name -> model field (string)
    allowed_datetime_fields: dict[str, str]
    | None = None,  # GraphQL name -> DateTimeField (string) for bucketing
    resolvers: ResolverSpec | None = None,
    type_name: str | None = None,
    enum_name: str | None = None,
    dt_enum_name: str | None = None,
) -> tuple[type, Callable[..., object]]:
    """
    Build a Strawberry GraphQL Stats type for `model`.

    - FieldEnum: which numeric-like field to aggregate on.
    - DateTimeFieldEnum: which datetime to bucket by.
    - Scalar stats (count/distinctCount/max/min/avg/sum) each accept `field: FieldEnum`,
      but under the hood we aggregate ALL of them in ONE query per `field` and memoize.
    - series(field:, timestampField:, by:) returns time buckets with per-bucket stats
      via ONE query (GROUP BY truncated timestamp).
    """

    filters_type = filters
    if allowed_fields is None:
        raise ValueError("allowed_fields must be provided")

    # --------- Enums ---------
    enum_name = enum_name or f"{model.__name__}Field"
    FieldEnumPy = Enum(enum_name, {k.upper(): v for k, v in allowed_fields.items()})
    # strawberry.enum mutates FieldEnumPy in place (attaches _enum_definition)
    strawberry.enum(
        FieldEnumPy,
        description=f"Numeric/aggregatable fields of {model.__name__}",
    )  # type: ignore

    if allowed_datetime_fields:
        dt_enum_name = dt_enum_name or f"{model.__name__}TimestampField"
        TimestampEnumPy = Enum(
            dt_enum_name, {k.upper(): v for k, v in allowed_datetime_fields.items()}
        )
        TimestampEnum = strawberry.enum(
            TimestampEnumPy,
            description=f"Datetime fields of {model.__name__} for bucketing",
        )
    else:
        TimestampEnumPy = None
        TimestampEnum = None  # type: ignore

    # --------- Helpers ---------
    def _truncate(dt_field: str, by: Granularity) -> TruncBase:
        return {
            "hour": TruncHour(dt_field),
            "day": TruncDay(dt_field),
            "week": TruncWeek(dt_field),
            "month": TruncMonth(dt_field),
            "quarter": TruncQuarter(dt_field),
            "year": TruncYear(dt_field),
        }[by.value]

    # Default scalar resolver functions (unused directly now; we aggregate once)
    def _all_stats_for_field(qs: QuerySet, mf: str) -> dict[str, object]:
        """
        Return all scalar stats for the model field in ONE aggregate.
        """
        return qs.aggregate(
            distinctCount=Count(mf, distinct=True),
            max=Max(mf),
            min=Min(mf),
            avg=Avg(mf),
            sum=Sum(mf),
            # Note: 'count' shown separately as total rows, not non-null count.
            # If you prefer non-null count for the field, use Count(mf) here.
        )

    # Build resolver spec but each field will read from the same cached aggregate:
    default_resolvers: ResolverSpec = {
        "distinctCount": (
            lambda qs, mf: _all_stats_for_field(qs, mf)["distinctCount"],
            int,
            "Number of distinct values for the field",
        ),
        "max": (
            lambda qs, mf: _all_stats_for_field(qs, mf)["max"],
            float | None,
            "Maximum",
        ),
        "min": (
            lambda qs, mf: _all_stats_for_field(qs, mf)["min"],
            float | None,
            "Minimum",
        ),
        "avg": (
            lambda qs, mf: _all_stats_for_field(qs, mf)["avg"],
            float | None,
            "Average",
        ),
        "sum": (
            lambda qs, mf: _all_stats_for_field(qs, mf)["sum"],
            float | None,
            "Sum",
        ),
    }
    resolvers = {**default_resolvers, **(resolvers or {})}

    # --------- Class body with memoized aggregates ---------
    cls_name = type_name or f"{model.__name__}Stats"
    annotations: dict[str, object] = {
        "_qs": strawberry.Private[QuerySet],
        "_cache": strawberry.Private[
            dict[str, dict[str, object]]
        ],  # per-field aggregate cache
        "count": int,
    }
    body: dict[str, object] = {
        "__annotations__": annotations,
        "__init__": lambda self, **data: setattr(self, "__dict__", data),
        "_qs": None,  # type: ignore
        "_cache": None,  # type: ignore
        "count": strawberry_django.field(
            description="Total number of items in the selection",
            resolver=lambda self: self._qs.count(),
        ),
        "_get_field_summary": lambda self, mf: self._cache.setdefault(
            mf,
            _all_stats_for_field(self._qs, mf),
        ),
    }

    # Attach scalar stat fields that share the same cached aggregate
    for stat_name, (_fn, return_type, desc) in resolvers.items():

        def _make_resolver(stat_key: str) -> Callable[..., object]:
            # No return annotation: strawberry takes the field's type from the class
            # annotation below, which one would override.
            def _resolver(self: _StatsRoot, field: FieldEnumPy):  # noqa: ANN202
                mf: str = field.value
                summary = self._get_field_summary(mf)
                if stat_key == "distinctCount":
                    return summary["distinctCount"]
                elif stat_key in ("max", "min", "avg", "sum"):
                    return summary[stat_key]
                else:
                    # fallback: compute via function if someone extended `resolvers`
                    return resolvers[stat_key][0](self._qs, mf)

            _resolver.__name__ = stat_key
            return _resolver

        annotations[stat_name] = return_type
        body[stat_name] = strawberry_django.field(
            description=desc, resolver=_make_resolver(stat_name)
        )

    # Optional: time-bucketed series
    if TimestampEnum is not None:

        def _series(
            self: _StatsRoot,
            field: FieldEnumPy,
            timestampField: TimestampEnumPy,
            by: Granularity,
        ) -> list[TimeBucket]:
            mf = field.value
            tf = timestampField.value
            trunc = _truncate(tf, by)

            # ONE query with GROUP BY bucket
            qs = (
                self._qs.annotate(bucket=trunc)
                .values("bucket")
                .annotate(
                    count=Count("pk"),
                    distinctCount=Count(mf, distinct=True),
                    max=Max(mf),
                    min=Min(mf),
                    avg=Avg(mf),
                    sum=Sum(mf),
                )
                .order_by("bucket")
            )

            out: list[TimeBucket] = []
            for row in qs:
                out.append(
                    TimeBucket(
                        ts=row["bucket"],
                        count=row["count"],
                        distinctCount=row["distinctCount"],
                        max=row["max"],
                        min=row["min"],
                        avg=row["avg"],
                        sum=row["sum"],
                    )
                )
            return out

        annotations["series"] = list[TimeBucket]
        body["series"] = strawberry_django.field(
            description="Time-bucketed stats over a datetime field.",
            resolver=_series,
        )

    # Create the Python class and wrap with strawberry
    new_type = strawberry.type(type(cls_name, (), body))

    # --------- Root field resolver (fixed filter handling + cache init) ---------
    def resolver(info: Info, filters: object | None = None) -> object:
        # Scope before aggregating: an aggregate over every tenant's rows leaks counts even
        # though it returns no objects. `for_org` is the same seam the single-object getters
        # use, so a model with no path to an organization fails loudly here rather than
        # quietly summing the whole table.
        qs = for_org(model, info)
        if filters_type and filters is not None:
            # `filters` is an instance or dict per strawberry_django usage
            qs = strawberry_django.filters.apply(filters, qs, info)
        # instance with caches
        return new_type(_qs=qs, _cache={})

    # Expose argument types nicely on the callable (optional)
    if filters_type is not None:
        resolver.__annotations__["filters"] = filters_type | None

    return new_type, resolver
