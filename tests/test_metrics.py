"""Metrics: recorded with their feed entry in one transaction, announced once when recorded alone,
read by kraph through the change feed.

Signals go to a real local stand-in for rekuest's intake (``test_rekuest_signals.intake``); the
feed is read over HTTP, signed with a key the trust bundle says is kraph's.
"""

import json
import time

import pytest
from arkitekt_service import trust
from arkitekt_service.feed.models import FeedEntry
from asgiref.sync import sync_to_async
from django.test import Client as HttpClient
from joserfc.jwk import OKPKey
from kante.context import HttpContext

from metric import models
from mikro_server.schema import schema
from tests import seed
from tests.test_rekuest_signals import INSTANCE, intake  # noqa: F401  the fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]

CREATE = """
mutation Create($input: MetricInput!) {
  createMetric(input: $input) { id uuid key magnitude unit code target { identifier object uuid } descriptors supersedes { id } }
}
"""
CREATE_MANY = """
mutation CreateMany($input: CreateMetricsInput!) { createMetrics(input: $input) { id key target { identifier object } } }
"""
DELETE = "mutation Delete($input: DeleteMetricInput!) { deleteMetric(input: $input) }"
DELETE_DATASET = "mutation Delete($input: DeleteArrayDatasetInput!) { deleteArrayDataset(input: $input) }"
LIST = """
query List($filters: MetricFilter) { metrics(filters: $filters) { id key target { identifier object } } }
"""
DRAW = """
mutation Draw($input: CreateAnnotationInput!) { createAnnotation(input: $input) { id } }
"""

KRAPH_KEY = OKPKey.generate_key("Ed25519")
STRANGER_KEY = OKPKey.generate_key("Ed25519")


async def run(ctx: HttpContext, query: str, variables: dict) -> dict:
    result = await schema.execute(query, context_value=ctx, variable_values=variables)
    assert not result.errors, result.errors
    return result.data


async def refused(ctx: HttpContext, query: str, variables: dict) -> str:
    result = await schema.execute(query, context_value=ctx, variable_values=variables)
    assert result.errors, result.data
    return result.errors[0].message


def quantity(dataset, key: str = "ais_suite.measure_profile/threshold_distance", magnitude: float = 31.2, unit: str = "µm", **extra) -> dict:
    return {"target": {"identifier": "@mikro/arraydataset", "object": str(dataset.id)}, "key": key, "magnitude": magnitude, "unit": unit, **extra}


def entries() -> list[FeedEntry]:
    return list(FeedEntry.objects.filter(feed="@mikro/metric").order_by("xid", "id"))


def metric_signals(intake, count: int, timeout: float = 3) -> list[dict]:  # noqa: F811
    deadline = time.monotonic() + timeout
    while True:
        found = [json.loads(r["body"]) for r in intake.received if json.loads(r["body"])["identifier"] == "@mikro/metric"]
        if len(found) >= count or time.monotonic() >= deadline:
            return found
        time.sleep(0.05)


class TestRecording:
    async def test_a_metric_is_recorded_with_its_feed_entry_and_announced(self, authenticated_context, intake):  # noqa: F811
        dataset = await seed.create_array_dataset(authenticated_context)
        data = await run(authenticated_context, CREATE, {"input": quantity(dataset)})
        metric = data["createMetric"]
        assert (metric["magnitude"], metric["unit"], metric["code"]) == (31.2, "µm", None)
        assert metric["target"] == {"identifier": "@mikro/arraydataset", "object": str(dataset.id), "uuid": metric["target"]["uuid"]}

        (entry,) = await sync_to_async(entries)()
        assert (entry.kind, entry.object, str(entry.uuid), entry.organization) == ("CREATED", metric["id"], metric["uuid"], "static_org")

        (signal,) = metric_signals(intake, 1)
        assert (signal["kind"], signal["object"]) == ("CREATED", metric["id"])
        assert signal["descriptors"] == {
            "@mikro/metric_key": "ais_suite.measure_profile/threshold_distance",
            "@mikro/magnitude": 31.2,
            "@mikro/unit": "µm",
            "@mikro/target_identifier": "@mikro/arraydataset",
            "@mikro/target_object": str(dataset.id),
        }

    async def test_a_code_is_a_value_too(self, authenticated_context):
        dataset = await seed.create_array_dataset(authenticated_context)
        spec = {"target": {"identifier": "@mikro/arraydataset", "object": str(dataset.id)}, "key": "segmenter_v2/class", "code": "segmenter_v2/class_3"}
        metric = (await run(authenticated_context, CREATE, {"input": spec}))["createMetric"]
        assert (metric["code"], metric["magnitude"], metric["descriptors"]["@mikro/metric_code"]) == ("segmenter_v2/class_3", None, "segmenter_v2/class_3")

    @pytest.mark.parametrize(
        ("change", "says"),
        [
            ({"code": "x/y"}, "exactly one"),
            ({"magnitude": None, "unit": None}, "exactly one"),
            ({"unit": None}, "lacks its unit"),
            ({"unit": "furlongs per fortnight squared banana"}, "not a valid unit"),
            ({"key": "no namespace"}, "<namespace>/<name>"),
        ],
    )
    async def test_a_value_is_a_quantity_or_a_code_under_a_namespaced_key(self, authenticated_context, change, says):
        dataset = await seed.create_array_dataset(authenticated_context)
        assert says in await refused(authenticated_context, CREATE, {"input": {**quantity(dataset), **change}})
        assert await sync_to_async(entries)() == []

    async def test_many_metrics_are_one_transaction_and_announce_nothing(self, authenticated_context, intake):  # noqa: F811
        dataset = await seed.create_array_dataset(authenticated_context)
        scene = await seed.create_scene(authenticated_context, "Canvas")
        drawn = (await run(authenticated_context, DRAW, {"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]}}))["createAnnotation"]
        on_annotation = {"target": {"identifier": "@mikro/annotation", "object": drawn["id"]}, "key": "user:1/spine_count", "magnitude": 4, "unit": "dimensionless"}

        data = await run(authenticated_context, CREATE_MANY, {"input": {"metrics": [quantity(dataset), quantity(dataset, magnitude=12.0), on_annotation]}})
        assert len(data["createMetrics"]) == 3
        assert len(await sync_to_async(entries)()) == 3
        # One target per object, made on its first metric.
        assert await models.MetricTarget.objects.acount() == 2
        assert metric_signals(intake, 1, timeout=1) == []

    async def test_a_batch_that_names_one_missing_object_records_nothing(self, authenticated_context):
        dataset = await seed.create_array_dataset(authenticated_context)
        missing = {"target": {"identifier": "@mikro/arraydataset", "object": "999999"}, "key": "a/b", "magnitude": 1, "unit": "s"}
        assert "No @mikro/arraydataset 999999" in await refused(authenticated_context, CREATE_MANY, {"input": {"metrics": [quantity(dataset), missing]}})
        assert await models.Metric.objects.acount() == 0 and await sync_to_async(entries)() == []

    async def test_a_metric_is_about_an_object_of_the_callers_organization(self, authenticated_context, other_org_context):
        dataset = await seed.create_array_dataset(authenticated_context)
        assert "in this organization" in await refused(other_org_context, CREATE, {"input": quantity(dataset)})
        await run(authenticated_context, CREATE, {"input": quantity(dataset)})
        assert (await run(other_org_context, LIST, {}))["metrics"] == []
        assert len((await run(authenticated_context, LIST, {"filters": {"target": {"identifier": "@mikro/arraydataset", "object": str(dataset.id)}}}))["metrics"]) == 1


class TestCorrecting:
    async def test_a_correction_supersedes_and_keeps_what_it_corrects(self, authenticated_context):
        dataset = await seed.create_array_dataset(authenticated_context)
        first = (await run(authenticated_context, CREATE, {"input": quantity(dataset)}))["createMetric"]
        second = (await run(authenticated_context, CREATE, {"input": quantity(dataset, magnitude=30.9, supersedes=first["id"])}))["createMetric"]
        assert second["supersedes"] == {"id": first["id"]}
        await refused(authenticated_context, DELETE, {"input": {"id": first["id"]}})
        assert await models.Metric.objects.filter(pk=first["id"]).aexists()

    async def test_a_correction_is_about_the_same_object(self, authenticated_context):
        one = await seed.create_array_dataset(authenticated_context)
        other = await seed.create_array_dataset(authenticated_context, name="Other")
        first = (await run(authenticated_context, CREATE, {"input": quantity(one)}))["createMetric"]
        assert "about another object" in await refused(authenticated_context, CREATE, {"input": quantity(other, supersedes=first["id"])})


class TestDeleting:
    async def test_a_deleted_metric_leaves_a_deleted_entry_and_is_announced(self, authenticated_context, intake):  # noqa: F811
        dataset = await seed.create_array_dataset(authenticated_context)
        metric = (await run(authenticated_context, CREATE, {"input": quantity(dataset)}))["createMetric"]
        await run(authenticated_context, DELETE, {"input": {"id": metric["id"]}})
        assert [(e.kind, e.object) for e in await sync_to_async(entries)()] == [("CREATED", metric["id"]), ("DELETED", metric["id"])]
        assert [s["kind"] for s in metric_signals(intake, 2)] == ["CREATED", "DELETED"]

    async def test_an_object_something_was_measured_about_cannot_be_deleted(self, authenticated_context):
        dataset = await seed.create_array_dataset(authenticated_context)
        metric = (await run(authenticated_context, CREATE, {"input": quantity(dataset)}))["createMetric"]
        await refused(authenticated_context, DELETE_DATASET, {"input": {"id": str(dataset.id)}})
        await run(authenticated_context, DELETE, {"input": {"id": metric["id"]}})
        # Once no measurement of it stands, the object goes, and its target with it.
        await run(authenticated_context, DELETE_DATASET, {"input": {"id": str(dataset.id)}})
        assert not await models.MetricTarget.objects.aexists()


class TestFeed:
    def read(self, settings, body: dict, key=KRAPH_KEY, issuer="live.arkitekt.kraph"):
        settings.INSTANCE = {
            **INSTANCE,
            "TRUST_JWKS": {
                "keys": [
                    *INSTANCE["TRUST_JWKS"]["keys"],
                    {**trust.public_jwk(KRAPH_KEY), "service": "live.arkitekt.kraph"},
                    {**trust.public_jwk(STRANGER_KEY), "service": "live.arkitekt.elektro"},
                ]
            },
        }
        raw = json.dumps(body).encode()
        headers = {"Authorization": trust.sign("POST", "/_feed", raw, issuer=issuer, audience="live.arkitekt.mikro", key=key)}
        return HttpClient().post("/_feed", data=raw, content_type="application/json", headers=headers)

    async def test_kraph_reads_each_metric_with_its_body(self, authenticated_context, settings):
        dataset = await seed.create_array_dataset(authenticated_context)
        metric = (await run(authenticated_context, CREATE, {"input": quantity(dataset)}))["createMetric"]

        response = await sync_to_async(self.read)(settings, {"feed": "@mikro/metric", "organization": "static_org", "after": {"xid": "0", "id": 0}, "limit": 10})
        assert response.status_code == 200, response.content
        (entry,) = response.json()["entries"]
        body = entry["body"]
        assert (entry["kind"], entry["object"], entry["uuid"]) == ("CREATED", metric["id"], metric["uuid"])
        assert body["target"] == {"identifier": "@mikro/arraydataset", "object": str(dataset.id), "uuid": metric["target"]["uuid"]}
        assert (body["key"], body["magnitude"], body["unit"], body["code"], body["supersedes"]) == ("ais_suite.measure_profile/threshold_distance", 31.2, "µm", None, None)
        assert body["producer"]["user"] == "1" and body["producer"]["client"]  # the client the request authenticated through

    async def test_one_organization_per_page_and_only_kraph_reads(self, authenticated_context, settings):
        dataset = await seed.create_array_dataset(authenticated_context)
        await run(authenticated_context, CREATE, {"input": quantity(dataset)})
        page = {"feed": "@mikro/metric", "organization": "other_org", "after": {"xid": "0", "id": 0}, "limit": 10}
        assert (await sync_to_async(self.read)(settings, page)).json()["entries"] == []
        assert (await sync_to_async(self.read)(settings, page, key=STRANGER_KEY, issuer="live.arkitekt.elektro")).status_code == 403
