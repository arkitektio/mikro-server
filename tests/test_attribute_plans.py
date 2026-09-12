"""Attribute plans: the read side the FIELD edge implies (RFC-7).

The server returns a plan; a worker executes it. A plan names the array to sample, the
axes to sample it on, the parquet to query and the columns to select -- and takes no
coordinate, so a client fetches it once and runs it per hover against the mask chunks it
is already rendering. The walk is pure over ``Transformation`` rows, so -- unlike every
other parquet path in this codebase -- it is testable for real: a plan reads nothing, so
there is nothing to mock and no object store to be unreachable.

The load-bearing tests: sibling fan-out (two tables off one mask, with differently named
produced axes -- the reason `produces` is per-edge), the warp-field skip (a FIELD whose
output is a pixel grid must not appear as a bogus plan; ablate the ``table_dataset_id``
guard in ``build_attribute_plans`` and it does), the refusals (a lens owns no array; an
array with no store cannot be sampled), and the SQL builder's injection test (identifiers
quoted, values only ever ``?``).

``Column.references`` is tested here too: it is the record-land sibling of the FIELD
edge (FIELD is the single crossing from geometry into records; between tables, a relation
is a schema fact), and the plans' `attributes` carry it to the client.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db.models.deletion import ProtectedError
from kante.context import HttpContext

from core import enums, models
from core.logic import attribute_plans as attribute_plans_logic
from core.logic import plan_sql
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) {
    id
    columns { name role references { id name } }
    coordinateSystem { id axes { name type } }
  }
}
"""

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id }
}
"""

PLANS = """
query Plans($system: ID!, $maxDepth: Int) {
  attributePlans(system: $system, maxDepth: $maxDepth) {
    edge { id version }
    path { transformation { id version } inverted }
    sample {
      __typename
      system { id } consumes produces passthrough
      ... on ArraySample { store { id } }
      ... on MeshSample { store { id } }
    }
    hops {
      index parent cardinality
      via { column { name } axis }
      table { id name }
      sparseDataset { id name }
      joinPath { table { name } column { name } }
      lookup {
        kind
        store { id }
        keyColumns { axis column { name dtype } }
        attributes { name references { id } }
        sparseArray { path } keyAxis keyHeld valueAxes
      }
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


async def _parquet(ctx: HttpContext, key: str, columns: list[tuple[str, str]] | None = None) -> models.ParquetStore:
    """A finished store carrying the file's own schema.

    The schema is load-bearing since 3b: `createTableDataset` reads a column's name and type
    off the store rather than from the caller, so a store that records none has nothing for a
    table to be declared over -- and `_resolve_store` refuses it rather than reaching for an S3
    no unit test has.
    """
    return await sync_to_async(models.ParquetStore.objects.create)(
        path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization,
        populated=True, columns=[{"name": name, "type": dtype, "nullable": True} for name, dtype in (columns or [])],
    )


async def _mask(ctx: HttpContext, name: str = "nuclei labels", axes: list | None = None, shapes: list | None = None, with_store: bool = True) -> models.ArrayDataset:
    """A label-mask dataset whose level-0 array has a zarr store a plan can name."""
    dataset = await seed.create_array_dataset(ctx, name, axes=axes or TYX_AXES, shapes=shapes or [[10, 64, 64]])
    if with_store:

        def attach() -> None:
            store = models.ZarrStore.objects.create(path=f"s3://zarr/{name}", bucket="zarr", key=name.replace(" ", "-"), organization=ctx.request.organization)
            array = dataset.data_arrays.get(level=0)
            array.store = store
            array.save()

        await sync_to_async(attach)()
    return dataset


async def _table(ctx: HttpContext, name: str, columns: list[dict]) -> dict:
    result = await schema.execute(CREATE_TABLE, context_value=ctx, variable_values={"input": await seed.table_input(ctx, name, columns)})
    assert not result.errors, result.errors
    return result.data["createTableDataset"]


async def _field_edge(ctx: HttpContext, mask_system: models.CoordinateSystem, table: dict, output_axes: list[str]) -> None:
    """The dereference: the mask's own pixels are the map, (y,x) consumed, ids produced."""
    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=ctx,
        variable_values={
            "input": {
                "input": str(mask_system.pk),
                "output": table["coordinateSystem"]["id"],
                "transform": {
                    "kind": "FIELD",
                    "field": str(mask_system.pk),
                    "inputAxes": ["y", "x"],
                    "outputAxes": output_axes,
                },
            }
        },
    )
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sibling_fan_out_returns_one_plan_per_table(authenticated_context: HttpContext):
    """Two FIELD edges off one mask are two plans, each zipped against its OWN produced axis.

    The produced axis is per-edge on purpose: objects names it `i`, intensity names it
    `label_id`, and a client zipping the sampled value against a shared key set would be
    wrong for one of them. Multi-table breadth is sibling fan-out -- no `value_column`,
    no join modelling, nothing beyond edges that exist today.
    """
    mask = await _mask(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    objects = await _table(
        authenticated_context,
        "nuclei morphology",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
            {"name": "mean_intensity", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    intensity = await _table(
        authenticated_context,
        "nuclei intensity",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "label_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "integrated", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    await _field_edge(authenticated_context, mask_system, objects, ["i"])
    await _field_edge(authenticated_context, mask_system, intensity, ["label_id"])

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert not result.errors, result.errors
    plans = result.data["attributePlans"]
    assert len(plans) == 2, "one plan per sibling table"

    by_table = {plan["hops"][0]["table"]["name"]: plan for plan in plans}
    morphology = by_table["nuclei morphology"]
    assert morphology["sample"]["consumes"] == ["y", "x"]
    assert morphology["sample"]["produces"] == ["i"]
    assert morphology["sample"]["passthrough"] == ["t"], "the axis the edge did not consume passes through by name"
    assert morphology["sample"]["system"]["id"] == str(mask_system.pk), "a mask's own pixels are the map"
    assert [(key["axis"], key["column"]["name"]) for key in morphology["hops"][0]["lookup"]["keyColumns"]] == [("t", "t"), ("i", "i")]
    # The statement is the worker's to build, from the wire shape as it arrives -- which is what
    # makes this a query-level test of the builder as well as of the plan.
    assert plan_sql.build_lookup_sql(morphology["hops"][0]["lookup"]) == 'SELECT "area", "mean_intensity" FROM read_parquet(?) WHERE "t" = ? AND "i" = ?'
    assert morphology["edge"]["version"] == 1, "the cache key rides on the edge"

    assert by_table["nuclei intensity"]["sample"]["produces"] == ["label_id"], "the produced axis is per-edge, never a shared key set"
    assert plan_sql.build_lookup_sql(by_table["nuclei intensity"]["hops"][0]["lookup"]) == 'SELECT "integrated" FROM read_parquet(?) WHERE "t" = ? AND "label_id" = ?'


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_warp_field_target_is_not_a_plan(authenticated_context: HttpContext):
    """A FIELD whose output is a pixel grid is a registration, not a dereference.

    ABLATION: drop the ``table_dataset_id is None`` guard in ``build_attribute_plans`` and
    this edge comes back as a bogus plan with no table to look anything up in.
    """
    mask = await _mask(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    atlas = await seed.create_array_dataset(authenticated_context, "atlas", axes=seed.YX_AXES, shapes=[[64, 64]])
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    def warp_edge() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.FIELD.value,
            input=mask_system,
            output=atlas.intrinsic_coordinate_system,
            field=mask_system,
            input_axes=["y", "x"],
            output_axes=["y", "x"],
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(warp_edge)()

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert not result.errors, result.errors
    assert result.data["attributePlans"] == [], "a pixel-grid target is skipped, not planned"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_degenerate_table_is_not_a_plan(authenticated_context: HttpContext):
    """A table with no coordinate columns enumerates rows on a synthetic axis nothing can bind.

    The `object` axis has no backing column, so there is no WHERE clause to write --
    positional parquet access is not a lookup a plan can state honestly. The same table is
    refused as a reference target, for the same reason.
    """
    mask = await _mask(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    degenerate = await _table(authenticated_context, "measurements", [{"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])

    def edge() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.FIELD.value,
            input=mask_system,
            output=models.CoordinateSystem.objects.get(pk=degenerate["coordinateSystem"]["id"]),
            field=mask_system,
            input_axes=["y", "x"],
            output_axes=["object"],
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(edge)()

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert not result.errors, result.errors
    assert result.data["attributePlans"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_owned_field_is_refused(authenticated_context: HttpContext):
    """A lens is a selection over a dataset, nothing else: it owns no array to sample.

    Refused rather than guessed -- resolving through to the dataset's store would silently
    ignore the crop the lens exists to state.
    """
    mask = await _mask(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])

    def lens_field_edge() -> None:
        lens_system = models.CoordinateSystem.objects.create(name="crop", organization=authenticated_context.request.organization)
        models.Lens.objects.create(dataset=mask, coordinate_system=lens_system, slices=[])
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.FIELD.value,
            input=mask_system,
            output=models.CoordinateSystem.objects.get(pk=table["coordinateSystem"]["id"]),
            field=lens_system,
            input_axes=["y", "x"],
            output_axes=["i"],
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(lens_field_edge)()

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert result.errors, "a lens-owned field must be refused, not resolved through"
    assert "lens" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_cannot_be_a_field(authenticated_context: HttpContext):
    """The geometry/record-land boundary, refused where the edge is written.

    "Nucleus 42 is in track 17" reads like a map and is not one a FIELD can carry: nothing
    you could stand in holds it, so there is nothing to dereference -- you need a *row*
    first. That relation is `Column.references`, and RFC-7's "References, not joins"
    argues why. This asserts the refusal that section exists to justify, which
    `docs/attribute-plans-api.md` publishes as a contract.

    This is the boundary widening the FIELD guard to mesh collections must not blur: a mesh
    keys a table because standing in its space yields an id, and a table still cannot,
    because its rows are already record-land.

    Note it fires at *write* time -- `createTransformation` used to accept the edge and only
    fail the day someone probed for plans.
    """
    nuclei = await _table(authenticated_context, "nuclei", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "track_id", "dtype": "BIGINT", "role": "TRACK_ID"}])
    tracks = await _table(authenticated_context, "tracks", [{"name": "track", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "duration", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])

    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": nuclei["coordinateSystem"]["id"],
                "output": tracks["coordinateSystem"]["id"],
                "transform": {
                    "kind": "FIELD",
                    "field": nuclei["coordinateSystem"]["id"],
                    "inputAxes": ["i"],
                    "outputAxes": ["track"],
                },
            }
        },
    )
    assert result.errors, "a map out of a table is not a FIELD edge"
    message = str(result.errors[0])
    assert "dereferences nothing" in message
    assert "Column.references" in message, "the error should name the mechanism that does work"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_array_without_a_store_is_refused(authenticated_context: HttpContext):
    """A plan names the array a worker samples; an array with no store cannot be named."""
    mask = await _mask(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]], with_store=False)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    await _field_edge(authenticated_context, mask_system, table, ["i"])

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert result.errors, "an unsampleable array must be refused"
    assert "no zarr store" in str(result.errors[0])


# --- discovery through the fact graph: probe the image, answer from the mask -----------


async def _derive(
    ctx: HttpContext,
    derived: models.ArrayDataset,
    source_system: models.CoordinateSystem,
    *,
    kind: str = "IDENTITY",
    input_axes: list[str] | None = None,
    output_axes: list[str] | None = None,
    value_relation: str | None = "CATEGORIZED",
) -> models.Transformation:
    """The derivation edge a segmentation writes: derived intrinsic -> source space.

    Exactly what `_write_derivation_edges` calls, without the lens + upload ceremony.
    """

    def build() -> models.Transformation:
        return graph_logic.write_relation_edge(
            name=f"{derived.name} <- {source_system.name}",
            input_system=derived.intrinsic_coordinate_system,
            output_system=source_system,
            kind=kind,
            input_axes=input_axes,
            output_axes=output_axes,
            value_relation=value_relation,
            ctx=seed._creation(ctx),
        )

    return await sync_to_async(build)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_probing_the_source_image_finds_the_derived_masks_plans(authenticated_context: HttpContext):
    """The scene-hover case: probe the image layer's system, answer from the derived mask.

    The image is (t,c,y,x); the mask is (t,y,x), derived BY_DIMENSION on the axes it kept
    (a segmentation is silent about `c`). The plan comes back with the derivation edge as
    a one-step `path`, inverted -- the edge is stored mask->image, the probe walks it
    backwards -- and `passthrough` is computed off the MASK's axes: `t` alone, never a
    bogus `c` borrowed from where the caller happens to stand. ABLATION: compute
    passthrough off the probed system and this test's last assert fails first.
    """
    image = await seed.create_array_dataset(
        authenticated_context,
        "timelapse",
        axes=[seed.axis("t", enums.AxisType.TIME), seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        shapes=[[10, 3, 64, 64]],
    )
    mask = await _mask(authenticated_context, "instance map")
    derivation = await _derive(authenticated_context, mask, await sync_to_async(lambda: image.intrinsic_coordinate_system)(), kind="BY_DIMENSION", input_axes=["t", "y", "x"], output_axes=["t", "y", "x"])

    table = await _table(
        authenticated_context,
        "nuclei morphology",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    await _field_edge(authenticated_context, mask_system, table, ["i"])

    image_system = await sync_to_async(lambda: image.intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(image_system.pk)})
    assert not result.errors, result.errors
    plans = result.data["attributePlans"]
    assert len(plans) == 1, "the mask's plan is found through the derivation edge"

    plan = plans[0]
    assert plan["path"] == [{"transformation": {"id": str(derivation.pk), "version": 1}, "inverted": True}], "the edge is stored mask->image; the probe walks it backwards"
    assert plan["sample"]["system"]["id"] == str(mask_system.pk)
    assert plan["sample"]["consumes"] == ["y", "x"]
    assert plan["sample"]["passthrough"] == ["t"], "passthrough is the MASK's unconsumed axes: the image's `c` does not exist there"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_probing_the_mask_directly_returns_an_empty_path(authenticated_context: HttpContext):
    """A locally-rooted plan carries no steps: you are already standing where it samples."""
    image = await seed.create_array_dataset(authenticated_context, "source", axes=seed.YX_AXES, shapes=[[64, 64]])
    mask = await _mask(authenticated_context, "labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    await _derive(authenticated_context, mask, await sync_to_async(lambda: image.intrinsic_coordinate_system)())
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    await _field_edge(authenticated_context, mask_system, table, ["i"])

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(mask_system.pk)})
    assert not result.errors, result.errors
    assert [plan["path"] for plan in result.data["attributePlans"]] == [[]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sibling_masks_correspond_through_their_parent(authenticated_context: HttpContext):
    """Probing mask A finds mask B's table: the point in A does correspond to a point in B.

    The question is "what corresponds to this point", not "what belongs to this dataset" --
    and the guardrails (no registrations, no UNMAPPABLE, no rank-changing inverses) bound
    the answer to grids that honestly correspond. Local plans sort first, so a client that
    wants only its own reads a stable prefix (or filters `path.length == 0`).
    """
    image = await seed.create_array_dataset(authenticated_context, "source", axes=seed.YX_AXES, shapes=[[64, 64]])
    image_system = await sync_to_async(lambda: image.intrinsic_coordinate_system)()

    plans_by_mask: dict[str, models.Transformation] = {}
    for name, produced in (("nuclei", "i"), ("cytoplasm", "label_id")):
        mask = await _mask(authenticated_context, f"{name} labels", axes=seed.YX_AXES, shapes=[[64, 64]])
        plans_by_mask[name] = await _derive(authenticated_context, mask, image_system)
        table = await _table(authenticated_context, f"{name} table", [{"name": produced, "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
        await _field_edge(authenticated_context, await sync_to_async(lambda m=mask: m.intrinsic_coordinate_system)(), table, [produced])

    nuclei_system = await sync_to_async(lambda: models.ArrayDataset.objects.get(name="nuclei labels").intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(nuclei_system.pk)})
    assert not result.errors, result.errors
    plans = result.data["attributePlans"]
    assert [plan["hops"][0]["table"]["name"] for plan in plans] == ["nuclei table", "cytoplasm table"], "own plan first, then by distance"
    assert plans[0]["path"] == []
    assert [(step["transformation"]["id"], step["inverted"]) for step in plans[1]["path"]] == [
        (str(plans_by_mask["nuclei"].pk), False),
        (str(plans_by_mask["cytoplasm"].pk), True),
    ], "down A's derivation forward, up B's backward"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_changing_derivation_is_not_walked_backwards(authenticated_context: HttpContext):
    """A (y,x) mask embedded into a (z,y,x) image has no inverse to offer the probe.

    `is_reverse_traversable` refuses the unequal-rank hop, so the mask is honestly
    unreachable from the image -- better absent than a plan whose path cannot be composed.
    """
    image = await seed.create_array_dataset(authenticated_context, "volume", axes=[seed.axis("z", enums.AxisType.SPACE), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)], shapes=[[8, 64, 64]])
    mask = await _mask(authenticated_context, "slice labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    await _field_edge(authenticated_context, mask_system, table, ["i"])

    def embed() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.AFFINE.value,
            input=mask_system,
            output=image.intrinsic_coordinate_system,
            params={"affine": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]},
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(embed)()

    image_system = await sync_to_async(lambda: image.intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(image_system.pk)})
    assert not result.errors, result.errors
    assert result.data["attributePlans"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_derivation_is_not_walked(authenticated_context: HttpContext):
    """UNMAPPABLE declares no point corresponds -- discovery must not compose across it."""
    image = await seed.create_array_dataset(authenticated_context, "source", axes=seed.YX_AXES, shapes=[[64, 64]])
    mask = await _mask(authenticated_context, "labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    await _derive(authenticated_context, mask, await sync_to_async(lambda: image.intrinsic_coordinate_system)(), kind="UNMAPPABLE", value_relation=None)
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    await _field_edge(authenticated_context, await sync_to_async(lambda: mask.intrinsic_coordinate_system)(), table, ["i"])

    image_system = await sync_to_async(lambda: image.intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(image_system.pk)})
    assert not result.errors, result.errors
    assert result.data["attributePlans"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sliced_lens_hop_appears_in_the_path(authenticated_context: HttpContext):
    """A mask derived from a crop is two backward steps from the image: lens edge, then derivation."""
    image = await seed.create_array_dataset(authenticated_context, "source", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, image, slices=[{"axis": "y", "start": 8, "stop": 40}])
    lens_system = await sync_to_async(lambda: lens.coordinate_system)()
    mask = await _mask(authenticated_context, "crop labels", axes=seed.YX_AXES, shapes=[[32, 64]])
    await _derive(authenticated_context, mask, lens_system)
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    await _field_edge(authenticated_context, await sync_to_async(lambda: mask.intrinsic_coordinate_system)(), table, ["i"])

    image_system = await sync_to_async(lambda: image.intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(image_system.pk)})
    assert not result.errors, result.errors
    plans = result.data["attributePlans"]
    assert len(plans) == 1
    assert [step["inverted"] for step in plans[0]["path"]] == [True, True], "image <- lens crop <- mask, both edges walked backwards"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_registrations_never_extend_discovery(authenticated_context: HttpContext):
    """Two datasets sharing a scene's world do not correspond by that fact alone.

    A registration is a claim a scene composes; this query has no scene, so the walk never
    even stands on the SHARED system. ABLATION: drop the SHARED-side exclusions in
    `fact_paths` (or `fact_edges`' registration filter) and the foreign mask's plan
    appears here.
    """
    scene = await seed.create_scene(authenticated_context)
    plain = await seed.create_array_dataset(authenticated_context, "plain", axes=seed.YX_AXES, shapes=[[64, 64]])
    mask = await _mask(authenticated_context, "labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    await _field_edge(authenticated_context, await sync_to_async(lambda: mask.intrinsic_coordinate_system)(), table, ["i"])
    await seed.register_into_scene(authenticated_context, scene, plain)
    await seed.register_into_scene(authenticated_context, scene, mask)

    plain_system = await sync_to_async(lambda: plain.intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(plain_system.pk)})
    assert not result.errors, result.errors
    assert result.data["attributePlans"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_edge_is_payload_never_connectivity(authenticated_context: HttpContext):
    """A warp-field FIELD edge onto another grid must not carry discovery into that grid."""
    atlas = await seed.create_array_dataset(authenticated_context, "atlas", axes=seed.YX_AXES, shapes=[[64, 64]])
    mask = await _mask(authenticated_context, "atlas labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    await _derive(authenticated_context, mask, await sync_to_async(lambda: atlas.intrinsic_coordinate_system)())
    table = await _table(authenticated_context, "objects", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    await _field_edge(authenticated_context, await sync_to_async(lambda: mask.intrinsic_coordinate_system)(), table, ["i"])

    probe = await seed.create_array_dataset(authenticated_context, "moving image", axes=seed.YX_AXES, shapes=[[64, 64]])
    probe_system = await sync_to_async(lambda: probe.intrinsic_coordinate_system)()

    def warp() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.FIELD.value,
            input=probe_system,
            output=atlas.intrinsic_coordinate_system,
            field=probe_system,
            input_axes=["y", "x"],
            output_axes=["y", "x"],
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(warp)()

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(probe_system.pk)})
    assert not result.errors, result.errors
    assert result.data["attributePlans"] == [], "the warp FIELD is neither a plan (pixel-grid target) nor a road to the atlas' plans"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_max_depth_limits_discovery(authenticated_context: HttpContext):
    """`maxDepth` bounds the walk exactly like coordinateGraph's: siblings are two hops away."""
    image = await seed.create_array_dataset(authenticated_context, "source", axes=seed.YX_AXES, shapes=[[64, 64]])
    image_system = await sync_to_async(lambda: image.intrinsic_coordinate_system)()
    for name, produced in (("nuclei", "i"), ("cytoplasm", "label_id")):
        mask = await _mask(authenticated_context, f"{name} labels", axes=seed.YX_AXES, shapes=[[64, 64]])
        await _derive(authenticated_context, mask, image_system)
        table = await _table(authenticated_context, f"{name} table", [{"name": produced, "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
        await _field_edge(authenticated_context, await sync_to_async(lambda m=mask: m.intrinsic_coordinate_system)(), table, [produced])

    nuclei_system = await sync_to_async(lambda: models.ArrayDataset.objects.get(name="nuclei labels").intrinsic_coordinate_system)()
    capped = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": str(nuclei_system.pk), "maxDepth": 1})
    assert not capped.errors, capped.errors
    assert [plan["hops"][0]["table"]["name"] for plan in capped.data["attributePlans"]] == ["nuclei table"], "depth 1 reaches the image, not the sibling behind it"


# --- discovery from a container that is not an ArrayDataset -------------------------------


async def _mesh_collection(ctx: HttpContext, source_system: models.CoordinateSystem, *, axes: list[dict], transform: dict | None) -> str:
    """A mesh collection in its own space, derived from `source_system`.

    `transform=None` leaves `derivedFrom` bare, which is UNMAPPABLE -- the default the
    negative test below exists to pin.
    """

    store = await seed.create_fabriks_store(ctx)
    entry: dict = {"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(source_system.pk)}
    if transform is not None:
        entry["transform"] = transform

    result = await schema.execute(
        "mutation Create($input: CreateMeshCollectionInput!) { createMeshCollection(input: $input) { coordinateSystem { id } } }",
        context_value=ctx,
        variable_values={"input": {"version": "v1", "store": str(store.pk), "axes": axes, "derivedFrom": [entry]}},
    )
    assert not result.errors, result.errors
    return result.data["createMeshCollection"]["coordinateSystem"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_probing_a_mesh_collections_system_finds_the_source_masks_plans(authenticated_context: HttpContext):
    """A mesh collection asks "what do we know about this segment?" the same way a pixel does.

    The collection owns its space, so the plan lives one hop away on the mask it was
    extracted from -- and that hop is walked FORWARD, because a derivation edge is stored
    child -> source. Nothing about rank or invertibility is consulted on a forward hop, so
    this holds even where the reverse direction would be refused.

    This is what lets the mesh wire format keep per-object attributes OUT of its parquet
    and defer them to the table the FIELD edge names.

    *This* collection keys nothing itself -- a collection that does gets a `MeshSample` plan
    of its own, rooted where you probe (see `tests/test_keyed_by.py`). The two routes
    coexist; what is pinned here is the derived one, which is the only way to reach a table
    that hangs off the mask rather than off the meshes.
    """
    mask = await _mask(authenticated_context, "instance map")
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    table = await _table(
        authenticated_context,
        "nuclei morphology",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    await _field_edge(authenticated_context, mask_system, table, ["i"])

    axes = [{"name": "t", "type": "TIME"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]
    mesh_system = await _mesh_collection(authenticated_context, mask_system, axes=axes, transform={"kind": "IDENTITY"})

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": mesh_system})
    assert not result.errors, result.errors
    plans = result.data["attributePlans"]
    assert len(plans) == 1, "the mask's plan is reachable from the collection's own space"

    plan = plans[0]
    assert [step["inverted"] for step in plan["path"]] == [False], "the edge is stored collection->mask, so the probe walks it forwards"
    assert plan["sample"]["system"]["id"] == str(mask_system.pk), "these meshes key nothing themselves; the mask is what a worker samples"
    assert plan["sample"]["__typename"] == "ArraySample", "the plan is rooted on the mask, not on the collection"
    assert plan["hops"][0]["table"]["name"] == "nuclei morphology"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_collection_with_an_unmappable_derivation_reaches_no_plans(authenticated_context: HttpContext):
    """`derivedFrom` with no `transform` is UNMAPPABLE, and UNMAPPABLE is never walked.

    The whole of the condition on the paragraph above: a writer that names its source and
    states nothing about how the two spaces relate gets lineage and no attributes. Stated
    as a test because it is the silent half -- the query succeeds and simply answers
    nothing.
    """
    mask = await _mask(authenticated_context, "instance map")
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    table = await _table(
        authenticated_context,
        "objects",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    await _field_edge(authenticated_context, mask_system, table, ["i"])

    axes = [{"name": "t", "type": "TIME"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]
    mesh_system = await _mesh_collection(authenticated_context, mask_system, axes=axes, transform=None)

    result = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": mesh_system})
    assert not result.errors, result.errors
    assert result.data["attributePlans"] == []


# --- Column.references: the record-land sibling of the FIELD edge ----------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_references_the_table_its_values_identify(authenticated_context: HttpContext):
    """`instance_id` on the objects table is a declared foreign key into tracks.

    The FK states only *which table*: which column carries the target's row identity is
    already declared there (its single INDEX coordinate column), and restating it here
    would be a second copy of one fact, free to disagree.
    """
    tracks = await _table(
        authenticated_context,
        "tracks",
        [
            {"name": "instance_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "duration", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    objects = await _table(
        authenticated_context,
        "tracked objects",
        [
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "instance_id", "dtype": "BIGINT", "role": "TRACK_ID", "references": tracks["id"]},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )

    column = next(col for col in objects["columns"] if col["name"] == "instance_id")
    assert column["references"] == {"id": tracks["id"], "name": "tracks"}

    referencing = await sync_to_async(lambda: [col.name for col in models.TableDataset.objects.get(pk=tracks["id"]).referenced_by.all()])()
    assert referencing == ["instance_id"], "the reverse relation answers 'who keys into me'"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_measured_coordinate_column_cannot_reference(authenticated_context: HttpContext):
    """A SPACE or TIME coordinate's values are positions, so they cannot also be row ids.

    Narrowed deliberately, and the narrowing is the whole content of the rule. A position in
    nanometres and a row id are different things, so a column claiming to be both is two maps
    at once and which one a reader follows is convention again -- that is refused here.

    An **INDEX** axis is not that case: its values are already ids ("an enumeration with no
    metric"), so naming the table it enumerates is what the enumeration is *of*, not a second
    map. That is what makes a product space expressible, and it is exercised by
    `tests/test_keyed_by.py::test_a_referenced_index_axis_is_identified_and_the_field_need_not_produce_it`.
    """
    tracks = await _table(authenticated_context, "tracks", [{"name": "instance_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "duration", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    store = await _parquet(authenticated_context, "coord-ref", [("x", "DOUBLE")])

    result = await schema.execute(
        CREATE_TABLE,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "bad",
                "data": str(store.pk),
                "columns": [{"name": "x", "dtype": "DOUBLE", "axisType": "SPACE", "unit": "micrometer", "identifiedBy": [{"kind": "TABLE", "table": tracks["id"]}]}],
            }
        },
    )
    assert result.errors, "a measured COORDINATE column with a reference must be refused"
    message = str(result.errors[0])
    assert "positions rather than ids" in message
    assert "INDEX" in message, "name the axis type that may say what it enumerates"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_composite_keyed_table_cannot_be_referenced(authenticated_context: HttpContext):
    """A single value cannot identify a row of a (t, i)-keyed table: refuse, don't guess."""
    timelapse = await _table(
        authenticated_context,
        "per-frame objects",
        [
            {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    store = await _parquet(authenticated_context, "composite-ref", [("object_ref", "BIGINT")])

    result = await schema.execute(
        CREATE_TABLE,
        context_value=authenticated_context,
        variable_values={"input": {"name": "bad", "data": str(store.pk), "columns": [{"name": "object_ref", "dtype": "BIGINT", "role": "ID", "identifiedBy": [{"kind": "TABLE", "table": timelapse["id"]}]}]}},
    )
    assert result.errors, "a composite-keyed target must be refused"
    assert "exactly one INDEX axis" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_degenerate_table_cannot_be_referenced(authenticated_context: HttpContext):
    """The synthetic `object` axis enumerates rows with no backing column to look a value up in."""
    degenerate = await _table(authenticated_context, "measurements", [{"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    store = await _parquet(authenticated_context, "degenerate-ref", [("row_ref", "BIGINT")])

    result = await schema.execute(
        CREATE_TABLE,
        context_value=authenticated_context,
        variable_values={"input": {"name": "bad", "data": str(store.pk), "columns": [{"name": "row_ref", "dtype": "BIGINT", "role": "ID", "identifiedBy": [{"kind": "TABLE", "table": degenerate["id"]}]}]}},
    )
    assert result.errors, "a synthetic row enumeration must be refused as a reference target"
    assert "synthetic row enumeration" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_referenced_table_cannot_be_deleted(authenticated_context: HttpContext):
    """PROTECT, for the same reason a warp field is PROTECTed: deleting tracks out from
    under a column keying it would orphan the meaning of every value in that column."""
    tracks = await _table(authenticated_context, "tracks", [{"name": "instance_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "duration", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    objects = await _table(
        authenticated_context,
        "tracked objects",
        [
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "instance_id", "dtype": "BIGINT", "role": "TRACK_ID", "references": tracks["id"]},
        ],
    )

    def delete_target() -> None:
        models.TableDataset.objects.get(pk=tracks["id"]).delete()

    with pytest.raises(ProtectedError):
        await sync_to_async(delete_target)()

    def delete_in_order() -> None:
        models.TableDataset.objects.get(pk=objects["id"]).delete()
        models.TableDataset.objects.get(pk=tracks["id"]).delete()

    await sync_to_async(delete_in_order)()


# --- hops: the chain a plan carries past its landing ------------------------------------

HOPS = """
query Plans($system: ID!, $maxJoinDepth: Int) {
  attributePlans(system: $system, maxJoinDepth: $maxJoinDepth) {
    hops {
      index parent cardinality
      via { column { name } axis }
      table { name }
      joinPath { table { name } column { name } }
      lookup { keyColumns { axis column { name } } attributes { name } }
    }
  }
}
"""


async def _tracked_stack(ctx: HttpContext) -> models.CoordinateSystem:
    """mask -> objects(i) whose `instance_id` references tracks(instance_id) whose `lineage_id` references lineages."""
    lineages = await _table(ctx, "lineages", [{"name": "lineage_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "founder", "dtype": "VARCHAR", "role": "LABEL"}])
    tracks = await _table(
        ctx,
        "tracks",
        [
            {"name": "instance_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "lineage_id", "dtype": "BIGINT", "role": "ID", "references": lineages["id"]},
            {"name": "duration", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    objects = await _table(
        ctx,
        "tracked objects",
        [
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "instance_id", "dtype": "BIGINT", "role": "TRACK_ID", "references": tracks["id"]},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    # A (y, x) mask: the objects table is keyed by `i` alone, so nothing passes through.
    mask = await _mask(ctx, axes=[seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)], shapes=[[64, 64]])
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    await _field_edge(ctx, mask_system, objects, ["i"])
    return mask_system


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_reference_is_one_hop_away_in_the_plan(authenticated_context: HttpContext):
    """`instance_id` references tracks, so the plan carries the hop -- with the key column the client used to guess."""
    system = await _tracked_stack(authenticated_context)

    result = await schema.execute(HOPS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
    assert not result.errors, result.errors
    (plan,) = result.data["attributePlans"]
    landing, hop = plan["hops"]

    assert landing["index"] == 0 and landing["parent"] is None and landing["via"] is None and landing["joinPath"] == []
    assert hop["index"] == 1 and hop["parent"] == 0
    assert hop["cardinality"] == "ONE", "one row's column binds one value"
    assert hop["via"] == {"column": {"name": "instance_id"}, "axis": None}
    assert hop["table"]["name"] == "tracks"
    assert hop["lookup"]["keyColumns"] == [{"axis": "instance_id", "column": {"name": "instance_id"}}], "held under the parent's column name, bound to the target's INDEX column"
    assert [column["name"] for column in hop["lookup"]["attributes"]] == ["lineage_id", "duration"]
    assert hop["joinPath"] == [{"table": {"name": "tracked objects"}, "column": {"name": "instance_id"}}], "exactly what a picker entry stores as `joinPath`"
    assert plan_sql.build_lookup_sql(hop["lookup"]) == 'SELECT "lineage_id", "duration" FROM read_parquet(?) WHERE "instance_id" = ?'


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_hops_are_bounded_and_default_to_one(authenticated_context: HttpContext):
    """`maxJoinDepth` defaults to one hop, zero is the landing alone, and past the cap is the cap."""
    system = await _tracked_stack(authenticated_context)

    async def lengths(**variables: object) -> list[str]:
        result = await schema.execute(HOPS, context_value=authenticated_context, variable_values={"system": str(system.pk), **variables})
        assert not result.errors, result.errors
        return [hop["table"]["name"] for hop in result.data["attributePlans"][0]["hops"]]

    assert await lengths() == ["tracked objects", "tracks"]
    assert await lengths(maxJoinDepth=0) == ["tracked objects"]
    assert await lengths(maxJoinDepth=2) == ["tracked objects", "tracks", "lineages"]
    assert await lengths(maxJoinDepth=99) == ["tracked objects", "tracks", "lineages"], "clamped, not refused"

    deeper = await schema.execute(HOPS, context_value=authenticated_context, variable_values={"system": str(system.pk), "maxJoinDepth": 2})
    lineage_hop = deeper.data["attributePlans"][0]["hops"][2]
    assert lineage_hop["parent"] == 1
    assert lineage_hop["joinPath"] == [{"table": {"name": "tracked objects"}, "column": {"name": "instance_id"}}, {"table": {"name": "tracks"}, "column": {"name": "lineage_id"}}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_hop_cycle_is_cut_per_branch(authenticated_context: HttpContext):
    """A reference back to the landing table is not a hop: the worker already stands there.

    The API cannot author a cycle (a target exists before the table referencing it), so this
    one is written at the model level -- the walk's guard is defence in depth, and this is what
    it defends against.
    """
    system = await _tracked_stack(authenticated_context)

    def close_the_loop() -> None:
        objects = models.TableDataset.objects.get(name="tracked objects")
        models.Column.objects.filter(table__name="tracks", name="lineage_id").update(references=objects)

    await sync_to_async(close_the_loop)()

    result = await schema.execute(HOPS, context_value=authenticated_context, variable_values={"system": str(system.pk), "maxJoinDepth": 4})
    assert not result.errors, result.errors
    assert [hop["table"]["name"] for hop in result.data["attributePlans"][0]["hops"]] == ["tracked objects", "tracks"], "tracks -> tracked objects revisits the landing and is cut"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_hop_walk_costs_one_read_per_level(authenticated_context: HttpContext):
    """Batched per level: a second reference out of the same table adds no query."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    system = await _tracked_stack(authenticated_context)
    organization = authenticated_context.request.organization

    def count() -> int:
        with CaptureQueriesContext(connection) as captured:
            attribute_plans_logic.build_attribute_plans(system, organization=organization, max_join_depth=2)
        return len(captured.captured_queries)

    before = await sync_to_async(count)()

    # A second table keyed by the objects, and a second reference out of it: same levels, more nodes.
    other = await _table(authenticated_context, "annotations", [{"name": "annotation_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "note", "dtype": "VARCHAR", "role": "LABEL"}])

    def widen() -> None:
        objects = models.TableDataset.objects.get(name="tracked objects")
        column = objects.columns.get(name="area")
        column.references_id = int(other["id"])
        column.role = enums.ColumnRoleChoices.ID.value
        column.save()

    await sync_to_async(widen)()

    after = await sync_to_async(count)()
    assert after == before, f"{before} queries before widening, {after} after -- the walk must batch per level, not per hop"
