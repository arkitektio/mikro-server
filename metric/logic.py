"""Recording metrics: each write is one transaction with its feed entries.

The feed (``arkitekt_service.feed``, ``@mikro/metric``) is what a reader -- kraph -- relies on: an
entry is written in the transaction that writes the metric, so a rolled-back write leaves none
and a committed one always has one. The signal a single write sends (``service.py``) only
nudges; a bulk write sends none and is found in the feed all the same.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from arkitekt_service.contract import JSON
from arkitekt_service.feed.api import Change, Entry, record_change, record_changes
from django.core.exceptions import ValidationError
from django.db import transaction
from kante.types import Info
from kanne_server.scalars import parse_unit

from core import models as core_models
from core.creation import CreationContext
from core.scoping import for_org
from metric import models

#: The feed every metric change is recorded in, named as the structure.
FEED = "@mikro/metric"

#: The most metrics one call may record: one transaction, and the feed waits for it to commit.
MAX_BATCH = 5000

#: ``<namespace>/<name>``: the producer's namespace (an app's, or ``user:<sub>``), then the name.
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*/[A-Za-z0-9][A-Za-z0-9_.\-]*$")

TARGET_MODELS = {
    "@mikro/annotation": core_models.Annotation,
    "@mikro/arraydataset": core_models.ArrayDataset,
    "@mikro/tabledataset": core_models.TableDataset,
    "@mikro/sparsedataset": core_models.SparseDataset,
}


@dataclass(frozen=True)
class MetricSpec:
    """One metric to record, as a client states it."""

    identifier: str
    object: str
    key: str
    magnitude: float | None = None
    unit: str | None = None
    code: str | None = None
    supersedes: str | None = None


def check(spec: MetricSpec) -> MetricSpec:
    """The spec, its unit as pint reads it; ``ValueError`` in prose otherwise."""
    if spec.identifier not in TARGET_MODELS:
        raise ValueError(f"A metric is about one of {', '.join(TARGET_MODELS)}, not a {spec.identifier}")
    if not KEY.match(spec.key):
        raise ValueError(f"A metric key is '<namespace>/<name>' (`ais_suite.measure_profile/threshold_distance`, `user:<sub>/spine_count`), not {spec.key!r}")
    is_quantity = spec.magnitude is not None or spec.unit is not None
    if is_quantity == (spec.code is not None):
        raise ValueError(f"A metric is a quantity (magnitude and unit) or a code, exactly one: {spec.key!r} gives {'both' if is_quantity else 'neither'}")
    if is_quantity:
        if spec.magnitude is None or spec.unit is None:
            raise ValueError(f"A quantity has a magnitude and a unit: {spec.key!r} lacks its {'unit' if spec.unit is None else 'magnitude'}")
        return MetricSpec(spec.identifier, spec.object, spec.key, spec.magnitude, parse_unit(spec.unit), None, spec.supersedes)
    if not spec.code:
        raise ValueError(f"A code is not empty: {spec.key!r}")
    return spec


def _targets(info: Info, ctx: CreationContext, specs: Sequence[MetricSpec]) -> dict[tuple[str, str], models.MetricTarget]:
    """The target of every spec, made where it is the first metric about its object.

    Each object must be one of the caller's organization. One query per structure named, one
    insert for the targets that are new.
    """
    wanted: dict[str, set[str]] = defaultdict(set)
    for spec in specs:
        wanted[spec.identifier].add(spec.object)

    targets: dict[tuple[str, str], models.MetricTarget] = {}
    for identifier, objects in wanted.items():
        model = TARGET_MODELS[identifier]
        field = models.TARGET_FIELDS[identifier]
        try:
            found = {str(pk) for pk in for_org(model, info).filter(pk__in=list(objects)).values_list("pk", flat=True)}
        except (ValueError, ValidationError) as error:  # a malformed id (an annotation's is a uuid)
            raise ValueError(f"Not ids of a {identifier}: {sorted(objects)[:5]}") from error
        missing = sorted(objects - found)
        if missing:
            raise ValueError(f"No {identifier} {', '.join(missing[:10])} in this organization")
        existing = {str(getattr(t, f"{field}_id")): t for t in models.MetricTarget.objects.filter(**{f"{field}_id__in": list(found)})}
        new = [models.MetricTarget(organization_id=ctx.organization.id, **{f"{field}_id": obj}) for obj in found if obj not in existing]
        if new:
            # A concurrent first metric about the same object made it already: kept, and read back.
            models.MetricTarget.objects.bulk_create(new, ignore_conflicts=True)
            existing = {str(getattr(t, f"{field}_id")): t for t in models.MetricTarget.objects.filter(**{f"{field}_id__in": list(found)})}
        for obj, target in existing.items():
            targets[(identifier, obj)] = target
    return targets


def _superseded(info: Info, specs: Sequence[MetricSpec], targets: dict[tuple[str, str], models.MetricTarget]) -> dict[str, models.Metric]:
    """The metrics the specs correct: each of this organization, about the same object."""
    ids = {spec.supersedes for spec in specs if spec.supersedes}
    if not ids:
        return {}
    found = {str(m.pk): m for m in for_org(models.Metric, info).filter(pk__in=list(ids))}
    for spec in specs:
        if spec.supersedes is None:
            continue
        old = found.get(spec.supersedes)
        if old is None:
            raise ValueError(f"No metric {spec.supersedes} in this organization to supersede")
        if old.target_id != targets[(spec.identifier, spec.object)].pk:
            raise ValueError(f"Metric {spec.supersedes} is about another object: a correction is about what it corrects")
    return found


def _producer(info: Info) -> dict[str, str | None]:
    """Who recorded it: the app and release of the request's client, or nothing when there is none."""
    try:
        client = info.context.request.client
    except ValueError:
        return {"producer_app": None, "producer_version": None, "producer_client": None}
    release = getattr(client, "release", None)
    return {
        "producer_app": release.app.identifier if release is not None else None,
        "producer_version": release.version if release is not None else None,
        "producer_client": client.client_id,
    }


def _rows(info: Info, specs: Sequence[MetricSpec]) -> list[models.Metric]:
    specs = [check(spec) for spec in specs]
    ctx = CreationContext.from_info(info)
    targets = _targets(info, ctx, specs)
    superseded = _superseded(info, specs, targets)
    producer = _producer(info)
    return [
        models.Metric(
            organization_id=ctx.organization.id,
            target=targets[(spec.identifier, spec.object)],
            key=spec.key,
            magnitude=spec.magnitude,
            unit=spec.unit,
            code=spec.code,
            supersedes=superseded.get(spec.supersedes) if spec.supersedes else None,
            creator=ctx.user,
            **producer,
            **ctx.provenance_kwargs(),
        )
        for spec in specs
    ]


def describe(metric: models.Metric) -> dict[str, str | float]:
    """A metric's descriptors: its key, its value, and the object it is about (read off the target's key columns)."""
    value: dict[str, str | float] = {"@mikro/magnitude": metric.magnitude, "@mikro/unit": metric.unit} if metric.code is None else {"@mikro/metric_code": metric.code}
    return {"@mikro/metric_key": metric.key, **value, "@mikro/target_identifier": metric.target.identifier, "@mikro/target_object": metric.target.object}


def _change(metric: models.Metric, kind: str, organization: str) -> Change:
    return Change(FEED, kind, str(metric.pk), organization, uuid=metric.uuid)


def create_metric(info: Info, spec: MetricSpec) -> models.Metric:
    """Record one metric. Its CREATED signal goes out after the commit (``service.py``)."""
    (row,) = _rows(info, [spec])
    with transaction.atomic():
        row.save()
        record_change(_change(row, "CREATED", info.context.request.organization.identifier))
    return row


def create_metrics(info: Info, specs: Sequence[MetricSpec]) -> list[models.Metric]:
    """Record many metrics in one transaction. Announces nothing: a reader finds them in the feed."""
    if not specs:
        return []
    if len(specs) > MAX_BATCH:
        raise ValueError(f"At most {MAX_BATCH} metrics per call, not {len(specs)}: a call is one transaction, and the feed waits for it")
    from mikro_server.service import service

    rows = _rows(info, specs)
    with transaction.atomic():
        # Described before the insert: a metric's descriptors are its own columns and its target's.
        created = models.Metric.objects.bulk_create(service.stamp(rows), batch_size=1000)
        organization = info.context.request.organization.identifier
        record_changes([_change(metric, "CREATED", organization) for metric in created])
    return created


def delete_metric(info: Info, id: str) -> str:
    """Delete a metric: a DELETED entry in the feed, and a DELETED signal after the commit.

    A metric a correction supersedes is protected while the correction stands.
    """
    metric = for_org(models.Metric, info).select_related("organization").get(pk=id)
    with transaction.atomic():
        record_change(_change(metric, "DELETED", metric.organization.identifier))
        metric.delete()
    return id


# --- the feed --------------------------------------------------------------------------------


def render(entries: list[Entry]) -> dict[str, JSON]:
    """What a page of ``@mikro/metric`` entries says about each metric still there, by id.

    A metric deleted since its CREATED was written has no body: its DELETED entry follows.
    """
    ids = {e.object for e in entries if e.kind != "DELETED"}
    if not ids:
        return {}
    metrics = models.Metric.objects.filter(pk__in=list(ids)).select_related(
        "target", "creator", "created_through", "supersedes"
    )
    return {str(m.pk): _body(m) for m in metrics}


def _body(metric: models.Metric) -> JSON:
    task = metric.created_through
    return {
        "target": {"identifier": metric.target.identifier, "object": metric.target.object, "uuid": str(metric.target.uuid)},
        "key": metric.key,
        "magnitude": metric.magnitude,
        "unit": metric.unit,
        "code": metric.code,
        "producer": {
            "app": metric.producer_app,
            "version": metric.producer_version,
            "client": metric.producer_client,
            "user": metric.creator.sub if metric.creator is not None else None,
            "issuer": metric.creator.iss if metric.creator is not None else None,
        },
        "task": (
            {"task_id": task.task_id, "root_task_id": task.root_task_id, "parent_task_id": task.parent_task_id, "agent_sub": task.agent_sub, "agent_client_id": task.agent_client_id, "caller_sub": task.caller_sub, "assigner_sub": task.assigner_sub}
            if task is not None
            else None
        ),
        "supersedes": str(metric.supersedes.uuid) if metric.supersedes is not None else None,
        "created_at": metric.created_at.isoformat(),
    }
