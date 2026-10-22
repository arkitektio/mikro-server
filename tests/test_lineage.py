"""`lineage`: the directed walk from one container or file, as one recursive CTE per direction.

The SQL is generated from `CONTAINERS` and must answer exactly what the Python predicates
answer -- `derivation_edges`, `derived_containers`, `container_map` -- so the parity tests
here are the guard against the two drifting. The rest pins what the walk adds over
`lineageGraph`: direction, files, task attribution, the caps, and a cost that follows the
lineage rather than the organization.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from django.test.utils import CaptureQueriesContext
from kante.context import HttpContext

from authentikate.models import Organization
from core import enums, models
from core.logic import graph as graph_logic
from core.logic import lineage as lineage_logic
from mikro_server.schema import schema
from tests import seed
from tests.test_cross_container_derivation import _MEASUREMENT_COLUMNS, _derived, _table

LINEAGE = """
query Lineage($start: LineageRootInput!, $up: Int!, $down: Int!) {
  lineage(start: $start, up: $up, down: $down) {
    root
    truncated
    nodes { key kind id name depth taskId hasMoreUp hasMoreDown }
    edges { key kind source target transformationKind valueRelation primary }
  }
}
"""


async def _chain(ctx: HttpContext) -> dict:
    """raw.czi --ingest--> Acquired --derive--> Mask --measure--> Objects, plus a sibling Projection of Acquired."""
    folder = await seed.create_folder(ctx, "Lineage")
    raw = await seed.create_file(ctx, "raw.czi", folder)
    acquired = await seed.create_array_dataset(ctx, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    await sync_to_async(models.FileLink.objects.create)(
        file=raw, dataset=acquired, direction=enums.FileLinkDirectionChoices.SOURCE.value, organization=ctx.request.organization
    )
    lens = await seed.create_lens(ctx, acquired)
    mask = await _derived(ctx, "Mask", lens=lens)
    projection = await _derived(ctx, "Projection", lens=lens)
    objects = await _table(ctx, "Objects", columns=_MEASUREMENT_COLUMNS, derived_from=[{"kind": "DATASET", "dataset": str(mask)}])
    return {"raw": raw.pk, "acquired": acquired.pk, "mask": int(mask), "projection": int(projection), "objects": int(objects["id"])}


async def _walk(ctx: HttpContext, kind: str, id: object, *, up: int, down: int) -> dict:
    result = await schema.execute(LINEAGE, context_value=ctx, variable_values={"start": {"kind": kind, "id": str(id)}, "up": up, "down": down})
    assert not result.errors, result.errors
    return result.data["lineage"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_file_walks_down_through_ingest_derivation_and_measurement(authenticated_context: HttpContext) -> None:
    ids = await _chain(authenticated_context)

    lineage = await _walk(authenticated_context, "FILE", ids["raw"], up=0, down=10)
    nodes = {node["key"]: node for node in lineage["nodes"]}
    assert lineage["root"] == f"file:{ids['raw']}"
    assert set(nodes) == {
        f"file:{ids['raw']}",
        f"dataset:{ids['acquired']}",
        f"dataset:{ids['mask']}",
        f"dataset:{ids['projection']}",
        f"tabledataset:{ids['objects']}",
    }
    assert nodes[f"tabledataset:{ids['objects']}"]["depth"] == 3
    assert not lineage["truncated"]

    edges = {(edge["source"], edge["target"]): edge for edge in lineage["edges"]}
    assert edges[(f"file:{ids['raw']}", f"dataset:{ids['acquired']}")]["kind"] == "INGEST"
    mask_edge = edges[(f"dataset:{ids['acquired']}", f"dataset:{ids['mask']}")]
    assert mask_edge["kind"] == "DERIVATION" and mask_edge["transformationKind"] == "IDENTITY" and mask_edge["valueRelation"] == "CATEGORIZED"
    assert mask_edge["primary"]
    assert edges[(f"dataset:{ids['mask']}", f"tabledataset:{ids['objects']}")]["transformationKind"] == "UNMAPPABLE", "history, not placement"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_walk_is_directed_and_never_turns_sideways(authenticated_context: HttpContext) -> None:
    """From the mask: up reaches the acquisition and its file, down the table -- never the sibling projection."""
    ids = await _chain(authenticated_context)

    lineage = await _walk(authenticated_context, "DATASET", ids["mask"], up=10, down=10)
    depths = {node["key"]: node["depth"] for node in lineage["nodes"]}
    assert depths == {
        f"file:{ids['raw']}": -2,
        f"dataset:{ids['acquired']}": -1,
        f"dataset:{ids['mask']}": 0,
        f"tabledataset:{ids['objects']}": 1,
    }

    # One hop each way: the acquisition says more is above it, and that more is there.
    lineage = await _walk(authenticated_context, "DATASET", ids["mask"], up=1, down=0)
    nodes = {node["key"]: node for node in lineage["nodes"]}
    assert nodes[f"dataset:{ids['acquired']}"]["hasMoreUp"], "its file is past the depth bound"
    assert nodes[f"dataset:{ids['acquired']}"]["hasMoreDown"], "its other child is not drawn"
    assert nodes[f"dataset:{ids['mask']}"]["hasMoreDown"], "the table is past down=0"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_registrations_and_lens_edges_are_not_lineage(authenticated_context: HttpContext) -> None:
    """A world is no container and a lens edge stays inside its dataset: neither is a hop."""
    ids = await _chain(authenticated_context)
    acquired = await sync_to_async(models.ArrayDataset.objects.get)(pk=ids["acquired"])
    scene = await seed.create_scene(authenticated_context, "Composition")
    await seed.register_into_scene(authenticated_context, scene, acquired)
    await seed.create_lens(authenticated_context, acquired, slices=[{"axis": "y", "start": 0, "stop": 32, "step": None}])

    lineage = await _walk(authenticated_context, "DATASET", ids["acquired"], up=10, down=1)
    assert {node["kind"] for node in lineage["nodes"]} <= {"DATASET", "FILE"}
    assert {node["key"] for node in lineage["nodes"]} == {
        f"file:{ids['raw']}",
        f"dataset:{ids['acquired']}",
        f"dataset:{ids['mask']}",
        f"dataset:{ids['projection']}",
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_sql_answers_what_the_python_predicates_answer(authenticated_context: HttpContext) -> None:
    """Parity, one hop, for every container the other tests seed -- and the key of every space."""
    await _chain(authenticated_context)
    acquired = await sync_to_async(models.ArrayDataset.objects.get)(name="Acquired")
    scene = await seed.create_scene(authenticated_context, "Composition")
    await seed.register_into_scene(authenticated_context, scene, acquired)

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        for dataset in models.ArrayDataset.objects.filter(organization=org):
            got = lineage_logic.lineage(("dataset", dataset.pk), organization=org, up=1, down=1, limit=1000, fanout=200)
            down = {(n.kind, n.pk) for n in got.nodes if n.depth == 1 and n.kind != lineage_logic.FILE}
            up = {(n.kind, n.pk) for n in got.nodes if n.depth == -1 and n.kind != lineage_logic.FILE}

            expected_down = {graph_logic.container_map([c.coordinate_system_id])[c.coordinate_system_id] for c in graph_logic.derived_containers(dataset)}
            parents = graph_logic.derivation_edges(dataset)
            keys = graph_logic.container_map({edge.output_id for edge in parents})
            assert down == expected_down, dataset.name
            assert up == {keys[edge.output_id] for edge in parents}, dataset.name

        for table in models.TableDataset.objects.filter(organization=org):
            got = lineage_logic.lineage(("tabledataset", table.pk), organization=org, up=1, down=0)
            edges = graph_logic.collection_derivation_edges(table.coordinate_system)
            keys = graph_logic.container_map({edge.output_id for edge in edges})
            assert {(n.kind, n.pk) for n in got.nodes if n.depth == -1} == {keys[edge.output_id] for edge in edges}

        # The keying rule itself, over every space there is: worlds, grids, lens spaces, tables.
        expected = graph_logic.container_map(models.CoordinateSystem.objects.values_list("pk", flat=True))
        with connection.cursor() as cursor:
            for system, key in expected.items():
                cursor.execute(lineage_logic._key_of("%(system)s"), {"system": system})
                row = cursor.fetchone()
                assert (tuple(row) if row else ("system", system)) == key, system

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_fanout_is_capped_and_says_so(authenticated_context: HttpContext) -> None:
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, acquired)
    for index in range(5):
        await _derived(authenticated_context, f"Tile{index}", lens=lens)

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        got = lineage_logic.lineage(("dataset", acquired.pk), organization=org, up=0, down=3, fanout=3)
        root = next(node for node in got.nodes if node.depth == 0)
        assert len([node for node in got.nodes if node.depth == 1]) == 3
        assert root.has_more_down

        got = lineage_logic.lineage(("dataset", acquired.pk), organization=org, up=0, down=3, limit=2)
        assert got.truncated and len(got.nodes) <= 2

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_cycle_terminates(authenticated_context: HttpContext) -> None:
    """Nothing refuses a derivation back into a descendant, so the walk must survive one."""
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, acquired)
    mask = await _derived(authenticated_context, "Mask", lens=lens)

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        child = models.ArrayDataset.objects.get(pk=mask)
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.UNMAPPABLE.value, input=acquired.coordinate_system, output=child.coordinate_system, organization=org
        )
        got = lineage_logic.lineage(("dataset", acquired.pk), organization=org, up=10, down=10)
        assert {(n.kind, n.pk) for n in got.nodes} == {("dataset", acquired.pk), ("dataset", child.pk)}

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cost_follows_the_lineage_not_the_depth_or_the_organization(authenticated_context: HttpContext) -> None:
    """Same round trips at depth 2 and 6, and an index-driven plan over an org full of unrelated edges."""
    head = await seed.create_array_dataset(authenticated_context, "Head", axes=seed.YX_AXES, shapes=[[64, 64]])
    parent = head
    for index in range(6):
        lens = await seed.create_lens(authenticated_context, parent)
        child = await _derived(authenticated_context, f"Step{index}", lens=lens)
        parent = await sync_to_async(models.ArrayDataset.objects.get)(pk=child)

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        counts = []
        for depth in (2, 6):
            with CaptureQueriesContext(connection) as queries:
                got = lineage_logic.lineage(("dataset", head.pk), organization=org, up=0, down=depth)
            assert len([n for n in got.nodes if n.depth > 0]) == depth
            counts.append(len(queries))
        assert counts[0] == counts[1], counts

        # Thousands of unrelated spaces and edges: the walk must reach its rows by index.
        systems = models.CoordinateSystem.objects.bulk_create([models.CoordinateSystem(name=f"noise{i}", organization=org) for i in range(3000)])
        models.Transformation.objects.bulk_create(
            [models.Transformation(kind=enums.TransformKindChoices.IDENTITY.value, input=a, output=b, organization=org) for a, b in zip(systems, systems[1:])]
        )
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE")
            cursor.execute(
                "EXPLAIN " + lineage_logic._walk_sql(True),
                {"root_kind": "dataset", "root_pk": head.pk, "depth": 6, "limit": 301, "fanout": 50, "org": org.pk},
            )
            plan = "\n".join(row[0] for row in cursor.fetchall())
        assert "Seq Scan on core_transformation" not in plan, plan

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_organizations_edges_never_appear(authenticated_context: HttpContext) -> None:
    """Theirs never shows: not their edge into our data, and not their container behind an edge of ours."""
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    foreign = await seed.create_array_dataset(authenticated_context, "Foreign", axes=seed.YX_AXES, shapes=[[64, 64]])

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        other = Organization.objects.create(slug="lineage_other_org")
        models.ArrayDataset.objects.filter(pk=foreign.pk).update(organization=other)
        models.CoordinateSystem.objects.filter(pk=foreign.coordinate_system_id).update(organization=other)

        # Their edge, from their dataset, landing in ours.
        models.Transformation.objects.create(kind=enums.TransformKindChoices.IDENTITY.value, input=foreign.coordinate_system, output=acquired.coordinate_system, organization=other)
        got = lineage_logic.lineage(("dataset", acquired.pk), organization=org, up=5, down=5)
        assert [(n.kind, n.pk) for n in got.nodes] == [("dataset", acquired.pk)]

        # An edge of ours naming their dataset: the node is theirs, so it is dropped with its edge.
        models.Transformation.objects.create(kind=enums.TransformKindChoices.IDENTITY.value, input=foreign.coordinate_system, output=acquired.coordinate_system, organization=org)
        got = lineage_logic.lineage(("dataset", acquired.pk), organization=org, up=5, down=5)
        assert [(n.kind, n.pk) for n in got.nodes] == [("dataset", acquired.pk)]
        assert got.edges == []

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_each_step_names_the_task_that_made_it(authenticated_context: HttpContext) -> None:
    """A node names its task; an edge names the task that made its child, the link's own when it has one."""
    from koherent.models import Task

    ids = await _chain(authenticated_context)

    def stamp() -> None:
        org = Organization.objects.get(slug="static_org")
        convert = Task.objects.create(task_id="convert-1", root_task_id="convert-1", token_id="token-convert-1", organization=org)
        segment = Task.objects.create(task_id="segment-1", root_task_id="segment-1", token_id="token-segment-1", organization=org)
        # The converter made the dataset; the link row it wrote carries no task of its own.
        models.ArrayDataset.objects.filter(pk=ids["acquired"]).update(created_through=convert)
        models.ArrayDataset.objects.filter(pk=ids["mask"]).update(created_through=segment)

    await sync_to_async(stamp)()

    lineage = await _walk(authenticated_context, "FILE", ids["raw"], up=0, down=2)
    nodes = {node["key"]: node for node in lineage["nodes"]}
    assert nodes[f"dataset:{ids['acquired']}"]["taskId"] == "convert-1"
    assert nodes[f"file:{ids['raw']}"]["taskId"] is None

    result = await schema.execute(
        "query L($start: LineageRootInput!) { lineage(start: $start, up: 0, down: 2) { edges { source target taskId } } }",
        context_value=authenticated_context,
        variable_values={"start": {"kind": "FILE", "id": str(ids["raw"])}},
    )
    assert not result.errors, result.errors
    tasks = {(edge["source"], edge["target"]): edge["taskId"] for edge in result.data["lineage"]["edges"]}
    assert tasks[(f"file:{ids['raw']}", f"dataset:{ids['acquired']}")] == "convert-1", "the ingest is the converter's"
    assert tasks[(f"dataset:{ids['acquired']}", f"dataset:{ids['mask']}")] == "segment-1", "a derivation is its product's task"
    assert tasks[(f"dataset:{ids['acquired']}", f"dataset:{ids['projection']}")] is None, "made by hand"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_lens_is_a_port_and_each_edge_leaves_from_its_own(authenticated_context: HttpContext) -> None:
    """A node lists its lenses, the whole one first; a step computed from a crop leaves from the crop."""
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    whole = await seed.create_lens(authenticated_context, acquired)
    crop = await seed.create_lens(authenticated_context, acquired, slices=[{"axis": "y", "start": 0, "stop": 32, "step": None}])
    await seed.create_lens(authenticated_context, acquired, slices=[{"axis": "x", "start": 8, "stop": 16, "step": None}])  # nothing made from it
    mask = await _derived(authenticated_context, "Mask", lens=whole)
    cropped = await _derived(authenticated_context, "Cropped", lens=crop)

    result = await schema.execute(
        """query L($start: LineageRootInput!) { lineage(start: $start, up: 0, down: 1) {
            nodes { key lensCount lenses { id label whole } }
            edges { source target sourceLens }
        } }""",
        context_value=authenticated_context,
        variable_values={"start": {"kind": "DATASET", "id": str(acquired.pk)}},
    )
    assert not result.errors, result.errors
    nodes = {node["key"]: node for node in result.data["lineage"]["nodes"]}
    root = nodes[f"dataset:{acquired.pk}"]
    assert root["lensCount"] == 3
    assert root["lenses"][0] == {"id": str(whole.pk), "label": "Whole", "whole": True}
    assert {lens["label"] for lens in root["lenses"]} == {"Whole", "y[0:32]", "x[8:16]"}, "every lens, used or not"

    sources = {edge["target"]: edge["sourceLens"] for edge in result.data["lineage"]["edges"]}
    assert sources[f"dataset:{mask}"] == str(whole.pk)
    assert sources[f"dataset:{cropped}"] == str(crop.pk), "the crop's product leaves from the crop"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_array_node_carries_its_axes_and_each_lens_its_shape(authenticated_context: HttpContext) -> None:
    """The tile draws the array: axes with sizes and chunks, and every lens' slices and shape."""
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", shapes=[[3, 64, 64], [3, 32, 32]])
    whole = await seed.create_lens(authenticated_context, acquired)
    crop = await seed.create_lens(authenticated_context, acquired, slices=[{"axis": "y", "start": 0, "stop": 32, "step": None}, {"axis": "x", "start": 8, "stop": 64, "step": 2}])

    result = await schema.execute(
        """query L($start: LineageRootInput!) { lineage(start: $start, up: 0, down: 1) {
            nodes { key axes { name type size chunk } dtype levels lenses { id shape slices { axis start stop step } } }
        } }""",
        context_value=authenticated_context,
        variable_values={"start": {"kind": "DATASET", "id": str(acquired.pk)}},
    )
    assert not result.errors, result.errors
    root = next(node for node in result.data["lineage"]["nodes"] if node["key"] == f"dataset:{acquired.pk}")
    assert root["axes"] == [
        {"name": "c", "type": "CHANNEL", "size": 3, "chunk": 3},
        {"name": "y", "type": "SPACE", "size": 64, "chunk": 64},
        {"name": "x", "type": "SPACE", "size": 64, "chunk": 64},
    ]
    assert root["levels"] == 2
    lenses = {lens["id"]: lens for lens in root["lenses"]}
    assert lenses[str(whole.pk)] == {"id": str(whole.pk), "shape": [3, 64, 64], "slices": []}
    assert lenses[str(crop.pk)]["shape"] == [3, 32, 28]
    assert lenses[str(crop.pk)]["slices"] == [{"axis": "y", "start": 0, "stop": 32, "step": 1}, {"axis": "x", "start": 8, "stop": 63, "step": 2}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scenes_drawing_surface_is_drawn_not_computed(authenticated_context: HttpContext) -> None:
    """A scene staged on a dataset's own grid registers its drawing collection INTO that grid.

    That edge lands in the dataset's space, which the derivation predicate cannot tell from
    "computed from the dataset". Lineage must not report it -- not on the page's walk, not in
    `derivedResidents`, not in `lineageGraph` -- while a crop cut along one of its shapes still
    names the collection as where it came from. Placement keeps the edge: that is how the
    shapes follow the dataset into other scenes.
    """
    from tests.test_annotations import _draw
    from tests.test_derived_datasets import _derive
    from tests.test_scene_over_owned_system import _stage_in_own_grid

    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Stained", axes=seed.YX_AXES, shapes=[[16, 16]])
    scene = await _stage_in_own_grid(ctx, dataset)
    shape = await _draw(ctx, {"scene": scene["id"]}, kind="RECTANGLE", vectors=[[1.0, 1.0], [1.0, 4.0], [4.0, 4.0], [4.0, 1.0]])
    collection = shape["collection"]["id"]
    assert shape["collection"]["scene"]["id"] == scene["id"]

    lens = await seed.create_lens(ctx, dataset)
    crop = await _derive(
        ctx,
        "Crop",
        axes=seed.YX_AXES,
        shape=[3, 3],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "ANNOTATION", "annotation": shape["id"]},
        ],
    )
    assert not crop.errors, crop.errors
    crop_id = crop.data["createArrayDataset"]["id"]

    down = await _walk(ctx, "DATASET", dataset.pk, up=0, down=5)
    assert {node["key"] for node in down["nodes"]} == {f"dataset:{dataset.pk}", f"dataset:{crop_id}"}, "the drawing surface is not a child"

    up = await _walk(ctx, "ANNOTATION_COLLECTION", collection, up=5, down=0)
    assert [node["key"] for node in up["nodes"]] == [f"annotationcollection:{collection}"], "it was made from nothing"

    from_crop = await _walk(ctx, "DATASET", crop_id, up=1, down=0)
    assert {node["key"] for node in from_crop["nodes"]} == {
        f"dataset:{crop_id}",
        f"dataset:{dataset.pk}",
        f"annotationcollection:{collection}",
    }, "a crop along a shape still came from it"

    def check() -> None:
        assert graph_logic.derived_containers(dataset) == [models.ArrayDataset.objects.get(pk=crop_id)]
        nodes, _ = graph_logic.lineage_graph(dataset.coordinate_system, organization=dataset.organization)
        drawn = models.AnnotationCollection.objects.get(pk=collection)
        assert drawn in nodes, "reached as the crop's parent"
        # ...but not as the dataset's child: no edge from its space is reported.
        _, edges = graph_logic.lineage_graph(dataset.coordinate_system, organization=dataset.organization)
        assert all(edge.input_id != drawn.coordinate_system_id for edge in edges)
        # Placement still reads the registration as the collection's fact.
        assert graph_logic.collection_derivation_edges(drawn.coordinate_system), "placement keeps the edge"

    await sync_to_async(check)()


DESCENDANTS = """
query Descendants($starts: [LineageRootInput!]!, $down: Int!) {
  descendants(starts: $starts, down: $down) { root more nodes { key kind id name depth parent } }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_descendants_answer_what_lineage_answers_per_root(authenticated_context: HttpContext) -> None:
    """One walk for many roots, of any kind: each root gets exactly its `lineage(up: 0)` nodes."""
    ids = await _chain(authenticated_context)
    starts = [
        {"kind": "DATASET", "id": str(ids["acquired"])},
        {"kind": "DATASET", "id": str(ids["mask"])},
        # A table is a root as much as a series is: not everything starts from a file.
        {"kind": "TABLE_DATASET", "id": str(ids["objects"])},
        {"kind": "FILE", "id": str(ids["raw"])},
    ]
    result = await schema.execute(DESCENDANTS, context_value=authenticated_context, variable_values={"starts": starts, "down": 3})
    assert not result.errors, result.errors
    by_root = {entry["root"]: entry for entry in result.data["descendants"]}

    for start in starts:
        walked = await _walk(authenticated_context, start["kind"], start["id"], up=0, down=3)
        want = {node["key"] for node in walked["nodes"] if node["depth"] > 0}
        entry = by_root[walked["root"]]
        assert {node["key"] for node in entry["nodes"]} == want, start
        assert not entry["more"]

    assert by_root[f"tabledataset:{ids['objects']}"]["nodes"] == []

    # Each node names what it was made from, so the walk reads as a tree.
    parent_of = {node["key"]: node["parent"] for node in by_root[f"dataset:{ids['acquired']}"]["nodes"]}
    assert parent_of[f"dataset:{ids['mask']}"] == f"dataset:{ids['acquired']}"
    assert parent_of[f"tabledataset:{ids['objects']}"] == f"dataset:{ids['mask']}"
    assert [node["depth"] for node in by_root[f"dataset:{ids['acquired']}"]["nodes"]] == sorted(
        node["depth"] for node in by_root[f"dataset:{ids['acquired']}"]["nodes"]
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_descendants_say_when_they_were_cut_short(authenticated_context: HttpContext) -> None:
    ids = await _chain(authenticated_context)

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        # Acquired -> Mask -> Objects: one hop leaves Objects out.
        (shallow,) = lineage_logic.descendants([("dataset", ids["acquired"])], organization=org, down=1)
        assert {node.pk for node in shallow.nodes} == {ids["mask"], ids["projection"]}
        assert shallow.more

        # Two children (Mask, Projection), a fan-out of one.
        (narrow,) = lineage_logic.descendants([("dataset", ids["acquired"])], organization=org, down=1, fanout=1)
        assert len(narrow.nodes) == 1 and narrow.more

        assert lineage_logic.descendants([("dataset", 10**9)], organization=org) == []

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_descendants_cost_does_not_grow_with_the_roots(authenticated_context: HttpContext) -> None:
    roots = []
    for index in range(6):
        acquired = await seed.create_array_dataset(authenticated_context, f"Acquired{index}", axes=seed.YX_AXES, shapes=[[64, 64]])
        lens = await seed.create_lens(authenticated_context, acquired)
        await _derived(authenticated_context, f"Mask{index}", lens=lens)
        roots.append(("dataset", acquired.pk))

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        counts = []
        for some in (roots[:2], roots):
            with CaptureQueriesContext(connection) as queries:
                got = lineage_logic.descendants(some, organization=org)
            assert [len(entry.nodes) for entry in got] == [1] * len(some)
            counts.append(len(queries))
        assert counts[0] == counts[1], counts

    await sync_to_async(check)()


ROOTS = """
query Roots($kinds: [LineageNodeKind!], $pagination: OffsetPaginationInput, $filters: RootFilter) {
  roots(kinds: $kinds, pagination: $pagination, filters: $filters) { key kind id name createdAt }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_roots_are_the_nodes_nothing_was_made_from(authenticated_context: HttpContext) -> None:
    """A file and what came without one are roots; a converted series, a mask, a measurement and an export are not."""
    ids = await _chain(authenticated_context)
    written = await seed.create_array_dataset(authenticated_context, "Written", axes=seed.YX_AXES, shapes=[[16, 16]])
    uploaded = await _table(authenticated_context, "Uploaded", columns=_MEASUREMENT_COLUMNS)

    def export() -> int:
        org = Organization.objects.get(slug="static_org")
        exported = models.File.objects.create(name="export.ome.tif", organization=org, membership=models.File.objects.get(pk=ids["raw"]).membership)
        models.FileLink.objects.create(file=exported, dataset_id=ids["mask"], direction=enums.FileLinkDirectionChoices.RENDITION.value, organization=org)
        return exported.pk

    exported = await sync_to_async(export)()

    result = await schema.execute(ROOTS, context_value=authenticated_context, variable_values={"pagination": {"limit": 50}})
    assert not result.errors, result.errors
    keys = [row["key"] for row in result.data["roots"]]
    assert {f"file:{ids['raw']}", f"dataset:{written.pk}", f"tabledataset:{uploaded['id']}"} <= set(keys)
    for derived in (f"dataset:{ids['acquired']}", f"dataset:{ids['mask']}", f"tabledataset:{ids['objects']}", f"file:{exported}"):
        assert derived not in keys, derived

    # Newest first, across kinds.
    stamps = [row["createdAt"] for row in result.data["roots"]]
    assert stamps == sorted(stamps, reverse=True)

    narrowed = await schema.execute(ROOTS, context_value=authenticated_context, variable_values={"kinds": ["TABLE_DATASET"]})
    assert {row["kind"] for row in narrowed.data["roots"]} == {"TABLE_DATASET"}

    first = await schema.execute(ROOTS, context_value=authenticated_context, variable_values={"pagination": {"limit": 1}})
    second = await schema.execute(ROOTS, context_value=authenticated_context, variable_values={"pagination": {"limit": 1, "offset": 1}})
    assert [first.data["roots"][0]["key"], second.data["roots"][0]["key"]] == keys[:2]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_roots_narrow_by_creator_collaboration_and_time(authenticated_context: HttpContext) -> None:
    """A file is judged by its converted series for acquisition and collaboration; kinds with no acquisition time drop out."""
    import datetime

    from arkitekt_service.collaborations.models import Collaboration, CollaborationItem
    from django.contrib.contenttypes.models import ContentType

    ids = await _chain(authenticated_context)
    written = await seed.create_array_dataset(authenticated_context, "Written", axes=seed.YX_AXES, shapes=[[16, 16]])
    uploaded = await _table(authenticated_context, "Uploaded", columns=_MEASUREMENT_COLUMNS)
    other = await seed.create_other_user(authenticated_context)
    folder = await seed.create_folder(authenticated_context, "Theirs")
    theirs = await seed.create_file(authenticated_context, "theirs.czi", folder, creator=other)

    day = lambda hour: datetime.datetime(2026, 10, 22, hour, tzinfo=datetime.UTC)  # noqa: E731

    def stage() -> int:
        # The series converted out of raw.czi was acquired 10:00-11:00; "Written" at 14:00.
        models.ArrayDataset.objects.filter(pk=ids["acquired"]).update(acquisition_start=day(10), acquisition_end=day(11))
        models.ArrayDataset.objects.filter(pk=written.pk).update(acquisition_start=day(14), acquisition_end=day(14))
        models.ArrayDataset.objects.filter(pk=ids["acquired"]).update(stored_spec=["VOLUME", "TIMESERIES"])
        models.ArrayDataset.objects.filter(pk=written.pk).update(stored_spec=["IMAGE"])
        org = Organization.objects.get(slug="static_org")
        collaboration = Collaboration.objects.create(organization=org, name="Shared")
        # Only the converted series is put in; its file counts as in through it.
        CollaborationItem.objects.create(
            collaboration=collaboration, content_type=ContentType.objects.get_for_model(models.ArrayDataset), object_id=str(ids["acquired"])
        )
        CollaborationItem.objects.create(
            collaboration=collaboration, content_type=ContentType.objects.get_for_model(models.TableDataset), object_id=str(uploaded["id"])
        )
        return collaboration.pk

    collaboration = await sync_to_async(stage)()

    async def keys(filters: dict) -> set[str]:
        result = await schema.execute(ROOTS, context_value=authenticated_context, variable_values={"pagination": {"limit": 50}, "filters": filters})
        assert not result.errors, result.errors
        return {row["key"] for row in result.data["roots"]}

    raw, mine = f"file:{ids['raw']}", f"dataset:{written.pk}"
    table = f"tabledataset:{uploaded['id']}"

    assert await keys({"owner": "2"}) == {f"file:{theirs.pk}"}
    assert {raw, mine, table} <= await keys({"owner": authenticated_context.request.user.sub})

    assert await keys({"collaboration": str(collaboration)}) == {raw, table}

    assert await keys({"acquiredAfter": day(9).isoformat(), "acquiredBefore": day(12).isoformat()}) == {raw}
    assert await keys({"acquiredAfter": day(13).isoformat()}) == {mine}
    assert await keys({"acquiredBefore": day(9).isoformat()}) == set()

    assert await keys({"spec": ["IMAGE"]}) == {mine}
    assert await keys({"spec": ["VOLUME", "TIMESERIES"]}) == {raw}
    assert await keys({"spec": ["VOLUME", "MULTICHANNEL"]}) == set()
    # A file's series must meet the spec and the window at once: raw's series is a volume but
    # was acquired at 10:00, and the image acquired at 14:00 is not a volume.
    assert await keys({"spec": ["VOLUME"], "acquiredAfter": day(13).isoformat()}) == set()
    assert await keys({"spec": ["VOLUME"], "acquiredBefore": day(12).isoformat()}) == {raw}

    assert await keys({"search": "writ"}) == {mine}
    assert await keys({"search": "upload"}) == {table}
    # raw.czi is found by its series' name, "Acquired"; the series itself is not a root.
    assert await keys({"search": "ACQUIRED"}) == {raw}
    assert await keys({"search": ".czi"}) == {raw, f"file:{theirs.pk}"}
    assert await keys({"search": "%"}) == set()
    assert await keys({"search": ".czi", "owner": "2"}) == {f"file:{theirs.pk}"}

    everything = await keys({})
    assert await keys({"createdAfter": day(0).replace(year=2000).isoformat()}) == everything
    assert await keys({"createdBefore": day(0).replace(year=2000).isoformat()}) == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_roots_cost_does_not_grow_with_the_page(authenticated_context: HttpContext) -> None:
    for index in range(8):
        acquired = await seed.create_array_dataset(authenticated_context, f"Acquired{index}", axes=seed.YX_AXES, shapes=[[16, 16]])
        lens = await seed.create_lens(authenticated_context, acquired)
        await _derived(authenticated_context, f"Mask{index}", lens=lens)

    def check() -> None:
        org = Organization.objects.get(slug="static_org")
        counts = []
        for limit in (2, 8):
            with CaptureQueriesContext(connection) as queries:
                got = lineage_logic.roots(organization=org, limit=limit)
            assert len(got) == limit and all(node.name.startswith("Acquired") for node in got)
            counts.append(len(queries))
        assert counts[0] == counts[1], counts

    await sync_to_async(check)()
