"""A table dataset is a parquet table that owns its space, placeable by its coordinate columns.

Two shapes in one object. Declare coordinate columns (x, y in nanometres) and the table
owns a placeable coordinate system whose axes ARE those columns -- a localization table
placed by an explicitly authored registration, like every other layer source. Declare none
and it degenerates to the old FeatureCollection: a single INDEX axis enumerating the rows,
and an UNMAPPABLE edge that records "this came from that image" while denying that any
pixel is a row.

The load-bearing tests here are the placement one (a point layer over a table dataset must
reach world through the table's own derivation edge), the rejection ones (an unplaced
source is refused at layer creation, never silently unplaced or fabricated into place),
and the column-mapping one (x/y/z are resolved by axis *name*, not array position, so a
table declared (x, y, z) is not silently transposed).
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

CREATE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) {
    id
    name
    store { id key }
    columns { name dtype role axisType unit order description }
    coordinateSystem { id residents { __typename } axes { name type unit order description } }
    derivedFrom {
      id kind
      output { id  }
      ... on UnmappableTransformation { reason }
    }
  }
}
"""

PLACEMENT = """
query Placement($id: ID!) {
  scene(id: $id) {
    layers {
      id
      placement
      pathToWorld {
        inverted
        transformation { id kind input { id  } output { id residents { __typename } } }
      }
    }
  }
}
"""

_AFFINE_3D = [
    [1.0, 0.0, 0.0, 5.0],
    [0.0, 1.0, 0.0, 5.0],
    [0.0, 0.0, 1.0, 0.0],
]


async def _parquet(ctx: HttpContext, key: str) -> models.ParquetStore:
    return await sync_to_async(models.ParquetStore.objects.create)(path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization, populated=True)


async def _create(ctx: HttpContext, key: str, **input_fields) -> dict:
    store = await _parquet(ctx, key)
    columns = input_fields.pop("columns", [])
    store_columns, declared = seed.split_declaration(columns)
    store.columns = [{"name": name, "type": dtype, "nullable": True} for name, dtype in store_columns]
    await sync_to_async(store.save)(update_fields=["columns"])
    payload = {"data": str(store.pk), "columns": declared, **input_fields}
    return await schema.execute(CREATE, context_value=ctx, variable_values={"input": payload})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_coordinate_table_owns_a_placeable_space(authenticated_context: HttpContext):
    """Its coordinate columns become SPACE axes of a TABLE system, in declared order and units."""
    result = await _create(
        authenticated_context,
        "localizations",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
            {"name": "photons", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]

    assert [r["__typename"] for r in table["coordinateSystem"]["residents"]] == ["TableDataset"], "the rows live in the table's own space"
    axes = table["coordinateSystem"]["axes"]
    assert [a["name"] for a in axes] == ["y", "x"]
    assert [a["type"] for a in axes] == ["SPACE", "SPACE"]
    assert all("nanometer" in a["unit"] for a in axes)
    assert [a["order"] for a in axes] == [0, 1]
    # The attribute column is declared but is not an axis.
    assert {c["name"] for c in table["columns"]} == {"y", "x", "photons"}
    assert next(c for c in table["columns"] if c["name"] == "photons")["role"] == "ATTRIBUTE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_measurement_table_is_the_feature_collection_case(authenticated_context: HttpContext):
    """No coordinate columns: a single INDEX axis, and an UNMAPPABLE edge to its source."""
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    result = await _create(
        authenticated_context,
        "morphology",
        name="nuclei morphology",
        columns=[{"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}, {"name": "mean_intensity", "dtype": "DOUBLE", "role": "ATTRIBUTE"}],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk)}],
    )
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]

    axes = table["coordinateSystem"]["axes"]
    assert [a["name"] for a in axes] == ["object"]
    assert axes[0]["type"] == "INDEX"
    assert axes[0]["unit"] is None
    assert table["derivedFrom"][0]["kind"] == "UNMAPPABLE"
    assert table["derivedFrom"][0]["output"]["id"] == str(system.pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_default_derivation_is_unmappable_even_with_coordinates(authenticated_context: HttpContext):
    """Naming a source is not authoring a map: without an explicit kind the edge stays UNMAPPABLE."""
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")  # (c, y, x)
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    result = await _create(
        authenticated_context,
        "loc-default",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk)}],
    )
    assert not result.errors, result.errors
    assert result.data["createTableDataset"]["derivedFrom"][0]["kind"] == "UNMAPPABLE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_identity_across_mismatched_rank_is_rejected(authenticated_context: HttpContext):
    """A (y,x) table cannot claim IDENTITY into a (c,y,x) source; BY_DIMENSION is how a rank change is stated."""
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")  # (c, y, x)
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    bad = await _create(
        authenticated_context,
        "loc-identity",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "IDENTITY"}}],
    )
    assert bad.errors, "an identity between a 2-axis table and a 3-axis image is a rank change in disguise"

    good = await _create(
        authenticated_context,
        "loc-bydim",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}}],
    )
    assert not good.errors, good.errors
    assert good.data["createTableDataset"]["derivedFrom"][0]["kind"] == "BY_DIMENSION"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_freestanding_table_has_no_edge(authenticated_context: HttpContext):
    """No source coordinate system: the table is a root, with no derivation edge."""
    result = await _create(
        authenticated_context,
        "free",
        name="molecules",
        columns=[{"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"}, {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"}],
    )
    assert not result.errors, result.errors
    assert result.data["createTableDataset"]["derivedFrom"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_out_of_order_coordinate_columns_are_accepted(authenticated_context: HttpContext):
    """A table column's position is arbitrary, so it is not a thing to refuse a table over.

    This used to assert `result.errors` -- space-before-time was rejected, on the reasoning
    that the render-axis derivation reads position. That reasoning is an array's: an array's
    axis order *is* its zarr's dimension order, so declaring it wrongly describes different
    bytes. A parquet's column order is whatever the frame happened to have.

    And the refusal protected nothing, measured against this codebase's own logic: `x, t` was
    refused while `t, x, y` was accepted, and both derived the same render axes, because
    `resolve_render_axes` finds the time axis by a *type scan* and never by position. The one
    arrangement that is genuinely catastrophic -- `x, y, z`, which derives x=z, z=x, fully
    transposed -- was accepted then and is accepted now. That hole is item 14 of the proposals
    doc and is a separate fix.

    So the axes are stored in the order the columns were given, and `create_table_axes` is the
    one axis writer that reasoned this out first: no space's axes are ordered by type.
    """
    result = await _create(
        authenticated_context,
        "bad-order",
        name="molecules",
        columns=[
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "t", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "TIME"},
        ],
    )
    assert not result.errors, result.errors
    axes = result.data["createTableDataset"]["coordinateSystem"]["axes"]
    assert [(a["name"], a["type"], a["order"]) for a in axes] == [("x", "SPACE", 0), ("t", "TIME", 1)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_spatial_axes_keep_the_order_they_were_declared_in(authenticated_context: HttpContext):
    """The part that is load-bearing, and the part nothing else checks.

    x is the *last* spatial axis, y the one before it, by position and never by name. Anything
    that reordered the axes on the way in -- a sort, a normalisation -- could turn a `(y, x)`
    declaration into `(x, y)` and mirror every scene built on the table, with nothing raising.
    """
    result = await _create(
        authenticated_context,
        "stable-order",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "t", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
    )
    assert not result.errors, result.errors
    axes = result.data["createTableDataset"]["coordinateSystem"]["axes"]
    assert [a["name"] for a in axes] == ["y", "t", "x"], "stored exactly as declared"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_coordinate_column_must_be_a_permitted_axis_type(authenticated_context: HttpContext):
    """A CHANNEL axis is not a coordinate: it indexes acquisitions, not positions.

    SPACE, TIME and INDEX are the permitted set -- INDEX because an id column's values are the
    coordinates of the space a label mask's pixels point into, exactly as an `x` column's are.
    """
    result = await _create(
        authenticated_context,
        "bad-type",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "c", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "CHANNEL"},
        ],
    )
    assert result.errors, "a coordinate column must be SPACE, TIME or INDEX"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_measurement_column_carries_its_unit_without_becoming_an_axis(authenticated_context: HttpContext):
    """An area is a quantity too: an ATTRIBUTE states what its values are in, and stays data.

    The dimension is not checked against anything -- an area is 'micrometer**2', not a length --
    and an explicit null is the ordinary case (a count is in nothing).
    """
    result = await _create(
        authenticated_context,
        "morphology-units",
        name="nuclei morphology",
        columns=[
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "micrometer**2"},
            {"name": "mean_gfp", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "a.u."},
            {"name": "n_pixels", "dtype": "BIGINT", "role": "ATTRIBUTE", "unit": None},
        ],
    )
    assert not result.errors, result.errors
    columns = {c["name"]: c for c in result.data["createTableDataset"]["columns"]}

    assert columns["area"]["unit"] == "micrometer**2"
    assert columns["mean_gfp"]["unit"] == "a.u."
    assert columns["n_pixels"]["unit"] is None
    # None of them place a row: the table is still the degenerate enumerating space.
    assert [a["name"] for a in result.data["createTableDataset"]["coordinateSystem"]["axes"]] == ["object"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unparseable_column_unit_is_refused(authenticated_context: HttpContext):
    """The unit is a pint unit, checked when the variable is coerced -- before the resolver runs."""
    result = await _create(
        authenticated_context,
        "bad-unit",
        name="molecules",
        columns=[{"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "furlongs_per_fortnight"}],
    )
    assert result.errors, "an unparseable unit must not be stored"
    assert "not a valid unit" in str(result.errors[0])
    assert result.data is None, "coercion fails ahead of the resolver, so nothing is returned"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_that_is_not_measured_refuses_a_unit(authenticated_context: HttpContext):
    """An id in nanometres names a metric that does not exist -- the INDEX argument, by role."""
    result = await _create(
        authenticated_context,
        "id-unit",
        name="molecules",
        columns=[{"name": "object_id", "dtype": "BIGINT", "role": "ID", "unit": "nanometer"}],
    )
    assert result.errors, "only COORDINATE and ATTRIBUTE columns carry a unit"
    assert "is not measured" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_dataset_is_not_editable_beyond_its_name(authenticated_context: HttpContext):
    """The store, the columns and the coordinate system are fixed at creation.

    Stated in three docstrings and a type description, and until now asserted nowhere -- which
    is how those docstrings came to claim the opposite ("Mutable, like ArrayDataset -- a
    recomputation edits the store"). A reader who believes that builds a cache that goes
    silently stale. The API is the authority, so pin it here.
    """
    created = await _create(
        authenticated_context,
        "fixed-table",
        name="nuclei",
        columns=[{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}],
    )
    assert not created.errors, created.errors
    table = created.data["createTableDataset"]
    store_at_creation = table["store"]["id"]

    # The whole of the editable surface: two scalar fields, neither of them the data.
    sdl = schema.as_str()
    definition = sdl[sdl.find("input UpdateTableDatasetInput ") : sdl.find("\n}", sdl.find("input UpdateTableDatasetInput "))]
    fields = {line.strip().split(":")[0] for line in definition.split("\n") if ":" in line and not line.strip().startswith('"')}
    assert fields == {"id", "name", "description"}, f"updateTableDataset must not reach the store, the columns or the space, but takes {fields}"

    renamed = await schema.execute(
        "mutation Update($input: UpdateTableDatasetInput!) { updateTableDataset(input: $input) { id name } }",
        context_value=authenticated_context,
        variable_values={"input": {"id": table["id"], "name": "renamed"}},
    )
    assert not renamed.errors, renamed.errors
    assert renamed.data["updateTableDataset"]["name"] == "renamed"

    # ...and the store and the declared schema are exactly where they were.
    after = await sync_to_async(lambda: models.TableDataset.objects.get(pk=table["id"]))()
    assert str(after.store_id) == store_at_creation, "renaming must not disturb the store"
    assert await sync_to_async(lambda: [c.name for c in after.columns.all()])() == ["i", "area"]

    # A table's own system is refused by updateCoordinateSystem: it serves shared spaces only.
    renamed_space = await schema.execute(
        "mutation Update($input: UpdateCoordinateSystemInput!) { updateCoordinateSystem(input: $input) { id name } }",
        context_value=authenticated_context,
        variable_values={"input": {"id": table["coordinateSystem"]["id"], "name": "hijacked"}},
    )
    assert renamed_space.errors, "a table owns its space; only a shared space has a lifecycle of its own"
    assert "data lives in it" in str(renamed_space.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_table_dataset_exposes_no_rows(authenticated_context: HttpContext):
    """Like FeatureCollection: the client reads the Parquet, GraphQL never paginates rows."""
    sdl = schema.as_str()
    definition = sdl[sdl.find("type TableDataset ") : sdl.find("\n}", sdl.find("type TableDataset "))]
    assert "\n  rows" not in definition
    assert "\n  columns" in definition, "the declared schema is exposed; the row data is not"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_point_layer_over_a_table_dataset_reaches_world(authenticated_context: HttpContext):
    """The whole feature: a localization table is placed through its own derivation edge."""
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")  # (c, y, x)
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    created = await _create(
        authenticated_context,
        "loc-placed",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}}],
    )
    assert not created.errors, created.errors
    table_id = created.data["createTableDataset"]["id"]

    scene = await seed.create_scene(authenticated_context, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.AFFINE.value,
            input=dataset.intrinsic_coordinate_system,
            output=scene.world,
            params={"affine": _AFFINE_3D},
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(register)()

    made = await schema.execute(
        """
        mutation Make($input: CreatePointLayerInput!) {
          createPointLayer(input: $input) { id xColumn yColumn }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "tableDataset": table_id}},
    )
    assert not made.errors, made.errors
    # The mappings come from the declared columns, by name.
    assert made.data["createPointLayer"]["xColumn"] == "x"
    assert made.data["createPointLayer"]["yColumn"] == "y"

    placement = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": str(scene.pk)})
    assert not placement.errors, placement.errors
    path = placement.data["scene"]["layers"][0]["pathToWorld"]
    assert path is not None, "a table dataset is placed by the image its rows were localized in"
    assert path[-1]["transformation"]["output"]["residents"] == [], "the path ends in a space nothing lives in: a world"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_column_mappings_are_by_name_not_position(authenticated_context: HttpContext):
    """A table declared (x, y, z) must not have x and z silently swapped by array-order logic."""
    created = await _create(
        authenticated_context,
        "xyz",
        name="molecules",
        columns=[
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "z", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
    )
    assert not created.errors, created.errors
    table_id = created.data["createTableDataset"]["id"]
    scene = await seed.create_scene(authenticated_context, "Composition")
    system = await sync_to_async(lambda: models.TableDataset.objects.get(pk=table_id).coordinate_system)()
    await seed.register_into_scene(authenticated_context, scene, system=system)

    made = await schema.execute(
        """
        mutation Make($input: CreatePointLayerInput!) {
          createPointLayer(input: $input) { id xColumn yColumn zColumn }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "tableDataset": table_id}},
    )
    assert not made.errors, made.errors
    layer = made.data["createPointLayer"]
    assert layer["xColumn"] == "x", "x must be the column named x, not the last spatial column"
    assert layer["yColumn"] == "y"
    assert layer["zColumn"] == "z"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unregistered_derived_table_layer_is_rejected(authenticated_context: HttpContext):
    """A derived table whose source is not registered has no path, so the layer is refused.

    Nothing is fabricated any more: the error names the mutation that closes the gap, and
    registering the source dataset (whose registration the table's derivation edge chains
    through) makes the same call succeed.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")  # (c, y, x)
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    created = await _create(
        authenticated_context,
        "loc-unreg",
        name="molecules",
        columns=[{"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"}, {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"}],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}}],
    )
    assert not created.errors, created.errors
    scene = await seed.create_scene(authenticated_context, "Composition")  # world (z, y, x), shares y/x

    make = """
    mutation Make($input: CreatePointLayerInput!) {
      createPointLayer(input: $input) { id placementValidity }
    }
    """
    variables = {"input": {"scene": str(scene.pk), "tableDataset": created.data["createTableDataset"]["id"]}}

    refused = await schema.execute(make, context_value=authenticated_context, variable_values=variables)
    assert refused.errors, "an unplaced source must be refused, not silently unplaced"
    assert "createTransformation" in str(refused.errors[0]), "the error points at the missing registration"
    edge_count = await sync_to_async(models.Transformation.objects.count)()

    await seed.register_into_scene(authenticated_context, scene, dataset)
    made = await schema.execute(make, context_value=authenticated_context, variable_values=variables)
    assert not made.errors, made.errors
    assert made.data["createPointLayer"]["placementValidity"] == "MANUAL", "the weakest edge is the authored registration"
    # The layer mutation itself wrote nothing to the graph: only the explicit registration did.
    assert await sync_to_async(models.Transformation.objects.count)() == edge_count + 2  # wrapper + IDENTITY child

    placement = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": str(scene.pk)})
    assert not placement.errors, placement.errors
    assert placement.data["scene"]["layers"][0]["pathToWorld"] is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_freestanding_table_layer_is_rejected_until_its_space_is_registered(authenticated_context: HttpContext):
    """A table derived from nothing has no source to chain through: register its own space, or no layer."""
    created = await _create(
        authenticated_context,
        "free-layer",
        name="molecules",
        columns=[{"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"}, {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"}],
    )
    assert not created.errors, created.errors
    table_id = created.data["createTableDataset"]["id"]
    scene = await seed.create_scene(authenticated_context, "Composition")

    make = """
    mutation Make($input: CreatePointLayerInput!) {
      createPointLayer(input: $input) { id }
    }
    """
    variables = {"input": {"scene": str(scene.pk), "tableDataset": table_id}}

    refused = await schema.execute(make, context_value=authenticated_context, variable_values=variables)
    assert refused.errors, "a freestanding table's space reaches no world until someone registers it"
    assert "createTransformation" in str(refused.errors[0])

    system = await sync_to_async(lambda: models.TableDataset.objects.get(pk=table_id).coordinate_system)()
    await seed.register_into_scene(authenticated_context, scene, system=system)
    made = await schema.execute(make, context_value=authenticated_context, variable_values=variables)
    assert not made.errors, made.errors

    placement = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": str(scene.pk)})
    assert not placement.errors, placement.errors
    assert placement.data["scene"]["layers"][0]["pathToWorld"] is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_registration_is_not_an_ingest_concern(authenticated_context: HttpContext):
    """createTableDataset takes no `scene`: a registration is a separate, explicit step."""
    sdl = schema.as_str()
    definition = sdl[sdl.find("input CreateTableDatasetInput ") : sdl.find("\n}", sdl.find("input CreateTableDatasetInput "))]
    assert definition, "the input type exists"
    assert "\n  scene" not in definition, "registering into a scene is createTransformation's job, not ingest's"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_column_descriptions_are_stored_and_carried_onto_axes(authenticated_context: HttpContext):
    """A column's description round-trips, and a coordinate column's is carried onto its axis."""
    result = await _create(
        authenticated_context,
        "described",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "description": "distance from the coverslip"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "photons", "dtype": "DOUBLE", "role": "ATTRIBUTE", "description": "photon count of the localization"},
        ],
    )
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]

    columns = {c["name"]: c for c in table["columns"]}
    assert columns["photons"]["description"] == "photon count of the localization"
    assert columns["x"]["description"] is None

    axes = {a["name"]: a for a in table["coordinateSystem"]["axes"]}
    assert axes["y"]["description"] == "distance from the coverslip", "the axis is the column, so it carries the same description"
    assert axes["x"]["description"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_point_layer_colours_by_its_own_table_without_an_edge(authenticated_context: HttpContext):
    """The one way a point layer differs: its objects ARE rows of a table.

    A mask's pixels and a collection's surfaces are geometry that has to be
    dereferenced into record-land across a FIELD edge. A point already stands
    there, so its own table is reachable with no edge at all — and a walk from
    the table's system would never return it, which is why it is seeded.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    created = await _create(
        authenticated_context,
        "loc-own-table",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "intensity", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}}],
    )
    assert not created.errors, created.errors
    table_id = created.data["createTableDataset"]["id"]
    scene = await seed.create_scene(authenticated_context, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.AFFINE.value,
            input=dataset.intrinsic_coordinate_system,
            output=scene.world,
            params={"affine": _AFFINE_3D},
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(register)()

    made = await schema.execute(
        """
        mutation Make($input: CreatePointLayerInput!) {
          createPointLayer(input: $input) {
            id
            activeColorBy
            colorBys { table column colormap kind }
          }
        }
        """,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.pk),
                "tableDataset": table_id,
                "colorBys": [{"table": table_id, "column": "intensity", "colormap": "VIRIDIS", "label": "Intensity"}],
                "activeColorBy": 0,
            }
        },
    )
    assert not made.errors, made.errors
    layer = made.data["createPointLayer"]
    assert layer["activeColorBy"] == 0
    (entry,) = layer["colorBys"]
    assert entry["column"] == "intensity"
    assert entry["kind"] == "COLUMN"

    # And the picker can be switched afterwards, which is what `updatePointLayer`
    # is for — it did not exist before the picker did.
    updated = await schema.execute(
        """
        mutation Retune($input: UpdatePointLayerInput!) {
          updatePointLayer(input: $input) { id activeColorBy colorBys { column } }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer["id"], "colorBys": [], "activeColorBy": None}},
    )
    assert not updated.errors, updated.errors
    assert updated.data["updatePointLayer"]["colorBys"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_point_layer_refuses_a_table_it_cannot_reach(authenticated_context: HttpContext):
    """Seeding its own table does not make every table reachable."""
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    created = await _create(
        authenticated_context,
        "loc-unreachable",
        name="molecules",
        columns=[
            {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
            {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
        ],
        derivedFrom=[{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}}],
    )
    table_id = created.data["createTableDataset"]["id"]
    scene = await seed.create_scene(authenticated_context, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.AFFINE.value,
            input=dataset.intrinsic_coordinate_system,
            output=scene.world,
            params={"affine": _AFFINE_3D},
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(register)()

    made = await schema.execute(
        """
        mutation Make($input: CreatePointLayerInput!) {
          createPointLayer(input: $input) { id }
        }
        """,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.pk),
                "tableDataset": table_id,
                "colorBys": [{"table": "999999", "column": "whatever", "colormap": "VIRIDIS"}],
            }
        },
    )
    assert made.errors, "expected an unreachable table to be refused"
    assert "not reachable" in str(made.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_that_declares_no_dtype_records_the_file_s_own(authenticated_context: HttpContext):
    """`dtype` is a fact about the file, so omitting it is a declaration, not a gap.

    The server read every column's type off the Parquet when the upload finished. A caller who
    repeats it is transcribing -- and the transcription has one specific failure mode, since
    `dtype` is DuckDB's vocabulary and a DataFrame speaks pandas'. So an omitted `dtype` is
    filled from the file and a stated one is still checked; both routes record the same value.
    """
    store = await _parquet(authenticated_context, "no-dtypes")
    store.columns = [
        {"name": "object_id", "type": "BIGINT", "nullable": False},
        {"name": "area", "type": "DOUBLE", "nullable": True},
        {"name": "label", "type": "VARCHAR", "nullable": True},
    ]
    await sync_to_async(store.save)(update_fields=["columns"])

    result = await schema.execute(
        CREATE,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "data": str(store.pk),
                "name": "undeclared types",
                # Not one dtype between them -- and `area` states a unit, which is the case the
                # option is for: something to say about a column that is not its type.
                "columns": [
                    {"name": "object_id", "axisType": "INDEX"},
                    {"name": "area", "unit": "micrometer**2"},
                    {"name": "label", "role": "LABEL"},
                ],
            }
        },
    )
    assert not result.errors, result.errors

    columns = result.data["createTableDataset"]["columns"]
    assert [(column["name"], column["dtype"]) for column in columns] == [
        ("object_id", "BIGINT"),
        ("area", "DOUBLE"),
        ("label", "VARCHAR"),
    ]
    assert [column["role"] for column in columns] == ["COORDINATE", "ATTRIBUTE", "LABEL"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stated_dtype_is_still_checked_against_the_file(authenticated_context: HttpContext):
    """Optional is not unchecked: a caller who asserts a type is held to it."""
    store = await _parquet(authenticated_context, "wrong-dtype")
    store.columns = [{"name": "area", "type": "DOUBLE", "nullable": True}]
    await sync_to_async(store.save)(update_fields=["columns"])

    result = await schema.execute(
        CREATE,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "data": str(store.pk),
                "name": "float64 is not a duckdb name",
                "columns": [{"name": "area", "dtype": "float64"}],
            }
        },
    )
    assert result.errors
    assert "declares types the Parquet does not have" in str(result.errors[0])
