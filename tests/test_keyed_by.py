"""`keyedBy`: authoring the FIELD dereference in the same call that creates the table.

A table of per-object measurements stands in two relations to the mask its rows were
measured out of, and they run in opposite directions:

* ``derivedFrom`` -- table -> mask, the lineage. UNMAPPABLE, because a row is an object and
  an object is not anywhere, so nothing places the table.
* ``keyedBy`` -- mask -> table, the dereference. A FIELD, and the only direction
  ``attributePlans`` can discover, because it looks for FIELD edges *landing on* a table.

Both are true, so both are written, and the point of ``keyedBy`` is that one mutation says
both instead of a create followed by a ``createTransformation`` that can fail after the
table is already stored.

The load-bearing test is :func:`test_keyed_by_derives_the_axis_split_from_the_two_spaces`:
the caller states no axes at all. The rank rule says the axes a FIELD does not consume pass
through by name, which leaves exactly one split for a given pair of systems -- so asking a
client for it would only be an opportunity to get it wrong, and a FIELD whose axes are
wrong is not refused at read, it is silently skipped.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) {
    id
    name
    columns { name role references { id name } }
    coordinateSystem { id axes { name type } }
    derivedFrom { id kind input { id } output { id } }
  }
}
"""

PLANS = """
query Plans($system: ID!) {
  attributePlans(system: $system) {
    edge { id kind name validity input { id } output { id } inputAxes outputAxes }
    path { inverted }
    sample {
      __typename
      system { id } consumes produces passthrough
      ... on ArraySample { store { id } }
      ... on MeshSample { store { id } }
    }
    hops {
      table { id name }
      lookup { keyColumns { axis column { name } } attributes { name references { id name } } }
    }
  }
}
"""

#: A timelapse mask: per-frame object ids, so the table key is (t, i) and t passes through.
TYX_AXES = [
    seed.axis("t", enums.AxisType.TIME),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]

#: The columns of a per-object table keyed by (t, i). Declared time-then-custom-then-space,
#: which is the axis type ordering the table's space inherits.
OBJECT_COLUMNS = [
    {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
    {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]


async def _parquet(ctx: HttpContext, key: str) -> models.ParquetStore:
    return await sync_to_async(models.ParquetStore.objects.create)(path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization, populated=True)


async def _mask(ctx: HttpContext, name: str = "nuclei labels", axes: list | None = None, shapes: list | None = None) -> models.ArrayDataset:
    """A label-mask dataset whose level-0 array has a zarr store a plan can name."""
    dataset = await seed.create_array_dataset(ctx, name, axes=axes or TYX_AXES, shapes=shapes or [[10, 64, 64]])

    def attach() -> None:
        store = models.ZarrStore.objects.create(path=f"s3://zarr/{name}", bucket="zarr", key=name.replace(" ", "-"), organization=ctx.request.organization)
        array = dataset.data_arrays.get(level=0)
        array.store = store
        array.save()

    await sync_to_async(attach)()
    return dataset


async def _create(ctx: HttpContext, name: str, columns: list[dict], **extra: object) -> object:
    return await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, columns, **extra)},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keyed_by_derives_the_axis_split_from_the_two_spaces(authenticated_context: HttpContext):
    """One call, no axes stated, and the plan comes out with the split already right.

    The caller names a mask and nothing else. `(t,y,x)` against `(t,i)` can only mean
    consume `(y,x)`, produce `i`, pass `t` through -- the axes the two spaces share are the
    ones that pass through, and that is the whole rule.
    """
    mask = await _mask(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await _create(authenticated_context, "nuclei morphology", OBJECT_COLUMNS, identified_by={"i": [{"kind": "DATASET", "dataset": str(mask.pk)}]})
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]

    plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert not plans.errors, plans.errors
    assert len(plans.data["attributePlans"]) == 1, "keyedBy authored an edge attributePlans can find"

    plan = plans.data["attributePlans"][0]
    assert plan["hops"][0]["table"]["id"] == table["id"]
    assert plan["sample"]["consumes"] == ["y", "x"], "derived, not stated"
    assert plan["sample"]["produces"] == ["i"]
    assert plan["sample"]["passthrough"] == ["t"], "the shared axis passes through by name"
    assert plan["sample"]["__typename"] == "ArraySample", "a mask is read at a coordinate"
    assert plan["sample"]["system"]["id"] == str(mask_system.pk), "a mask's own pixels are the map"
    assert [(key["axis"], key["column"]["name"]) for key in plan["hops"][0]["lookup"]["keyColumns"]] == [("t", "t"), ("i", "i")]

    edge = plan["edge"]
    assert edge["kind"] == "FIELD"
    assert edge["input"]["id"] == str(mask_system.pk), "the edge runs mask -> table"
    assert edge["output"]["id"] == table["coordinateSystem"]["id"]
    assert edge["inputAxes"] == ["y", "x"]
    assert edge["outputAxes"] == ["i"]
    assert edge["name"] == "nuclei labels -> nuclei morphology"
    assert edge["validity"] == "MANUAL", "an authored claim unless the caller says otherwise"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keyed_by_and_derived_from_are_two_edges_in_opposite_directions(authenticated_context: HttpContext):
    """Stating both in one call writes both, and neither stands in for the other.

    This is the whole design question `keyedBy` answers: the table really is derived from
    the mask, but that sentence and "the mask's pixels index into the table" are different
    claims running opposite ways, so folding the FIELD into `derivedFrom` would put it in
    the direction no plan can find.
    """
    mask = await _mask(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await _create(
        authenticated_context,
        "nuclei morphology",
        OBJECT_COLUMNS,
        derivedFrom=[{"kind": "DATASET", "dataset": str(mask.pk), "valueRelation": "TRANSFORMED"}],
        keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk), "validity": "VALIDATED"}],
    )
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]
    table_system = table["coordinateSystem"]["id"]

    # The lineage: table -> mask, and unmappable because a row is an object.
    assert len(table["derivedFrom"]) == 1
    lineage = table["derivedFrom"][0]
    assert lineage["kind"] == "UNMAPPABLE", "no transform stated means no geometry claimed"
    assert lineage["input"]["id"] == table_system
    assert lineage["output"]["id"] == str(mask_system.pk)

    # The dereference: mask -> table, and the one a plan is built from.
    plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert not plans.errors, plans.errors
    (plan,) = plans.data["attributePlans"]
    assert plan["edge"]["input"]["id"] == str(mask_system.pk)
    assert plan["edge"]["output"]["id"] == table_system
    assert plan["edge"]["validity"] == "VALIDATED", "the caller checked the ids against the rows"

    assert plan["edge"]["id"] != lineage["id"], "two facts, two rows"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sibling_masks_each_get_their_own_edge(authenticated_context: HttpContext):
    """`keyedBy` is a list: two masks may key one table, one edge each."""
    nuclei = await _mask(authenticated_context, "nuclei labels")
    cells = await _mask(authenticated_context, "cell labels")
    nuclei_system = await sync_to_async(lambda: nuclei.intrinsic_coordinate_system)()
    cells_system = await sync_to_async(lambda: cells.intrinsic_coordinate_system)()

    result = await _create(
        authenticated_context,
        "object morphology",
        OBJECT_COLUMNS,
        keyed_by=[{"kind": "DATASET", "dataset": str(nuclei.pk)}, {"kind": "DATASET", "dataset": str(cells.pk)}],
    )
    assert not result.errors, result.errors

    for system in (nuclei_system, cells_system):
        plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
        assert not plans.errors, plans.errors
        found = [plan for plan in plans.data["attributePlans"] if plan["edge"]["input"]["id"] == str(system.pk)]
        assert len(found) == 1, f"one edge rooted at {system.pk}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_with_no_axes_cannot_be_keyed_at_all(authenticated_context: HttpContext):
    """It used to be a runtime refusal. It is now inexpressible, which is the better guarantee.

    A table with no `axes` has the synthetic `object` axis, which has no column behind it, so
    an id could never be looked up in it -- the edge wrote fine and `attributePlans` silently
    returned nothing, which is the failure the old check turned into a sentence. Identification
    travels *on an axis* now, and this table has none, so there is nowhere to put one. The
    only way to key it is to give it an axis, which is exactly the fix the old message asked
    for -- so the refusal has become the shape of the input.
    """
    mask = await _mask(authenticated_context)

    result = await _create(
        authenticated_context,
        "measurements",
        [{"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}],
    )
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]
    assert [axis["name"] for axis in table["coordinateSystem"]["axes"]] == ["object"], "the synthetic axis"

    # And the input has no field that could name a source for it: `identifiedBy` lives on the
    # declared `ColumnInput`s, and the synthetic axis corresponds to no declared column.
    assert "keyedBy" not in str(schema.as_str().split("input CreateTableDatasetInput")[1][:400])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keyed_by_refuses_a_mask_that_consumes_nothing(authenticated_context: HttpContext):
    """A mask whose every axis is also a table axis collapses nothing, so it is not a map."""
    mask = await _mask(authenticated_context)

    result = await _create(
        authenticated_context,
        "localizations",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
        keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk)}],
    )
    assert result.errors
    assert "consume nothing" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keyed_by_refuses_a_table_that_produces_nothing(authenticated_context: HttpContext):
    """A table with no coordinate of its own has nothing for the pixels to supply.

    The refusal is now the *specific* one, because the caller names the axis: `t` is an axis of
    the mask too, so it passes through rather than being supplied. "The edge would produce
    nothing" was what that looked like from the derivation's side, and said nothing about which
    axis was meant -- it is now unreachable through either create, both of which state
    `produces`.
    """
    mask = await _mask(authenticated_context)

    result = await _create(
        authenticated_context,
        "per frame",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "count", "dtype": "BIGINT", "role": "ATTRIBUTE"},
        ],
        identified_by={"t": [{"kind": "DATASET", "dataset": str(mask.pk)}]},
    )
    assert result.errors
    message = str(result.errors[0])
    assert "was declared to key 't'" in message
    assert "passes through rather than being supplied" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keyed_by_refuses_the_same_mask_twice(authenticated_context: HttpContext):
    """A second edge between the same pair says nothing the first did not."""
    mask = await _mask(authenticated_context)

    result = await _create(
        authenticated_context,
        "nuclei morphology",
        OBJECT_COLUMNS,
        keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk)}, {"kind": "DATASET", "dataset": str(mask.pk)}],
    )
    assert result.errors
    assert "distinct source" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_refused_key_edge_leaves_no_table_behind(authenticated_context: HttpContext):
    """The whole creation is one transaction, so a bad mask does not strand a table."""
    mask = await _mask(authenticated_context)

    result = await _create(
        authenticated_context,
        "per frame",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "count", "dtype": "BIGINT", "role": "ATTRIBUTE"},
        ],
        identified_by={"t": [{"kind": "DATASET", "dataset": str(mask.pk)}]},
    )
    assert result.errors
    assert not await sync_to_async(models.TableDataset.objects.filter(name="per frame").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keying_a_table_does_not_make_the_mask_derived_from_it(authenticated_context: HttpContext):
    """The FIELD leaves the mask's system and lands in a table's, which is the shape
    ``derivedFrom`` reports for a dataset -- so check it is not read as lineage.

    A mask is not computed *from* the table it keys; the arrow only happens to point that
    way because a dereference runs from pixels to records.
    """
    mask = await _mask(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await _create(authenticated_context, "nuclei morphology", OBJECT_COLUMNS, identified_by={"i": [{"kind": "DATASET", "dataset": str(mask.pk)}]})
    assert not result.errors, result.errors

    mask_lineage = await schema.execute(
        "query M($id: ID!) { arrayDataset(id: $id) { derivedFrom { id kind output { id } } } }",
        context_value=authenticated_context,
        variable_values={"id": str(mask.pk)},
    )
    assert not mask_lineage.errors, mask_lineage.errors
    assert mask_lineage.data["arrayDataset"]["derivedFrom"] == [], "keying a table is not a lineage claim about the mask"

    # and the edge really does exist, rooted at the mask
    assert await sync_to_async(models.Transformation.objects.filter(input=mask_system, kind=enums.TransformKindChoices.FIELD.value).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keyed_by_refuses_a_table_with_two_id_axes(authenticated_context: HttpContext):
    """One pixel holds one value, so one mask cannot supply two ids.

    `assert_field_produces` refuses this anyway, but from the field's side -- it reads as
    though the mask were at fault and suggests giving it a value axis, which would turn a
    label mask into a warp field. The fixable thing is the table's second id column, and
    the shape RFC-7 wants for a second object space is a TABLE identification on a data
    column, so the error says that.
    """
    mask = await _mask(authenticated_context)

    result = await _create(
        authenticated_context,
        "contacts",
        [
            {"name": "nucleus_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "overlap", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
        identified_by={"nucleus_id": [{"kind": "DATASET", "dataset": str(mask.pk)}]},
    )
    assert result.errors
    message = str(result.errors[0])
    assert "a source supplies one" in message
    assert "['nucleus_id', 'cell_id']" in message, "name the two it would have to supply"
    assert "kind: TABLE" in message, "point at the mechanism that does work"
    assert "value axis" not in message, "the mask is not the thing to fix"
    assert not await sync_to_async(models.TableDataset.objects.filter(name="contacts").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_second_object_space_is_a_reference_not_an_axis(authenticated_context: HttpContext):
    """The shape the refusal above recommends: one INDEX axis, the other id a data column.

    This is RFC-7's line -- FIELD is the single crossing from geometry into record-land, and
    once inside, a relation between tables is a foreign key on a column. So the table stays
    keyable by the mask *and* still relates to the other object space.
    """
    mask = await _mask(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    cells = await _create(authenticated_context, "cells", [{"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}])
    assert not cells.errors, cells.errors

    result = await _create(
        authenticated_context,
        "nuclei",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "nucleus_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "cell_id", "dtype": "BIGINT", "role": "ID", "references": cells.data["createTableDataset"]["id"]},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
        keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk)}],
    )
    assert not result.errors, result.errors
    nuclei = result.data["createTableDataset"]
    cells_id = cells.data["createTableDataset"]["id"]

    # The relation landed, as a schema fact on the column.
    column = next(col for col in nuclei["columns"] if col["name"] == "cell_id")
    assert column["references"] == {"id": cells_id, "name": "cells"}

    plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert not plans.errors, plans.errors
    (plan,) = plans.data["attributePlans"]
    assert plan["sample"]["produces"] == ["nucleus_id"], "one id from the mask"
    assert plan["sample"]["passthrough"] == ["t"]

    # ...and it comes back through the plan, which is what makes the shape usable: one
    # hover yields the nucleus' attributes *and* the foreign key to follow into `cells`.
    # The second hop is the client's, per RFC-7 -- a plan is one sample and one lookup.
    attributes = {attr["name"]: attr["references"] for attr in plan["hops"][0]["lookup"]["attributes"]}
    assert attributes["cell_id"] == {"id": cells_id, "name": "cells"}
    assert attributes["area"] is None


# --- keyed by a mesh collection ------------------------------------------------------
#
# The same relation over a different substrate. A mask materialises the id per pixel; a
# collection materialises it per geometry row, so a client that picked a surface is already
# holding one. What both share -- and the whole of what a FIELD asserts -- is that standing
# somewhere in the source's space yields an id. See `docs/field-vs-references.md`.


async def _mesh_collection(ctx: HttpContext, axes: list[dict], *, version: str = "v1") -> models.MeshCollection:
    """A mesh collection in a space of its own, derived from nothing."""
    store = await seed.create_fabriks_store(ctx)
    result = await schema.execute(
        "mutation Create($input: CreateMeshCollectionInput!) { createMeshCollection(input: $input) { id coordinateSystem { id } } }",
        context_value=ctx,
        variable_values={"input": {"version": version, "store": str(store.pk), "axes": axes}},
    )
    assert not result.errors, result.errors
    return await sync_to_async(models.MeshCollection.objects.get)(id=result.data["createMeshCollection"]["id"])


#: A collection with no time axis, keyed by a table whose only coordinate is the object id.
ZYX_MESH_AXES = [{"name": "z", "type": "SPACE"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]

#: A per-frame collection, which shares `t` with its table and so passes it through.
TYX_MESH_AXES = [{"name": "t", "type": "TIME"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]

SHAPE_COLUMNS = [
    {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "volume", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_collection_keys_a_table(authenticated_context: HttpContext):
    """A collection is a keying source in its own right, not only a probe point.

    Before this, the only route from a mesh to a table ran through the mask it was
    extracted from -- which a collection imported from an STL does not have, and which
    makes a client sample a mask to recover an id it is already holding.

    Nothing is stated about the axes: the same split that reads a mask reads a collection.
    Its three spatial axes are all consumed, because the table shares none of them.
    """
    collection = await _mesh_collection(authenticated_context, ZYX_MESH_AXES)
    system = await sync_to_async(lambda: collection.coordinate_system)()

    result = await _create(authenticated_context, "shape stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]

    plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
    assert not plans.errors, plans.errors
    (plan,) = plans.data["attributePlans"]

    assert plan["hops"][0]["table"]["id"] == table["id"]
    assert plan["sample"]["__typename"] == "MeshSample", "nothing is sampled: the id came with the picked surface"
    assert plan["sample"]["store"]["id"] == str(await sync_to_async(lambda: collection.store_id)()), "the fabriks store, for a worker that did not do the picking"
    assert plan["sample"]["consumes"] == ["z", "y", "x"], "the table shares no axis, so all three are consumed"
    assert plan["sample"]["produces"] == ["object"]
    assert plan["sample"]["passthrough"] == [], "nothing is shared, so nothing passes through"
    assert plan["sample"]["system"]["id"] == str(system.pk), "the collection's own geometry is the map"
    assert [(key["axis"], key["column"]["name"]) for key in plan["hops"][0]["lookup"]["keyColumns"]] == [("object", "object")]

    edge = plan["edge"]
    assert edge["kind"] == "FIELD"
    assert edge["input"]["id"] == str(system.pk), "the edge runs collection -> table"
    assert edge["output"]["id"] == table["coordinateSystem"]["id"]
    assert edge["name"] == "v1 -> shape stats", "a collection has a version where a dataset has a name"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_per_frame_collection_passes_time_through(authenticated_context: HttpContext):
    """The shared axis passes through by name here exactly as it does for a mask.

    Worth its own test because it is the case that proves the axis split was not special-cased
    for collections: `(t,y,x)` against `(t,object)` can only mean consume `(y,x)`.
    """
    collection = await _mesh_collection(authenticated_context, TYX_MESH_AXES, version="v2")
    system = await sync_to_async(lambda: collection.coordinate_system)()

    columns = [
        {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
        {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "volume", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    ]
    result = await _create(authenticated_context, "tracked shapes", columns, keyed_by=[{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}])
    assert not result.errors, result.errors

    plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
    assert not plans.errors, plans.errors
    (plan,) = plans.data["attributePlans"]

    assert plan["sample"]["consumes"] == ["y", "x"]
    assert plan["sample"]["produces"] == ["object"]
    assert plan["sample"]["passthrough"] == ["t"], "the axis the two share binds the second key"
    assert [(key["axis"], key["column"]["name"]) for key in plan["hops"][0]["lookup"]["keyColumns"]] == [("t", "t"), ("object", "object")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collection_is_named_by_its_version_in_refusals(authenticated_context: HttpContext):
    """Every refusal used to read `dataset.name`, which a MeshCollection has not got.

    A collection carries `version` instead, so the message raised an AttributeError -- a
    500 rather than a sentence -- on every path this change makes reachable. Pinned here
    because the failure is invisible until a caller gets something wrong.
    """
    collection = await _mesh_collection(authenticated_context, ZYX_MESH_AXES, version="v20260713-a3f9")

    # The collection's axes are all axes of the table too, so the edge would consume
    # nothing: there is no map, because nothing is collapsed into an id.
    columns = [
        {"name": "z", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
    ]
    result = await _create(
        authenticated_context,
        "vertices",
        columns,
        identified_by={"z": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]},
    )

    assert result.errors
    message = str(result.errors[0])
    assert "v20260713-a3f9" in message, "the collection is named by its version"
    assert "consume nothing" in message
    assert not await sync_to_async(models.TableDataset.objects.filter(name="vertices").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_keyed_by_refuses_a_lens_and_a_table_by_construction(authenticated_context: HttpContext):
    """The union advertises two members, so the other four are a schema error, not a runtime one.

    `DerivationSourceKind` carries six; keying reuses neither the enum nor its breadth,
    because a lens owns nothing to dereference and a table is already record-land -- where
    the relation is `Column.references`. Advertising those and refusing them in a
    resolver would be a schema that says yes where the server says no.
    """
    result = await _create(authenticated_context, "shape stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "LENS", "lens": "1"}]})
    assert result.errors
    assert "LENS" in str(result.errors[0]), "refused by the enum, before any resolver runs"
    assert not await sync_to_async(models.TableDataset.objects.filter(name="shape stats").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collection_keyed_to_its_own_table_still_reaches_its_masks(authenticated_context: HttpContext):
    """The two routes coexist, and the local one sorts first.

    A collection extracted from a mask could already reach that mask's table, one
    derivation hop away, by sampling the mask -- which is the round-trip this change
    removes, not replaces. Both plans come back: the mesh's own, rooted where the caller
    probed, and the mask's behind a forward step.

    Probed at the collection, deliberately: the derivation is stored collection -> mask, so
    this walks it *forwards*, which consults neither rank nor invertibility.
    """
    mask = await _mask(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    await _create(authenticated_context, "nuclei morphology", OBJECT_COLUMNS, identified_by={"i": [{"kind": "DATASET", "dataset": str(mask.pk)}]})

    store = await seed.create_fabriks_store(authenticated_context)
    created = await schema.execute(
        "mutation Create($input: CreateMeshCollectionInput!) { createMeshCollection(input: $input) { id coordinateSystem { id } } }",
        context_value=authenticated_context,
        variable_values={
            "input": {
                "version": "v3",
                "store": str(store.pk),
                "axes": TYX_MESH_AXES,
                "derivedFrom": [{"kind": "DATASET", "dataset": str(mask.pk), "transform": {"kind": "IDENTITY"}}],
            }
        },
    )
    assert not created.errors, created.errors
    collection = await sync_to_async(models.MeshCollection.objects.get)(id=created.data["createMeshCollection"]["id"])
    mesh_system = created.data["createMeshCollection"]["coordinateSystem"]["id"]

    surfaces = [
        {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
        {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "curvature", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    ]
    await _create(authenticated_context, "surface stats", surfaces, keyed_by=[{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}])

    plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": mesh_system})
    assert not plans.errors, plans.errors
    local, remote = plans.data["attributePlans"]

    assert local["path"] == [], "the collection's own plan is rooted where the caller probed, and sorts first"
    assert local["sample"]["__typename"] == "MeshSample"
    assert local["hops"][0]["table"]["name"] == "surface stats"

    assert [step["inverted"] for step in remote["path"]] == [False], "the derivation is stored collection -> mask, walked forwards"
    assert remote["sample"]["__typename"] == "ArraySample"
    assert remote["sample"]["system"]["id"] == str(mask_system.pk)
    assert remote["hops"][0]["table"]["name"] == "nuclei morphology"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_referenced_index_axis_is_identified_and_the_field_need_not_produce_it(authenticated_context: HttpContext):
    """The product-space case: a row identified by a *pair*, and a mask can only supply one.

    A contact map is indexed by (nucleus, cell). A row is not a nucleus carrying a cell as an
    attribute -- it is the pair -- so both are COORDINATE axes, and the table's space is
    genuinely two-dimensional. One pixel holds one value, so the mask supplies exactly one of
    them, which is what `test_keyed_by_refuses_a_table_with_two_id_axes` is about.

    The other is identified by ``references``: its positions *are* rows of another table. That
    is the relation `Column.references` already carries, said of an axis rather than of a
    data column, and it is what lets the rank rule account for an axis the edge does not
    produce -- every axis of a FIELD's target must be accounted for by the edge **or by its own
    identification**.

    Deliberately distinct from :func:`test_a_second_object_space_is_a_reference_not_an_axis`,
    where the second id is an attribute *of* a row and the table's space stays one-dimensional.
    Both shapes are legal and they say different things.
    """
    mask = await _mask(authenticated_context)

    cells = await _create(
        authenticated_context,
        "cells",
        [
            {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    assert not cells.errors, cells.errors
    cell_table = cells.data["createTableDataset"]["id"]

    result = await _create(
        authenticated_context,
        "contacts",
        [
            {"name": "nucleus_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX", "references": cell_table},
            {"name": "overlap", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
        identified_by={"nucleus_id": [{"kind": "DATASET", "dataset": str(mask.pk)}]},
    )
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]

    axes = [axis["name"] for axis in table["coordinateSystem"]["axes"]]
    assert axes == ["nucleus_id", "cell_id"], "both are coordinates: the row is the pair"

    referenced = {column["name"]: column["references"] for column in table["columns"]}
    assert referenced["cell_id"] is not None and referenced["cell_id"]["name"] == "cells"
    assert referenced["nucleus_id"] is None, "the axis the mask supplies identifies nothing elsewhere"

    # And **no plan**, deliberately. A worker holding what this edge supplies has one id, not
    # the pair, so `cell_id` could only be bound to nothing -- and dropping it from the `WHERE`
    # instead returns every row of that half, silently, where one was meant. A lookup this
    # cannot state honestly is one it does not state, exactly as for the degenerate table.
    # Answering a *slice* rather than a row is a different lookup kind, not a hole in this one.
    plans = await schema.execute(
        PLANS,
        context_value=authenticated_context,
        variable_values={"system": str((await sync_to_async(lambda: mask.intrinsic_coordinate_system)()).pk)},
    )
    assert not plans.errors, plans.errors
    assert not [plan for plan in plans.data["attributePlans"] if plan["hops"][0]["table"]["name"] == "contacts"], (
        "a product-space table has no executable row lookup, so it publishes no plan"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_product_space_table_is_offered_to_no_picker(authenticated_context: HttpContext):
    """Reachable by an edge, and still not something a colouring can resolve.

    The edge is a real fact -- the mask's pixels are nucleus ids, and those are half of a
    contact's identity. What is missing is the other half: a colouring names a table and a
    column and has nowhere to say *which* cell, so it could no more resolve a row than the plan
    could. Offering it anyway would break the one invariant the options query exists to hold --
    that the set it returns is exactly the set the mutation accepts.
    """
    from core.logic import attribute_plans as attribute_plans_logic

    mask = await _mask(authenticated_context)
    cells = await _create(
        authenticated_context,
        "cells",
        [
            {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    assert not cells.errors, cells.errors

    plain = await _create(authenticated_context, "nuclei morphology", OBJECT_COLUMNS, identified_by={"i": [{"kind": "DATASET", "dataset": str(mask.pk)}]})
    assert not plain.errors, plain.errors

    product = await _create(
        authenticated_context,
        "contacts",
        [
            {"name": "nucleus_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX", "references": cells.data["createTableDataset"]["id"]},
            {"name": "overlap", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
        identified_by={"nucleus_id": [{"kind": "DATASET", "dataset": str(mask.pk)}]},
    )
    assert not product.errors, product.errors

    system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    reachable = await sync_to_async(attribute_plans_logic.field_reachable_tables)(system, authenticated_context.request.organization)
    names = {table.name for table in reachable.values()}
    assert "nuclei morphology" in names, "an ordinary per-object table is still reachable"
    assert "contacts" not in names, "a product-space table resolves no row from this source"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_identified_axes_lookup_does_not_repeat_per_keying_source(authenticated_context: HttpContext):
    """`identified_axes(own_system)` is a property of the table, so it is read once.

    It sat inside the per-entry loop, costing two ORM traversals per keying source, and
    nothing in the loop could change its answer.

    Counted by call rather than by query, deliberately. A query count around the whole
    mutation would also carry every unrelated read the create does, so the assertion would
    be about all of them and would drift; what is on trial is one line's position relative
    to a `for`. Patching the function *is* the direct statement of that.

    The claim is the *slope*, not the count. `assert_edge_rank` shares this definition and
    runs once per edge, so one legitimate call is added per source; the hoisted one is not.
    Measured: 2 calls for one source and 3 for two, where the un-hoisted version went 2 and
    4. Verified by ablation -- move the line back inside the loop and the delta becomes 2.
    """
    from unittest.mock import patch

    from core.logic import coordinate_system as cs_logic
    from core.logic import graph as graph_logic

    async def calls_for(label: str, masks: list) -> int:
        counter = []
        real = graph_logic.identified_axes

        def counting(system):
            counter.append(system.pk)
            return real(system)

        with patch.object(cs_logic.graph_logic, "identified_axes", counting):
            result = await _create(
                authenticated_context,
                label,
                OBJECT_COLUMNS,
                keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk)} for mask in masks],
            )
        assert not result.errors, result.errors
        return len(counter)

    one = await _mask(authenticated_context, "one mask")
    two = await _mask(authenticated_context, "another mask")
    three = await _mask(authenticated_context, "a third mask")

    single = await calls_for("keyed once", [one])
    double = await calls_for("keyed twice", [two, three])

    assert double - single == 1, (
        f"one extra source added {double - single} calls, not the one `assert_edge_rank` "
        f"makes per edge ({single} -> {double}): the table's own lookup is back inside the loop"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_kind_that_cannot_key_is_refused_rather_than_raising_keyerror(authenticated_context: HttpContext):
    """The lookup into `_DERIVATION_SOURCES` was bare, so an unmapped kind was a 500.

    Reachable only through a bug -- every caller filters on `AUTHORS_EDGE` first -- but the
    keying kinds and the identifying kinds are two overlapping vocabularies whose one shared
    member is spelled differently in each: `TABLE_DATASET` in the derivation table, `TABLE`
    on a sparse axis. That is precisely the sort of near-miss that leaks, and a leak here
    was a traceback rather than a sentence.
    """
    from types import SimpleNamespace

    from core.logic import coordinate_system as cs_logic

    table = await _create(authenticated_context, "measurements", OBJECT_COLUMNS)
    assert not table.errors, table.errors
    dataset = await models.TableDataset.objects.aget(pk=table.data["createTableDataset"]["id"])
    own_system = await sync_to_async(lambda: dataset.coordinate_system)()

    entry = SimpleNamespace(kind="TABLE", source_id="1", name=None, validity=None)

    with pytest.raises(ValueError) as raised:
        await sync_to_async(cs_logic.write_key_edges)(
            info=None,
            own_system=own_system,
            keyed_by=[entry],
            name="measurements",
            ctx=None,
        )
    message = str(raised.value)
    assert "'TABLE' cannot key" in message
    assert "authors no edge" in message
    assert "TABLE_DATASET" in message, "the keyable kinds are listed"
