"""A derivation runs between *containers*, whichever kind they are.

"This data was computed from that data" is one edge, and it was only ever expressible
between two array datasets: `createArrayDataset(derivedFrom:)` named a `Lens` and nothing else,
while the three collections named a bare `coordinateSystem` -- so a table could not say
which image its rows were segmented out of without the caller looking that image's *system*
id up by hand, and an image reconstructed from a table could not say so at all.

Two directions matter here, and they are not symmetric:

- **image -> table.** An instance mask's per-object measurements. The table's rows enumerate
  objects rather than sitting anywhere, so its space is an INDEX axis and the only honest
  edge is UNMAPPABLE -- which is exactly what an omitted `transform` now means.
- **table -> image.** An SMLM localization table's coordinate columns declare a *metric*
  space in nanometres, so a reconstruction rendered from it relates to it by a real SCALE,
  and inherits the table's placement the way a derived image inherits its parent's.

The second is what forced the placement machinery to be keyed by container rather than by
dataset: keyed by dataset, a table had no key to be a parent under, so the edge was not
refused -- it was silently dropped from `derivedFrom` and never walked.
"""

import re

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.inputs.coords import derived_from_union_types
from mikro_server.schema import schema
from tests import seed

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) {
    id
    coordinateSystem { id axes { name type unit } }
    derivedFrom { id kind valueRelation output { id residents { __typename } } }
  }
}
"""

DATASET_LINEAGE = """
query Lineage($id: ID!) {
  arrayDataset(id: $id) {
    id
    derivedFrom { id kind output { id residents { __typename ... on TableDataset { name } } } }
  }
}
"""

PLACEMENT = """
query Placement($id: ID!) {
  scene(id: $id) {
    layers { id placement pathToWorld { inverted transformation { id kind } } }
  }
}
"""


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


async def _table(ctx: HttpContext, name: str, *, columns: list, derived_from: list | None = None) -> dict:
    """A table dataset through the real mutation."""
    store = await _parquet(ctx, f"table-{name}", seed.split_declaration(columns)[0])
    result = await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, columns, derivedFrom=derived_from or [])},
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]


_MEASUREMENT_COLUMNS = [
    {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "area", "dtype": "DOUBLE"},
]

_LOCALIZATION_COLUMNS = [
    {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
    {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_measurement_table_says_which_image_it_was_measured_from(authenticated_context: HttpContext) -> None:
    """image -> table, naming the *container* rather than hunting for its system id.

    The transform is omitted, so the edge is UNMAPPABLE: the rows are per-object
    measurements and are not anywhere. That is the lineage recorded and no geometry
    claimed, which is the whole "naming a source is not the same as claiming a map" rule.
    """
    mask = await seed.create_array_dataset(authenticated_context, "InstanceMask", axes=seed.YX_AXES, shapes=[[64, 64]])

    table = await _table(
        authenticated_context,
        "Objects",
        columns=_MEASUREMENT_COLUMNS,
        derived_from=[{"kind": "DATASET", "dataset": str(mask.pk), "valueRelation": "TRANSFORMED"}],
    )

    assert [axis["name"] for axis in table["coordinateSystem"]["axes"]] == ["object"], "rows enumerate objects; they are not placed"
    assert len(table["derivedFrom"]) == 1
    edge = table["derivedFrom"][0]
    assert edge["kind"] == "UNMAPPABLE", "an omitted transform records the lineage and claims no geometry"
    assert edge["valueRelation"] == "TRANSFORMED"
    assert [r["__typename"] for r in edge["output"]["residents"]] == ["ArrayDataset", "DataArray"], "the far end is the image the rows were measured from"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_image_says_which_table_it_was_reconstructed_from(authenticated_context: HttpContext) -> None:
    """table -> image: the direction that was previously unexpressible in either sense.

    Not merely unsupported -- **silently dropped**. `derivation_edges` resolved an edge's
    output to an `ArrayDataset` and discarded the edge when it could not, so this assertion
    fails on the old code by coming back with an empty list rather than an error.
    """
    table = await _table(authenticated_context, "Localizations", columns=_LOCALIZATION_COLUMNS)
    render = await _reconstruction(authenticated_context, table, transform={"kind": "SCALE", "scale": [10.0, 10.0]})

    result = await schema.execute(DATASET_LINEAGE, context_value=authenticated_context, variable_values={"id": str(render)})
    assert not result.errors, result.errors

    parents = result.data["arrayDataset"]["derivedFrom"]
    assert len(parents) == 1, "the table parent must not be dropped"
    assert parents[0]["kind"] == "SCALE"
    residents = parents[0]["output"]["residents"]
    assert [r["__typename"] for r in residents] == ["TableDataset"] and residents[0]["name"] == "Localizations"


async def _reconstruction(ctx: HttpContext, table: dict, *, transform: dict) -> str:
    """An array dataset derived from a table, through the real ingest mutation."""
    from unittest.mock import patch

    store = await sync_to_async(models.ZarrStore.objects.create)(
        path="s3://zarr/render",
        bucket="zarr",
        key="render",
        shape=[64, 64],
        chunks=[64, 64],
        version="3",
        dtype="uint8",
        populated=True,
        organization=ctx.request.organization,
    )
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            "mutation D($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }",
            context_value=ctx,
            variable_values={
                "input": {
                    "name": "Reconstruction",
                    "data": str(store.id),
                    "scales": [],
                    "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                    "derivedFrom": [{"kind": "TABLE_DATASET", "tableDataset": table["id"], "transform": transform, "valueRelation": "TRANSFORMED"}],
                }
            },
        )
    assert not result.errors, result.errors
    return result.data["createArrayDataset"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_reconstruction_is_placed_through_its_localization_table(authenticated_context: HttpContext) -> None:
    """The payoff: register the table, and the image built from it comes along.

    This is what container-keying the fact tree bought. Keyed by dataset id, the walks had
    no key for a table -- `_derivation_descendants` never discovered the reconstruction as
    a descendant, and `container_buckets` closed over nothing -- so the image was
    UNREGISTERED however real the SCALE edge was.

    The transform is stated explicitly, and has to be: an omitted one is UNMAPPABLE, and
    `is_traversable` refuses that edge, so nothing would be inherited through it.
    """
    table = await _table(authenticated_context, "Localizations", columns=_LOCALIZATION_COLUMNS)
    render = await _reconstruction(authenticated_context, table, transform={"kind": "SCALE", "scale": [10.0, 10.0]})
    scene = await seed.create_scene(authenticated_context, "Composition")

    def register_and_layer() -> None:
        # The table is registered into the world; the reconstruction is not.
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value,
            input=models.TableDataset.objects.get(pk=table["id"]).coordinate_system,
            output=scene.world,
            params={"scale": [0.001, 0.001]},
            organization=authenticated_context.request.organization,
        )
        dataset = models.ArrayDataset.objects.get(pk=render)
        # An unsliced lens: its space *is* the dataset's intrinsic grid, so the layer sits
        # exactly where the reconstruction does.
        lens = models.Lens.objects.create(dataset=dataset, slices=[])
        models.Layer.objects.create(kind=enums.LayerKindChoices.IMAGE.value, scene=scene, lens=lens)

    await sync_to_async(register_and_layer)()

    result = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors

    layer = result.data["scene"]["layers"][0]
    assert layer["placement"] == "PLACED", "the reconstruction inherits the table's registration"
    kinds = [step["transformation"]["kind"] for step in layer["pathToWorld"]]
    assert "SCALE" in kinds and len(kinds) >= 2, f"the path runs through the table: {kinds}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_measurement_tables_index_space_refuses_a_metric_edge(authenticated_context: HttpContext) -> None:
    """RFC-7's "tables are leaves", enforced by the axis types rather than by a container check.

    The distinction that makes the SMLM case work is not "table" versus "image" -- it is
    whether the table declared coordinate columns. One with none gets a single INDEX axis,
    and `assert_edge_rank` refuses arithmetic on it: the distance between object 3 and
    object 4 means nothing.
    """
    mask = await seed.create_array_dataset(authenticated_context, "InstanceMask", axes=seed.YX_AXES, shapes=[[64, 64]])
    store = await _parquet(authenticated_context, "table-refused", seed.split_declaration(_MEASUREMENT_COLUMNS)[0])

    result = await schema.execute(
        CREATE_TABLE,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "Objects",
                "data": str(store.pk),
                **seed.split_payload(_MEASUREMENT_COLUMNS),
                "derivedFrom": [{"kind": "DATASET", "dataset": str(mask.pk), "transform": {"kind": "SCALE", "scale": [1.0]}}],
            }
        },
    )
    assert result.errors and "INDEX axis" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert not await sync_to_async(models.TableDataset.objects.filter(name="Objects").exists)(), "a refused edge rolls the table back with it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_source_reports_its_non_dataset_children_under_its_own_name(authenticated_context: HttpContext) -> None:
    """`derivedDatasets` stays about datasets; `derivedResidents` is the wider question.

    The narrow walk requires the edge's input to *be* an intrinsic system, which is exactly
    what confines it to array datasets -- so a measurement table computed from this image
    is missing from it. That was widened by adding a second field rather than by changing
    what the first one returns: `derivedDatasets` returning a table would be a field whose
    name lies.
    """
    mask = await seed.create_array_dataset(authenticated_context, "InstanceMask", axes=seed.YX_AXES, shapes=[[64, 64]])
    await _table(authenticated_context, "Objects", columns=_MEASUREMENT_COLUMNS, derived_from=[{"kind": "DATASET", "dataset": str(mask.pk)}])

    result = await schema.execute(
        """
        query Children($id: ID!) {
          arrayDataset(id: $id) {
            derivedDatasets { id name }
            derivedResidents { __typename ... on TableDataset { name } }
          }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": str(mask.pk)},
    )
    assert not result.errors, result.errors

    dataset = result.data["arrayDataset"]
    assert dataset["derivedDatasets"] == [], "no *dataset* was derived from the mask"
    assert [(r["__typename"], r.get("name")) for r in dataset["derivedResidents"]] == [("TableDataset", "Objects")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_wider_field_reports_dataset_children_too(authenticated_context: HttpContext) -> None:
    """The obvious half, which is exactly the half a key mismatch silently drops.

    A container key's first half is written on `CONTAINERS` rather than read off the model
    name, and this is why: a dataset keys as `("dataset", pk)`, so a lookup that reverses it
    through `ArrayDataset.__name__.lower()` asks for `"arrayDataset"`, finds nothing, and returns an
    answer that is short by every dataset in it -- with no error anywhere. A test with only
    a table child passes throughout.
    """
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, acquired)
    await _derived(authenticated_context, "Mask", lens=lens)
    await _table(authenticated_context, "Objects", columns=_MEASUREMENT_COLUMNS, derived_from=[{"kind": "DATASET", "dataset": str(acquired.pk)}])

    result = await schema.execute(
        """
        query Children($id: ID!) {
          arrayDataset(id: $id) {
            derivedResidents { __typename ... on ArrayDataset { name } ... on TableDataset { name } }
          }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": str(acquired.pk)},
    )
    assert not result.errors, result.errors
    assert {(r["__typename"], r["name"]) for r in result.data["arrayDataset"]["derivedResidents"]} == {
        ("ArrayDataset", "Mask"),
        ("TableDataset", "Objects"),
    }, "both kinds of child, not just the one whose key happens to match its model name"


LINEAGE = """
query Lineage($system: ID!, $maxDepth: Int) {
  lineageGraph(coordinateSystem: $system, maxDepth: $maxDepth) {
    root { id }
    nodes { __typename ... on ArrayDataset { name } ... on TableDataset { name } }
    edges { id kind input { id } output { id } }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_lineage_graph_steps_through_a_mixed_chain(authenticated_context: HttpContext) -> None:
    """image -> mask -> measurement table, walked from any point in it, in both directions.

    The point of the query: a client standing on the mask wants the acquisition above it
    *and* the table below it, and no single-hop field gives that. `coordinateGraph` cannot
    stand in -- it crosses every edge touching a space, so a registration would drag in
    every other dataset in the same world.

    The chain deliberately mixes kinds. The mask -> image edge is a real IDENTITY, and the
    table -> mask edge is UNMAPPABLE, which is exactly the hop a spatial walk
    (`lineage_ancestors`) refuses and a *historical* one must not.
    """
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, acquired)
    mask = await _derived(authenticated_context, "Mask", lens=lens)
    await _table(authenticated_context, "Objects", columns=_MEASUREMENT_COLUMNS, derived_from=[{"kind": "DATASET", "dataset": str(mask)}])

    mask_system = await sync_to_async(lambda: str(models.ArrayDataset.objects.get(pk=mask).intrinsic_coordinate_system.pk))()

    result = await schema.execute(LINEAGE, context_value=authenticated_context, variable_values={"system": mask_system, "maxDepth": None})
    assert not result.errors, result.errors

    graph = result.data["lineageGraph"]
    assert {(n["__typename"], n.get("name")) for n in graph["nodes"]} == {
        ("ArrayDataset", "Acquired"),
        ("ArrayDataset", "Mask"),
        ("TableDataset", "Objects"),
    }, "the whole chain, from the middle of it, in both directions"

    kinds = sorted(edge["kind"] for edge in graph["edges"])
    assert kinds == ["IDENTITY", "UNMAPPABLE"], "the UNMAPPABLE hop is walked: this is history, not placement"

    # And from the top of the chain the answer is the same component.
    acquired_system = await sync_to_async(lambda: str(acquired.intrinsic_coordinate_system.pk))()
    result = await schema.execute(LINEAGE, context_value=authenticated_context, variable_values={"system": acquired_system, "maxDepth": None})
    assert not result.errors, result.errors
    assert len(result.data["lineageGraph"]["nodes"]) == 3, "a component read from either end is the same component"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_lineage_graph_is_bounded_and_excludes_registrations(authenticated_context: HttpContext) -> None:
    """`maxDepth` bounds the walk, and a world never enters it.

    A registration is where data was *put*, not where it came from, so it is not a lineage
    edge at all -- which falls out of the shared predicate rather than being filtered for
    here: a world has no residents, so it is no container.
    """
    acquired = await seed.create_array_dataset(authenticated_context, "Acquired", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, acquired)
    mask = await _derived(authenticated_context, "Mask", lens=lens)
    await _table(authenticated_context, "Objects", columns=_MEASUREMENT_COLUMNS, derived_from=[{"kind": "DATASET", "dataset": str(mask)}])

    scene = await seed.create_scene(authenticated_context, "Composition")
    await seed.register_into_scene(authenticated_context, scene, acquired)

    acquired_system = await sync_to_async(lambda: str(acquired.intrinsic_coordinate_system.pk))()

    result = await schema.execute(LINEAGE, context_value=authenticated_context, variable_values={"system": acquired_system, "maxDepth": 1})
    assert not result.errors, result.errors
    names = {n.get("name") for n in result.data["lineageGraph"]["nodes"]}
    assert names == {"Acquired", "Mask"}, "one hop reaches the mask and stops short of its table"

    # The world the acquisition is registered into is nowhere in the graph, at any depth.
    result = await schema.execute(LINEAGE, context_value=authenticated_context, variable_values={"system": acquired_system, "maxDepth": None})
    assert not result.errors, result.errors
    assert all(node["__typename"] != "CoordinateSystem" for node in result.data["lineageGraph"]["nodes"])
    outputs = {edge["output"]["id"] for edge in result.data["lineageGraph"]["edges"]}
    assert str(scene.world.pk) not in outputs, "a registration is not a lineage edge"


async def _derived(ctx: HttpContext, name: str, *, lens) -> str:  # noqa: ANN001 - a seeded Lens
    """An array dataset derived from a lens, through the real ingest mutation."""
    from unittest.mock import patch

    store = await sync_to_async(models.ZarrStore.objects.create)(
        path=f"s3://zarr/{name}", bucket="zarr", key=name, shape=[64, 64], chunks=[64, 64], version="3", dtype="uint8", populated=True, organization=ctx.request.organization
    )
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            "mutation D($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }",
            context_value=ctx,
            variable_values={
                "input": {
                    "name": name,
                    "data": str(store.id),
                    "scales": [],
                    "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                    "derivedFrom": [{"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}, "valueRelation": "CATEGORIZED"}],
                }
            },
        )
    assert not result.errors, result.errors
    return result.data["createArrayDataset"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_documented_sequences_run_end_to_end(authenticated_context: HttpContext) -> None:
    """The exact call sequences in docs/derivation-api.md, through the real mutations.

    A doc that names a field the schema does not have is worse than no doc: it reads as
    verified. This runs both -- the SMLM table and its reconstruction, then the stack, its
    segmentation and its measurement table -- so neither can rot into fiction without a red
    test. The same guarantee `test_the_documented_sequence_runs_end_to_end` gives
    docs/field-transforms-api.md.
    """
    from unittest.mock import patch

    async def _zarr(key: str, shape: list[int]) -> models.ZarrStore:
        return await models.ZarrStore.objects.acreate(
            organization=authenticated_context.request.organization, key=key, bucket="zarr", shape=shape, chunks=shape, version="3", dtype="uint8", populated=True
        )

    async def _run(query: str, **variables) -> dict:
        with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
            result = await schema.execute(query, context_value=authenticated_context, variable_values=variables)
        assert not result.errors, result.errors
        return result.data

    # --- A. SMLM: the table, the reconstruction, the registration ----------------------
    locs_store = await _parquet(authenticated_context, "locs", [("y", "DOUBLE"), ("x", "DOUBLE"), ("photons", "DOUBLE"), ("precision", "DOUBLE")])
    locs = (
        await _run(
            """
            mutation ($data: ParquetLike!) {
              createTableDataset(input: {
                name: "locs"
                data: $data
                columns: [
                  {name: "y", dtype: "DOUBLE", axisType: SPACE, unit: "nanometer"},
                  {name: "x", dtype: "DOUBLE", axisType: SPACE, unit: "nanometer"},
                  {name: "photons", dtype: "DOUBLE"},
                  {name: "precision", dtype: "DOUBLE"}
                ]
              }) { id coordinateSystem { id axes { name type unit } } }
            }
            """,
            data=str(locs_store.pk),
        )
    )["createTableDataset"]
    assert [axis["name"] for axis in locs["coordinateSystem"]["axes"]] == ["y", "x"], "the coordinate columns are the axes"

    render_store = await _zarr("render", [64, 64])
    render = (
        await _run(
            """
            mutation ($data: ArrayLike!, $locs: ID!) {
              createArrayDataset(input: {
                name: "reconstruction"
                data: $data
                scales: []
                axes: [{name: "y", type: SPACE}, {name: "x", type: SPACE}]
                derivedFrom: [{
                  kind: TABLE_DATASET
                  tableDataset: $locs
                  transform: {kind: SCALE, scale: [10.0, 10.0]}
                  valueRelation: TRANSFORMED
                }]
              }) { id intrinsicSystem { id } }
            }
            """,
            data=str(render_store.id),
            locs=locs["id"],
        )
    )["createArrayDataset"]

    await _run(
        """
        mutation ($locs: ID!) {
          createCoordinateSystem(input: {
            name: "slide"
            axes: [{name: "y", type: SPACE, unit: "micrometer"}, {name: "x", type: SPACE, unit: "micrometer"}]
            registrations: [{tableDataset: $locs, transform: {kind: SCALE, scale: [0.001, 0.001]}, validity: VALIDATED}]
          }) { id }
        }
        """,
        locs=locs["id"],
    )

    # The doc's claim: register the table, and the reconstruction comes along.
    graph = (await _run(LINEAGE, system=render["intrinsicSystem"]["id"], maxDepth=None))["lineageGraph"]
    assert {n["__typename"] for n in graph["nodes"]} == {"ArrayDataset", "TableDataset"}
    assert [e["kind"] for e in graph["edges"]] == ["SCALE"], "a mappable derivation, so placement is inherited"

    # --- B. Segmentation: the stack, a lens, the mask, the table, the dereference -------
    raw_store = await _zarr("raw", [3, 64, 64])
    raw = (
        await _run(
            """
            mutation ($data: ArrayLike!) {
              createArrayDataset(input: {
                name: "raw"
                data: $data
                scales: []
                axes: [{name: "c", type: CHANNEL}, {name: "y", type: SPACE}, {name: "x", type: SPACE}]
              }) { id intrinsicSystem { id } }
            }
            """,
            data=str(raw_store.id),
        )
    )["createArrayDataset"]

    lens = (await _run('mutation ($d: ID!) { createLens(input: {dataset: $d, slices: []}) { id } }', d=raw["id"]))["createLens"]

    mask_store = await _zarr("mask", [64, 64])
    mask = (
        await _run(
            """
            mutation ($data: ArrayLike!, $lens: ID!) {
              createArrayDataset(input: {
                name: "nuclei labels"
                data: $data
                scales: []
                axes: [{name: "y", type: SPACE}, {name: "x", type: SPACE}]
                derivedFrom: [{
                  kind: LENS
                  lens: $lens
                  transform: {kind: BY_DIMENSION, inputAxes: ["y", "x"], outputAxes: ["y", "x"]}
                  valueRelation: CATEGORIZED
                }]
              }) { id intrinsicSystem { id } }
            }
            """,
            data=str(mask_store.id),
            lens=lens["id"],
        )
    )["createArrayDataset"]

    morphology_store = await _parquet(authenticated_context, "morphology", [("i", "BIGINT"), ("area", "DOUBLE"), ("mean_intensity", "DOUBLE")])
    morphology = (
        await _run(
            """
            mutation ($data: ParquetLike!, $mask: ID!) {
              createTableDataset(input: {
                name: "nuclei morphology"
                data: $data
                columns: [
                  {name: "i", dtype: "BIGINT", axisType: INDEX},
                  {name: "area", dtype: "DOUBLE"},
                  {name: "mean_intensity", dtype: "DOUBLE"}
                ]
                derivedFrom: [{kind: DATASET, dataset: $mask, valueRelation: TRANSFORMED}]
              }) { id coordinateSystem { id axes { name type } } derivedFrom { kind } }
            }
            """,
            data=str(morphology_store.pk),
            mask=mask["id"],
        )
    )["createTableDataset"]
    assert [axis["type"] for axis in morphology["coordinateSystem"]["axes"]] == ["INDEX"], "rows enumerate objects"
    assert [edge["kind"] for edge in morphology["derivedFrom"]] == ["UNMAPPABLE"], "an omitted transform claims no geometry"

    await _run(
        """
        mutation ($mask: ID!, $table: ID!) {
          createTransformation(input: {
            input: $mask
            output: $table
            transform: {kind: FIELD, field: $mask, inputAxes: ["y", "x"], outputAxes: ["i"]}
          }) { id }
        }
        """,
        mask=mask["intrinsicSystem"]["id"],
        table=morphology["coordinateSystem"]["id"],
    )

    # The doc's reading-it-back claim: from the mask, the stack above and the table below.
    graph = (await _run(LINEAGE, system=mask["intrinsicSystem"]["id"], maxDepth=None))["lineageGraph"]
    assert {n.get("name") for n in graph["nodes"]} == {"raw", "nuclei labels", "nuclei morphology"}
    assert sorted(e["kind"] for e in graph["edges"]) == ["BY_DIMENSION", "UNMAPPABLE"], "the FIELD edge is payload, not lineage"


def test_the_derivation_union_is_published_for_codegen() -> None:
    """The third `@unionElementOf` instance, held to the same two rules as the other two.

    Both are silent failures. A member input is referenced by no field, so dropping
    `derived_from_union_types` from the schema's `types=[...]` erases it from the SDL with
    no import error and no query error; and a member that declares only its own id field
    generates a type a client cannot construct, having no way to say what it is derived
    from *with*.
    """
    sdl = schema.as_str()
    common = ["kind", "transform", "valueRelation"]

    for member in derived_from_union_types:
        start = sdl.find(f"input {member.__name__} ")
        assert start >= 0, f"{member.__name__} missing from the SDL"
        header = sdl[start : sdl.find("{", start)]
        assert '@unionElementOf(union: "DerivedFromInput", discriminator: "kind", key: ' in header, f"{member.__name__} lacks its annotation"

        body = sdl[start : sdl.find("\n}", start)]
        missing = [field for field in common if not re.search(rf"^\s*{field}:", body, re.M)]
        assert not missing, f"{member.__name__} does not declare the common fields {missing}"

    # Every source kind a client may name has a member to name it with.
    enum_body = sdl[sdl.find("enum DerivationSourceKind") : sdl.find("}", sdl.find("enum DerivationSourceKind"))]
    for kind in enums.DerivationSourceKind:
        assert kind.value in enum_body
    assert len(derived_from_union_types) == len(list(enums.DerivationSourceKind))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_registration_is_not_reported_as_a_derivation(authenticated_context: HttpContext) -> None:
    """`derivedFrom` on a freestanding collection must stay empty after it is registered.

    `collection_derivation_edge` took the earliest edge out of the collection's system,
    kind-blind *and* order-blind -- so registering a freestanding collection with
    `createTransformation` made that registration answer a field documented as "the edge
    back into the data it was computed from". Pre-existing bug; the shared derivation
    predicate is what fixes it, since a world has no residents to be a container.
    """
    table = await _table(authenticated_context, "Freestanding", columns=_LOCALIZATION_COLUMNS)
    assert table["derivedFrom"] == [], "nothing was named, so nothing is reported"

    scene = await seed.create_scene(authenticated_context, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value,
            input=models.TableDataset.objects.get(pk=table["id"]).coordinate_system,
            output=scene.world,
            params={"scale": [0.001, 0.001]},
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(register)()

    result = await schema.execute(
        "query T($id: ID!) { tableDataset(id: $id) { derivedFrom { id kind } } }",
        context_value=authenticated_context,
        variable_values={"id": table["id"]},
    )
    assert not result.errors, result.errors
    assert result.data["tableDataset"]["derivedFrom"] == [], "a registration is where the data was put, not where it came from"
