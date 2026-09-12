"""Colouring and filtering a network layer: the two shared picker kinds, plus its own.

The COLUMN half is `test_mesh_layers` over a different substrate -- a table keyed by
`createTableDataset(keyedBy: {kind: NETWORK_COLLECTION})`, one row per traced **object** --
and is deliberately tested the same way. What is new is the GRAPH half: a per-node value the
collection itself carries, validated against the vocabulary its manifest declared rather than
against any table, and the depth-zero rooting of the reachability walk -- the wider fact walk
reaches the image a network was traced from and offers tables keyed by mask-instance ids,
joins an object id cannot execute.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from core.logic import attribute_plans as attribute_plans_logic
from mikro_server.schema import schema
from tests import seed

LAYER_FIELDS = """
    id
    materialColor
    maxLevel
    activeColorBy
    activeFilterBys
    colorBys { kind attribute target table column colormap min max label joinPath { table column } }
    filterBys { kind attribute target table column min max values exclude label joinPath { table column } }
"""

CREATE_LAYER = """
mutation Create($input: CreateNetworkLayerInput!) {
  createNetworkLayer(input: $input) {
    %s
  }
}
""" % LAYER_FIELDS

UPDATE_LAYER = """
mutation Update($input: UpdateNetworkLayerInput!) {
  updateNetworkLayer(input: $input) {
    %s
  }
}
""" % LAYER_FIELDS

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id }
}
"""

OPTIONS = """
query Options($collection: ID!) {
  networkColorByOptions(networkCollection: $collection) {
    graphAttribute
    control
    table { id }
    column { name }
    joinPath { table { id } column { name } }
  }
}
"""

XYZ_AXES = [{"name": "x", "type": "SPACE"}, {"name": "y", "type": "SPACE"}, {"name": "z", "type": "SPACE"}]

#: One measure column and one categorical one, exactly the mesh fixture's split.
FILAMENT_COLUMNS = [
    {"name": "object_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "total_length", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    {"name": "compartment", "dtype": "VARCHAR", "role": "LABEL"},
]

#: The vocabulary every seeded collection carries: the four intrinsics plus `radius`, which
#: joins exactly because the seeded encoding declares `radii: FLOAT32`.
VOCABULARY = ["strahler", "degree", "depth", "component", "radius"]


async def _collection(ctx: HttpContext, **store_kwargs: object) -> models.NetworkCollection:
    """A network collection in a space of its own."""
    store = await seed.create_konnektion_store(ctx, **store_kwargs)
    result = await schema.execute(
        "mutation Create($input: CreateNetworkCollectionInput!) { createNetworkCollection(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"version": "v1", "store": str(store.pk), "axes": XYZ_AXES}},
    )
    assert not result.errors, result.errors
    return await sync_to_async(models.NetworkCollection.objects.select_related("store", "coordinate_system").get)(id=result.data["createNetworkCollection"]["id"])


async def _scene_for(ctx: HttpContext, collection: models.NetworkCollection) -> models.Scene:
    """A scene the collection is placed in, so the layer mutation's placement gate passes."""
    scene = await seed.create_scene(ctx, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind="IDENTITY",
            input=collection.coordinate_system,
            output=scene.world,
            params={},
            organization=ctx.request.organization,
        )

    await sync_to_async(register)()
    return scene


async def _table(ctx: HttpContext, name: str, columns: list[dict], **extra: object) -> str:
    await sync_to_async(models.ParquetStore.objects.create)(path=f"s3://parquet/{name}", bucket="parquet", key=name, organization=ctx.request.organization, populated=True)
    result = await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, columns, **extra)},
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


async def _keyed_table(ctx: HttpContext, collection: models.NetworkCollection, name: str = "filament-stats") -> str:
    """A per-object statistics table keyed by the collection's object ids."""
    return await _table(
        ctx,
        name,
        FILAMENT_COLUMNS,
        identified_by={"object_id": [{"kind": "NETWORK_COLLECTION", "networkCollection": str(collection.pk)}]},
    )


async def _create_layer(ctx: HttpContext, scene: models.Scene, collection: models.NetworkCollection, **extra: object):
    return await schema.execute(
        CREATE_LAYER,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.pk), "networkCollection": str(collection.pk), **extra}},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_network_layer_colours_by_a_graph_attribute(authenticated_context: HttpContext):
    """The GRAPH member end to end: validated against the manifest, stored with its target."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    result = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[
            {"kind": "GRAPH", "attribute": "strahler", "colormap": "VIRIDIS", "label": "Strahler order"},
            {"kind": "GRAPH", "attribute": "radius", "target": "EDGE", "colormap": "VIRIDIS"},
        ],
        activeColorBy=0,
    )
    assert not result.errors, result.errors

    layer = result.data["createNetworkLayer"]
    assert layer["colorBys"][0]["attribute"] == "strahler"
    assert layer["colorBys"][0]["target"] == "NODE", "an omitted target is NODE -- the only thing there was to target before edges could be addressed"
    assert layer["colorBys"][0]["table"] is None and layer["colorBys"][0]["column"] is None
    assert layer["colorBys"][1] == {
        "kind": "GRAPH", "attribute": "radius", "target": "EDGE", "table": None, "column": None,
        "colormap": "VIRIDIS", "min": None, "max": None, "label": None, "joinPath": [],
    }
    assert layer["activeColorBy"] == 0

    stored = await models.Layer.objects.aget(id=layer["id"])
    assert stored.network_color_bys[0]["kind"] == "GRAPH"
    assert stored.network_color_bys[0]["attribute"] == "strahler"
    assert stored.network_color_bys[0]["target"] == "NODE"
    assert stored.network_color_bys[0]["table"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_graph_colouring_names_an_attribute_the_collection_carries(authenticated_context: HttpContext):
    """An unknown attribute is refused, and the refusal lists the declared vocabulary."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    result = await _create_layer(
        authenticated_context, scene, collection,
        colorBys=[{"kind": "GRAPH", "attribute": "tortuosity", "colormap": "VIRIDIS"}],
    )
    assert result.errors, "an attribute the manifest never declared must be refused"
    message = str(result.errors[0])
    assert "tortuosity" in message
    assert "strahler" in message, "the refusal names the set that would have been accepted"

    # A collection whose store predates attributes declares none, and says so -- radius-less
    # too, or `encoding.radii` keeps one word in the vocabulary and the empty-set prose never
    # shows.
    bare = await _collection(authenticated_context, attributes=None, encoding=dict(seed.KONNEKTION_ENCODING, radii="NONE"))
    bare_scene = await _scene_for(authenticated_context, bare)
    result = await _create_layer(
        authenticated_context, bare_scene, bare,
        colorBys=[{"kind": "GRAPH", "attribute": "strahler", "colormap": "VIRIDIS"}],
    )
    assert result.errors
    assert "rebuild" in str(result.errors[0]), "a pre-attribute collection is told what to do, not just refused"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_radius_is_in_the_vocabulary_exactly_when_the_encoding_carries_one(authenticated_context: HttpContext):
    """`radius` is not a manifest attribute -- it rides on `encoding.radii` -- but it colours."""
    radiusless = dict(seed.KONNEKTION_ENCODING, radii="NONE")
    collection = await _collection(authenticated_context, encoding=radiusless)
    scene = await _scene_for(authenticated_context, collection)

    result = await _create_layer(
        authenticated_context, scene, collection,
        colorBys=[{"kind": "GRAPH", "attribute": "radius", "colormap": "VIRIDIS"}],
    )
    assert result.errors, "a radius colouring over a radius-less collection reads bytes nothing wrote"
    assert "radius" in str(result.errors[0])

    options = await schema.execute(OPTIONS, context_value=authenticated_context, variable_values={"collection": str(collection.pk)})
    assert not options.errors, options.errors
    offered = [option["graphAttribute"] for option in options.data["networkColorByOptions"] if option["graphAttribute"]]
    assert "radius" not in offered, "the offered set and the accepted set are one set"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_graph_colouring_is_measured(authenticated_context: HttpContext):
    """Qualitative colormaps are refused for every graph attribute, `component` included."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    result = await _create_layer(
        authenticated_context, scene, collection,
        colorBys=[{"kind": "GRAPH", "attribute": "component", "colormap": "HUES"}],
    )
    assert result.errors
    assert "qualitative" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_network_layer_colours_by_a_keyed_table(authenticated_context: HttpContext):
    """The NETWORK_COLLECTION identification end to end: the edge authors, the colouring runs."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _keyed_table(authenticated_context, collection)

    result = await _create_layer(
        authenticated_context, scene, collection,
        colorBys=[{"table": table, "column": "total_length", "colormap": "VIRIDIS", "label": "Total length"}],
        filterBys=[{"table": table, "column": "compartment", "values": ["dendrite"], "label": "Dendrites"}],
        activeColorBy=0,
        activeFilterBys=[0],
    )
    assert not result.errors, result.errors

    layer = result.data["createNetworkLayer"]
    assert layer["colorBys"][0]["kind"] == "COLUMN"
    assert layer["colorBys"][0]["table"] == table
    assert layer["colorBys"][0]["attribute"] is None
    assert layer["filterBys"][0]["values"] == ["dendrite"]
    assert layer["activeFilterBys"] == [0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_wider_fact_walk_is_not_offered(authenticated_context: HttpContext):
    """A table keyed by the image the network was traced from is reachable and still refused.

    The depth-zero rooting on trial. The fact walk crosses derivations in both directions, so
    rooted on the network's system it reaches the source image and the tables keyed by *mask
    instance* ids -- real tables, genuinely reachable, and not executable from an object id.
    The premise (the wider walk DOES find it) is asserted too, so this cannot pass vacuously.
    """
    ctx = authenticated_context
    collection = await _collection(ctx)
    scene = await _scene_for(ctx, collection)

    source = await seed.create_array_dataset(ctx, "traced-image")
    mask_table = await _table(
        ctx,
        "mask-stats",
        FILAMENT_COLUMNS,
        identified_by={"object_id": [{"kind": "DATASET", "dataset": str(source.pk)}]},
    )

    def relate() -> None:
        models.Transformation.objects.create(
            kind="IDENTITY",
            input=collection.coordinate_system,
            output=source.intrinsic_coordinate_system,
            params={},
            organization=ctx.request.organization,
        )

    await sync_to_async(relate)()

    def widely_reachable() -> set[str]:
        return set(attribute_plans_logic.field_reachable_tables(collection.coordinate_system, ctx.request.organization))

    assert mask_table in await sync_to_async(widely_reachable)(), "the premise: the unbounded walk reaches the mask table, which is exactly why the rooting matters"

    options = await schema.execute(OPTIONS, context_value=ctx, variable_values={"collection": str(collection.pk)})
    assert not options.errors, options.errors
    offered_tables = {option["table"]["id"] for option in options.data["networkColorByOptions"] if option["table"]}
    assert mask_table not in offered_tables, "an option an object id cannot execute must not be offered"

    result = await _create_layer(
        ctx, scene, collection,
        colorBys=[{"table": mask_table, "column": "total_length", "colormap": "VIRIDIS"}],
    )
    assert result.errors, "and the mutation refuses it too -- offered == accepted, in both directions"
    assert "not reachable" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_options_offer_exactly_what_the_mutation_accepts(authenticated_context: HttpContext):
    """The invariant, walked in both directions over both halves of the list."""
    ctx = authenticated_context
    collection = await _collection(ctx)
    scene = await _scene_for(ctx, collection)
    table = await _keyed_table(ctx, collection)

    options = await schema.execute(OPTIONS, context_value=ctx, variable_values={"collection": str(collection.pk)})
    assert not options.errors, options.errors
    offered = options.data["networkColorByOptions"]

    graph_half = [option["graphAttribute"] for option in offered if option["graphAttribute"]]
    assert graph_half == VOCABULARY, "the graph attributes come first, in declaration order, radius last"
    for option in offered:
        if option["graphAttribute"]:
            assert option["control"] == "MEASURE", "every graph attribute is measured -- one rule, no per-semantics branch"
            assert option["table"] is None and option["column"] is None

    column_half = {(option["table"]["id"], option["column"]["name"]) for option in offered if option["table"]}
    assert (table, "total_length") in column_half
    assert (table, "compartment") in column_half

    # Accepted, entry by entry: every offered option writes.
    entries = []
    for option in offered:
        if option["graphAttribute"]:
            entries.append({"kind": "GRAPH", "attribute": option["graphAttribute"], "colormap": "VIRIDIS", "min": float(len(entries))})
        elif option["column"]["name"] == "total_length":
            entries.append({"table": option["table"]["id"], "column": "total_length", "colormap": "VIRIDIS"})
    result = await _create_layer(ctx, scene, collection, colorBys=entries)
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_nested_options_field_roots_on_the_network(authenticated_context: HttpContext):
    """`NetworkCollection.colorByOptions` resolves through the network rooting.

    The regression this guards: it used to delegate to the mesh-rooted root query with a
    network pk -- DoesNotExist when no mesh row shared the id, silently another collection's
    options when one did.
    """
    collection = await _collection(authenticated_context)
    result = await schema.execute(
        "query Get($id: ID!) { networkCollection(id: $id) { colorByOptions { graphAttribute } } }",
        context_value=authenticated_context,
        variable_values={"id": str(collection.pk)},
    )
    assert not result.errors, result.errors
    assert [option["graphAttribute"] for option in result.data["networkCollection"]["colorByOptions"]] == VOCABULARY


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_graph_rule_filters_by_bounds(authenticated_context: HttpContext):
    """A GRAPH filter is bounds over a per-node value; a value set is refused."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    result = await _create_layer(
        authenticated_context, scene, collection,
        filterBys=[{"kind": "GRAPH", "attribute": "strahler", "min": 3, "label": "Trunk only"}],
        activeFilterBys=[0],
    )
    assert not result.errors, result.errors
    rule = result.data["createNetworkLayer"]["filterBys"][0]
    assert rule == {
        "kind": "GRAPH", "attribute": "strahler", "target": "NODE", "table": None, "column": None,
        "min": 3.0, "max": None, "values": None, "exclude": False, "label": "Trunk only", "joinPath": [],
    }

    refused = await _create_layer(
        authenticated_context, scene, collection,
        filterBys=[{"kind": "GRAPH", "attribute": "component", "values": ["0"]}],
    )
    assert refused.errors
    assert "measured" in str(refused.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_identical_graph_colourings_are_one_wearing_two_names(authenticated_context: HttpContext):
    """The duplicate key covers `attribute` and `target`: same pair refused, different target kept."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    refused = await _create_layer(
        authenticated_context, scene, collection,
        colorBys=[
            {"kind": "GRAPH", "attribute": "degree", "colormap": "VIRIDIS"},
            {"kind": "GRAPH", "attribute": "degree", "colormap": "VIRIDIS", "label": "A second name"},
        ],
    )
    assert refused.errors
    assert "render" in str(refused.errors[0]).lower()

    allowed = await _create_layer(
        authenticated_context, scene, collection,
        colorBys=[
            {"kind": "GRAPH", "attribute": "degree", "colormap": "VIRIDIS"},
            {"kind": "GRAPH", "attribute": "degree", "target": "EDGE", "colormap": "VIRIDIS"},
        ],
    )
    assert not allowed.errors, "one metric aimed at nodes and at edges are two colourings someone might switch between"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_update_patches_the_pickers(authenticated_context: HttpContext):
    """`updateNetworkLayer` carries the mesh patch semantics: wholesale replace, dropped danglers."""
    ctx = authenticated_context
    collection = await _collection(ctx)
    scene = await _scene_for(ctx, collection)

    created = await _create_layer(
        ctx, scene, collection,
        colorBys=[
            {"kind": "GRAPH", "attribute": "strahler", "colormap": "VIRIDIS"},
            {"kind": "GRAPH", "attribute": "degree", "colormap": "VIRIDIS"},
        ],
        activeColorBy=1,
    )
    assert not created.errors, created.errors
    layer_id = created.data["createNetworkLayer"]["id"]

    # Shorten the picker without renaming the active index: the dangler is dropped, not kept.
    updated = await schema.execute(
        UPDATE_LAYER,
        context_value=ctx,
        variable_values={"input": {"id": layer_id, "colorBys": [{"kind": "GRAPH", "attribute": "strahler", "colormap": "VIRIDIS"}]}},
    )
    assert not updated.errors, updated.errors
    assert updated.data["updateNetworkLayer"]["activeColorBy"] is None
    assert len(updated.data["updateNetworkLayer"]["colorBys"]) == 1

    # `[]` clears; an omitted picker leaves the other settings alone.
    cleared = await schema.execute(
        UPDATE_LAYER,
        context_value=ctx,
        variable_values={"input": {"id": layer_id, "colorBys": []}},
    )
    assert not cleared.errors, cleared.errors
    assert cleared.data["updateNetworkLayer"]["colorBys"] == []


def test_the_colour_union_publishes_every_arm() -> None:
    """Every member names every flat spelling it belongs to, and GRAPH names only the network one.

    The membership names are hardcoded strings in `core/render/layer/inputs.py`, and the
    directive is what a generated client rebuilds its tagged union from -- so a forgotten name
    is not an error anywhere, it is a client that quietly loses an arm. Pinned here the way
    `test_transform_param_validation` pins the transform union.
    """
    sdl = schema.as_str()
    memberships = {
        "ColumnColorByInput": {"LabelColorByInput", "MeshColorByInput", "NetworkColorByInput"},
        "SparseColorByInput": {"LabelColorByInput", "MeshColorByInput", "NetworkColorByInput"},
        "GraphColorByInput": {"NetworkColorByInput"},
    }
    for member, unions in memberships.items():
        start = sdl.find(f"input {member} ")
        assert start >= 0, f"{member} missing from the SDL -- dropped from `color_by_union_types`?"
        header = sdl[start : sdl.find("{", start)]
        for union in unions:
            assert f'@unionElementOf(union: "{union}", discriminator: "kind", key: ' in header, f"{member} does not name {union}"
        named = {union for union in ("LabelColorByInput", "MeshColorByInput", "NetworkColorByInput") if f'union: "{union}"' in header}
        assert named == unions, f"{member} names {sorted(named)}, expected {sorted(unions)} -- a GRAPH arm on a mask or mesh is a picker entry no renderer can resolve"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_table_a_network_picker_names_is_refused(authenticated_context: HttpContext):
    """The PROTECT guard covers the network columns -- the gap the point pickers sat in."""
    ctx = authenticated_context
    collection = await _collection(ctx)
    scene = await _scene_for(ctx, collection)
    table = await _keyed_table(ctx, collection)

    created = await _create_layer(
        ctx, scene, collection,
        colorBys=[{"table": table, "column": "total_length", "colormap": "VIRIDIS"}],
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        "mutation Delete($input: DeleteTableDatasetInput!) { deleteTableDataset(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": table}},
    )
    assert result.errors, "a table a network layer colours by must not delete out from under it"
    assert "colour or filter" in str(result.errors[0])

PLANS = """
query Plans($system: ID!) {
  attributePlans(system: $system) {
    path { inverted }
    sample { __typename produces consumes ... on NetworkSample { store { id } } }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_network_collection_roots_a_plan(authenticated_context: HttpContext):
    """A table keyed by a network's object ids publishes a plan whose sample is the wireframe.

    The regression test for a missing re-export: `NetworkSample` was registered in the schema
    but not exported from `core.types`, so `attributePlans` raised AttributeError -- and took
    every other plan in the fact component down with it -- the first time discovery reached a
    network collection. Nothing short of running the query through the resolver catches that.
    """
    collection = await _collection(authenticated_context)
    await _keyed_table(authenticated_context, collection)

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(collection.coordinate_system.pk)})
    assert not result.errors, result.errors
    (plan,) = result.data["attributePlans"]
    assert plan["path"] == [], "rooted where we probed"
    assert plan["sample"]["__typename"] == "NetworkSample", "nothing is sampled: the id came with the picked segment"
    assert plan["sample"]["store"]["id"] == str(collection.store.pk)
    assert plan["sample"]["produces"] == ["object_id"]
