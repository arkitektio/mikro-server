"""Recording metrics: one, many, or deleting one. There is no update: a correction supersedes."""

import kante
import strawberry
from kante.types import Info
from pydantic import BaseModel, Field

from core.input_unions import prose_errors
from metric import logic, types


class MetricTargetInputModel(BaseModel):
    identifier: str
    object: str


@kante.pydantic_input(MetricTargetInputModel, description="The object a metric is about: its structure and id, as rekuest's signals and STRUCTURE ports spell them")
class MetricTargetInput:
    identifier: str = strawberry.field(description="@mikro/annotation, @mikro/arraydataset, @mikro/tabledataset or @mikro/sparsedataset")
    object: strawberry.ID = strawberry.field(description="The id of the object within its structure")


class MetricInputModel(BaseModel):
    target: MetricTargetInputModel
    key: str
    magnitude: float | None = None
    unit: str | None = None
    code: str | None = None
    supersedes: str | None = None

    def spec(self) -> logic.MetricSpec:
        return logic.MetricSpec(self.target.identifier, self.target.object, self.key, self.magnitude, self.unit, self.code, self.supersedes)


_METRIC_FIELDS = dict(
    key="What was measured, namespaced by who measured it: `ais_suite.measure_profile/threshold_distance`, `user:<sub>/spine_count`",
    magnitude="(quantity) The number, in `unit`. Exclusive with `code`",
    unit="(quantity) The unit of `magnitude`, one pint reads (`µm`, `s`, `dimensionless` for a count). Exclusive with `code`",
    code="(code) An opaque category a process emitted (`segmenter_v2/class_3`). A person choosing a category is an assertion, and goes to kraph. Exclusive with `magnitude` and `unit`",
    supersedes="The metric this one corrects, about the same object. The corrected metric stays, and cannot be deleted while its correction stands",
)


@prose_errors
@kante.pydantic_input(MetricInputModel, description="One value about one object: a quantity (magnitude and unit) or a code, exactly one")
class MetricInput:
    target: MetricTargetInput = strawberry.field(description="The object the value is about")
    key: str = strawberry.field(description=_METRIC_FIELDS["key"])
    magnitude: float | None = strawberry.field(default=None, description=_METRIC_FIELDS["magnitude"])
    unit: str | None = strawberry.field(default=None, description=_METRIC_FIELDS["unit"])
    code: str | None = strawberry.field(default=None, description=_METRIC_FIELDS["code"])
    supersedes: strawberry.ID | None = strawberry.field(default=None, description=_METRIC_FIELDS["supersedes"])


class CreateMetricsInputModel(BaseModel):
    metrics: list[MetricInputModel] = Field(max_length=logic.MAX_BATCH)


@prose_errors
@kante.pydantic_input(CreateMetricsInputModel, description=f"Many metrics in one transaction, at most {logic.MAX_BATCH}")
class CreateMetricsInput:
    metrics: list[MetricInput] = strawberry.field(description="The values to record, about any objects of the organization")


class DeleteMetricInputModel(BaseModel):
    id: str


@kante.pydantic_input(DeleteMetricInputModel, description="The metric to delete")
class DeleteMetricInput:
    id: strawberry.ID = strawberry.field(description="The metric to delete")


def create_metric(info: Info, input: MetricInput) -> types.Metric:
    """Record one metric, announced to the hub's rekuest once it is committed."""
    return logic.create_metric(info, input.to_pydantic().spec())


def create_metrics(info: Info, input: CreateMetricsInput) -> list[types.Metric]:
    """Record many metrics in one transaction. Announces none of them: the change feed has them all."""
    return logic.create_metrics(info, [metric.spec() for metric in input.to_pydantic().metrics])


def delete_metric(info: Info, input: DeleteMetricInput) -> strawberry.ID:
    """Delete a metric. One a correction supersedes is kept while the correction stands."""
    return strawberry.ID(logic.delete_metric(info, input.to_pydantic().id))

