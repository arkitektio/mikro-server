"""File links: the bytes a container was converted from, and the files written out of it.

The mechanism these tests pin exists because a file is a *store*, not a container. Every
`derivedFrom` entry resolves to a `CoordinateSystem`, because a derivation is an edge of the
coordinate graph and states how one space maps into another -- and a file has no space. So the
lineage between bytes and data is its own relation, and the load-bearing claim, checked by
`test_source_files_leave_the_coordinate_graph_alone`, is that recording it touches the graph
not at all.

Both directions are here, because they are one relation seen from opposite ends: `sourceFiles`
on a container's create mutation, `exportOf` on `fromFileLike`, and `linkFile` for either after
the fact.
"""

from unittest.mock import patch

import pytest

from core import enums, models
from core.inputs.file_link import EXPORT_OF_MEMBERS, file_link_union_types
from core.logic.file_link import _CONTAINER_FIELDS, _CONTAINER_MODELS
from kante.context import HttpContext
from mikro_server.schema import schema

from tests.seed import create_array_dataset, create_folder, create_file


async def _zarr(ctx: HttpContext) -> "models.ZarrStore":
    return await models.ZarrStore.objects.acreate(
        path="s3://zarr/cells",
        bucket="zarr",
        key="cells",
        shape=[64, 64],
        chunks=[64, 64],
        version="3",
        dtype="uint8",
        populated=True,
        organization=ctx.request.organization,
    )


async def _big_file_store(ctx: HttpContext, key: str = "scan") -> "models.BigFileStore":
    return await models.BigFileStore.objects.acreate(
        path=f"s3://bigfile/{key}",
        bucket="bigfile",
        key=key,
        populated=True,
        organization=ctx.request.organization,
    )


async def _create_array_dataset_with_sources(ctx: HttpContext, source_files: list) -> dict:
    """Run the real ingest mutation, naming the files the arrays were converted from."""
    store = await _zarr(ctx)
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            """
            mutation D($input: CreateArrayDatasetInput!) {
              createArrayDataset(input: $input) {
                id
                sourceFiles { id seriesIdentifier valueRelation direction file { id name } container { __typename } }
                derivedFrom { id }
              }
            }
            """,
            context_value=ctx,
            variable_values={
                "input": {
                    "name": "Cells",
                    "data": str(store.id),
                    "scales": [],
                    "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                    "sourceFiles": source_files,
                }
            },
        )
    assert not result.errors, result.errors
    return result.data["createArrayDataset"]


async def _file_names(ctx, filters: dict) -> set:
    """The names the `files` query returns for a filter, as a set."""
    result = await schema.execute(
        "query L($filters: FileFilter) { files(filters: $filters) { name } }",
        context_value=ctx,
        variable_values={"filters": filters},
    )
    assert not result.errors, result.errors
    return {row["name"] for row in result.data["files"]}


# --------------------------------------------------------------------------------------
# Totality. Three of `DerivedFromInput`'s five parallel lists are guarded by nothing, and a
# missing entry there fails at runtime on the first use with a green suite. This union does
# not repeat that: every kind is checked into every list it has to appear in.
# --------------------------------------------------------------------------------------


def test_every_container_kind_has_an_input_member() -> None:
    assert set(EXPORT_OF_MEMBERS) == {kind.value for kind in enums.FileLinkContainerKind}, "a FileLinkContainerKind with no member model is advertised in the SDL and unparseable"


def test_every_container_kind_has_a_published_sdl_member() -> None:
    assert len(file_link_union_types) == len(list(enums.FileLinkContainerKind)), "a member missing from file_link_union_types vanishes from the SDL silently -- nothing references it"


def test_every_container_kind_resolves_to_a_model_and_a_column() -> None:
    assert set(_CONTAINER_MODELS) == {kind.value for kind in enums.FileLinkContainerKind}, "a kind with no model raises KeyError on the first export link"
    for model in _CONTAINER_MODELS.values():
        assert model in _CONTAINER_FIELDS, f"{model.__name__} resolves from a discriminator but has no FileLink column to be written into"


def test_every_member_container_field_is_read_by_the_flat_input() -> None:
    """Each member's id field must be one the flat wire type actually forwards."""
    from core.inputs.file_link import _EXPORT_OF_CONTAINER_FIELDS

    for member in EXPORT_OF_MEMBERS.values():
        assert member.CONTAINER_FIELD in _EXPORT_OF_CONTAINER_FIELDS, f"{member.__name__} reads `{member.CONTAINER_FIELD}`, which to_pydantic never forwards -- every use would fail as a missing field"


# --------------------------------------------------------------------------------------
# The ingest direction.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_records_the_file_it_was_converted_from(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.lif", folder)

    dataset = await _create_array_dataset_with_sources(ctx, [{"file": str(file.id), "seriesIdentifier": "series-3", "valueRelation": "IDENTICAL"}])

    (link,) = dataset["sourceFiles"]
    assert link["file"]["name"] == "scan.lif"
    assert link["seriesIdentifier"] == "series-3"
    assert link["valueRelation"] == "IDENTICAL"
    assert link["direction"] == "SOURCE"
    assert link["container"]["__typename"] == "ArrayDataset"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_series_of_one_file_are_two_links(db, authenticated_context: HttpContext):
    """A dataset fused from two series names one file twice, and that is not a duplicate.

    This is why the series is part of the link's *identity* rather than a label on it: keyed
    on the file alone, the second entry would be refused as a repeat of the first.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.lif", folder)

    dataset = await _create_array_dataset_with_sources(
        ctx,
        [
            {"file": str(file.id), "seriesIdentifier": "series-3"},
            {"file": str(file.id), "seriesIdentifier": "series-7"},
        ],
    )

    assert [link["seriesIdentifier"] for link in dataset["sourceFiles"]] == ["series-3", "series-7"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_naming_one_file_twice_is_refused_with_a_sentence(db, authenticated_context: HttpContext):
    """The writer refuses before the database does, so the client gets prose not an IntegrityError."""
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.czi", folder)

    store = await _zarr(ctx)
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            "mutation D($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }",
            context_value=ctx,
            variable_values={
                "input": {
                    "name": "Cells",
                    "data": str(store.id),
                    "scales": [],
                    "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                    "sourceFiles": [{"file": str(file.id)}, {"file": str(file.id)}],
                }
            },
        )

    assert result.errors
    message = str(result.errors[0].message)
    assert "more than once" in message and "seriesIdentifier" in message, message
    assert "IntegrityError" not in message and "duplicate key" not in message, message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_source_files_leave_the_coordinate_graph_alone(db, authenticated_context: HttpContext):
    """The whole point of the split: recording a file mints no space and writes no edge.

    Had FILE become a `DerivedFromInput` kind, this dataset would carry a coordinate system
    for a file and an UNMAPPABLE edge into it -- a node and an edge in a geometry graph
    holding no geometry.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.czi", folder)

    systems_before = await models.CoordinateSystem.objects.acount()
    edges_before = await models.Transformation.objects.acount()

    dataset = await _create_array_dataset_with_sources(ctx, [{"file": str(file.id)}])

    assert dataset["derivedFrom"] == [], "a file is not something data is derived *from* -- it has no space to be derived from"
    # One system for the dataset's own pixel grid, and nothing else. No file space.
    assert await models.CoordinateSystem.objects.acount() == systems_before + 1
    assert await models.Transformation.objects.acount() == edges_before, "a file link must write no edge: there are no two spaces for one to relate"


# --------------------------------------------------------------------------------------
# The export direction.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_file_records_the_dataset_it_was_written_from(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await create_array_dataset(ctx, "Cells")
    store = await _big_file_store(ctx, "export")

    with (
        patch("datalayer.models.BigFileStore.fill_info", return_value=None),
        patch("datalayer.datalayer.Datalayer.get_object_size", return_value=42),
    ):
        result = await schema.execute(
            """
            mutation E($input: FromFileLike!) {
              fromFileLike(input: $input) {
                id
                name
                exportedFrom { direction seriesIdentifier container { __typename ... on ArrayDataset { name } } }
                derivedContainers { id }
              }
            }
            """,
            context_value=ctx,
            variable_values={
                "input": {
                    "file": str(store.id),
                    "fileName": "cells.ome.tiff",
                    "exportOf": [{"kind": "DATASET", "dataset": str(dataset.id), "valueRelation": "IDENTICAL"}],
                }
            },
        )

    assert not result.errors, result.errors
    file = result.data["fromFileLike"]
    # The supplied name, not the store's key: `fileName` was required and then ignored.
    assert file["name"] == "cells.ome.tiff"
    assert file["derivedContainers"] == []
    (link,) = file["exportedFrom"]
    assert link["direction"] == "RENDITION"
    assert link["container"] == {"__typename": "ArrayDataset", "name": "Cells"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_link_file_records_an_export_after_the_fact(db, authenticated_context: HttpContext):
    """A dataset exported months later gets the same row the create mutation would have written."""
    ctx = authenticated_context
    dataset = await create_array_dataset(ctx, "Cells")
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "cells.ome.tiff", folder)

    result = await schema.execute(
        """
        mutation L($input: LinkFileInput!) {
          linkFile(input: $input) { id direction container { __typename } file { name } }
        }
        """,
        context_value=ctx,
        variable_values={"input": {"file": str(file.id), "sourceOf": [{"kind": "DATASET", "dataset": str(dataset.id)}]}},
    )

    assert not result.errors, result.errors
    (link,) = result.data["linkFile"]
    assert link["direction"] == "RENDITION"
    assert link["container"]["__typename"] == "ArrayDataset"
    assert link["file"]["name"] == "cells.ome.tiff"

    unlinked = await schema.execute(
        "mutation U($input: UnlinkFileInput!) { unlinkFile(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": link["id"]}},
    )
    assert not unlinked.errors, unlinked.errors
    assert await models.FileLink.objects.acount() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_link_file_refuses_an_undecidable_direction(db, authenticated_context: HttpContext):
    """Naming both ends leaves it unsaid which was made from which, and that is the whole column."""
    ctx = authenticated_context
    dataset = await create_array_dataset(ctx, "Cells")
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "cells.tiff", folder)

    result = await schema.execute(
        "mutation L($input: LinkFileInput!) { linkFile(input: $input) { id } }",
        context_value=ctx,
        variable_values={
            "input": {
                "file": str(file.id),
                "dataset": str(dataset.id),
                "sourceFiles": [{"file": str(file.id)}],
            }
        },
    )

    assert result.errors
    assert "not both" in str(result.errors[0].message), result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_link_file_refuses_two_containers(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await create_array_dataset(ctx, "Cells")
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "cells.tiff", folder)

    # Two *different kinds* of container, since the input carries one field per kind.
    parquet = await models.ParquetStore.objects.acreate(path="s3://parquet/locs", bucket="parquet", key="locs", populated=True, organization=ctx.request.organization)
    table = await models.TableDataset.objects.acreate(name="Locs", store=parquet, creator=ctx.request.user, organization=ctx.request.organization)

    result = await schema.execute(
        "mutation L($input: LinkFileInput!) { linkFile(input: $input) { id } }",
        context_value=ctx,
        variable_values={
            "input": {
                "dataset": str(dataset.id),
                "tableDataset": str(table.id),
                "sourceFiles": [{"file": str(file.id)}],
            }
        },
    )

    assert result.errors
    assert "Name one container" in str(result.errors[0].message), result.errors[0].message


# --------------------------------------------------------------------------------------
# Strictness and scoping.
# --------------------------------------------------------------------------------------


def test_an_export_link_rejects_a_field_outside_its_kind() -> None:
    """The union is strict: a contradicting field is an error naming both, never a silent drop."""
    from core.inputs.file_link import ExportOfInput

    flat = ExportOfInput(
        kind=enums.FileLinkContainerKind.DATASET,
        dataset="1",
        table_dataset="2",
        mesh_collection=None,
        annotation_collection=None,
        series_identifier=None,
        value_relation=None,
    )
    with pytest.raises(ValueError) as err:
        flat.to_pydantic()
    assert "does not read `tableDataset`" in str(err.value)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_file_from_another_organization_is_refused(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """Every id a client sends is org-scoped, and a file link is no exception."""
    ctx = authenticated_context
    foreign_folder = await create_folder(other_org_context, "Theirs")
    foreign_file = await create_file(other_org_context, "theirs.czi", foreign_folder)

    store = await _zarr(ctx)
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            "mutation D($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }",
            context_value=ctx,
            variable_values={
                "input": {
                    "name": "Cells",
                    "data": str(store.id),
                    "scales": [],
                    "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                    "sourceFiles": [{"file": str(foreign_file.id)}],
                }
            },
        )

    assert result.errors, "a file belonging to another organization must not be linkable"


# --------------------------------------------------------------------------------------
# Reading it back by query.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_datasets_can_be_filtered_by_the_file_and_series_they_came_from(db, authenticated_context: HttpContext):
    """"Which datasets came from series 3 of this file" is a normal query, not a bespoke walk."""
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.lif", folder)

    await _create_array_dataset_with_sources(ctx, [{"file": str(file.id), "seriesIdentifier": "series-3"}])

    async def names(filters):
        result = await schema.execute(
            "query L($filters: ArrayDatasetFilter) { arrayDatasets(filters: $filters) { name } }",
            context_value=ctx,
            variable_values={"filters": filters},
        )
        assert not result.errors, result.errors
        return {row["name"] for row in result.data["arrayDatasets"]}

    assert await names({"sourceFile": str(file.id)}) == {"Cells"}
    assert await names({"sourceSeriesIdentifier": "series-3"}) == {"Cells"}
    assert await names({"sourceSeriesIdentifier": "series-9"}) == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_documented_read_fields_all_exist(db, authenticated_context: HttpContext):
    """Every field docs/derivation-api.md section C names must be real.

    A doc that names a field the schema does not have is worse than no doc -- it reads as
    verified. `test_the_documented_sequences_run_end_to_end` makes the same promise for
    sections A and B.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.lif", folder)
    await _create_array_dataset_with_sources(ctx, [{"file": str(file.id), "seriesIdentifier": "series-3"}])

    result = await schema.execute(
        """
        query Documented($file: ID!) {
          arrayDatasets {
            sourceFiles { file { name } seriesIdentifier }
            exports { file { name } }
          }
          files {
            derivedContainers { container { __typename } }
            exportedFrom { container { __typename } }
          }
          fromSeries: arrayDatasets(filters: {sourceFile: $file, sourceSeriesIdentifier: "series-3"}) { name }
        }
        """,
        context_value=ctx,
        variable_values={"file": str(file.id)},
    )

    assert not result.errors, result.errors
    assert result.data["fromSeries"] == [{"name": "Cells"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collision_with_a_link_already_on_record_writes_nothing(db, authenticated_context: HttpContext):
    """A second entry colliding with an existing link must not leave the first one written.

    The in-request duplicate is caught by `_refuse_duplicates` before anything is fetched;
    this is the other case -- a link already in the database -- and it is why the existence
    check sits in the resolve phase rather than in the write loop.

    It also pins what `createArrayDataset` leaves behind, which is *not* nothing: the dataset and
    its coordinate system are already committed by the time links are written. That is the
    resolver's pre-existing partial-creation exposure (its `anchors` loop can fail the same
    way), not something file links introduced, and it is deliberately not fixed here.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    first = await create_file(ctx, "a.czi", folder)
    second = await create_file(ctx, "b.czi", folder)

    dataset = await _create_array_dataset_with_sources(ctx, [{"file": str(first.id)}])

    # The same file again, behind a fresh one, against the dataset that already links it.
    result = await schema.execute(
        "mutation L($input: LinkFileInput!) { linkFile(input: $input) { id } }",
        context_value=ctx,
        variable_values={
            "input": {
                "dataset": dataset["id"],
                "sourceFiles": [{"file": str(second.id)}, {"file": str(first.id)}],
            }
        },
    )

    assert result.errors
    assert "already records file 'a.czi'" in str(result.errors[0].message), result.errors[0].message
    # The good entry ahead of the collision must not have been written.
    assert await models.FileLink.objects.acount() == 1, "a collision must roll back the entries before it"
    assert not await models.FileLink.objects.filter(file=second).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_three_container_filters_differ_only_by_direction(db, authenticated_context: HttpContext):
    """`sourceOf` and `exportedFrom` are one-way; `linkedTo` is both.

    One dataset with a file on each side of it, so a filter that ignores `direction` returns
    two names where it should return one.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    source = await create_file(ctx, "scan.czi", folder)
    export = await create_file(ctx, "cells.ome.tiff", folder)

    dataset = await _create_array_dataset_with_sources(ctx, [{"file": str(source.id)}])
    linked = await schema.execute(
        "mutation L($input: LinkFileInput!) { linkFile(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"file": str(export.id), "sourceOf": [{"kind": "DATASET", "dataset": dataset["id"]}]}},
    )
    assert not linked.errors, linked.errors

    ref = {"kind": "DATASET", "id": dataset["id"]}
    assert await _file_names(ctx, {"sourceOf": ref}) == {"scan.czi"}
    assert await _file_names(ctx, {"exportedFrom": ref}) == {"cells.ome.tiff"}
    assert await _file_names(ctx, {"linkedTo": ref}) == {"scan.czi", "cells.ome.tiff"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_container_ref_reads_the_kind_not_only_the_id(db, authenticated_context: HttpContext):
    """The reason the ref is `{kind, id}` and not a bare ID.

    A dataset and a table dataset have ids drawn from separate sequences, so an unqualified
    id cannot say which was meant. Rather than forcing a pk collision -- explicit `id=` on a
    BigAutoField leaves the sequence unadvanced and breaks later creates -- this asks for the
    *table's* pk under `kind: DATASET`. A mapping that ignored `kind` would return the
    table's file; the right one returns nothing.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    table_file = await create_file(ctx, "locs.csv", folder)

    parquet = await models.ParquetStore.objects.acreate(path="s3://parquet/locs", bucket="parquet", key="locs", populated=True, organization=ctx.request.organization)
    table = await models.TableDataset.objects.acreate(name="Locs", store=parquet, creator=ctx.request.user, organization=ctx.request.organization)
    linked = await schema.execute(
        "mutation L($input: LinkFileInput!) { linkFile(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"tableDataset": str(table.id), "sourceFiles": [{"file": str(table_file.id)}]}},
    )
    assert not linked.errors, linked.errors

    assert await _file_names(ctx, {"sourceOf": {"kind": "TABLE_DATASET", "id": str(table.id)}}) == {"locs.csv"}
    assert await _file_names(ctx, {"sourceOf": {"kind": "DATASET", "id": str(table.id)}}) == set(), "the kind must pick the column, not just the id"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_links_to_one_container_return_the_file_once(db, authenticated_context: HttpContext):
    """A to-many hop without `.distinct()` returns the row once per matching link."""
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.lif", folder)

    dataset = await _create_array_dataset_with_sources(
        ctx,
        [{"file": str(file.id), "seriesIdentifier": "series-3"}, {"file": str(file.id), "seriesIdentifier": "series-7"}],
    )

    result = await schema.execute(
        "query L($filters: FileFilter) { files(filters: $filters) { name } }",
        context_value=ctx,
        variable_values={"filters": {"linkedTo": {"kind": "DATASET", "id": dataset["id"]}}},
    )
    assert not result.errors, result.errors
    assert [row["name"] for row in result.data["files"]] == ["scan.lif"], "two links, one file, one row"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_not_derived_survives_a_second_link_join(db, authenticated_context: HttpContext):
    """`notDerived` combined with `sourceOf` -- two `links__` lookups in one query.

    This is why `notDerived` is a `pk__in` subquery rather than `~Q(links__direction=...)`:
    Django builds a second join for the second lookup, and the negated one stops meaning what
    it reads as.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    raw = await create_file(ctx, "scan.czi", folder)
    export = await create_file(ctx, "cells.ome.tiff", folder)

    dataset = await _create_array_dataset_with_sources(ctx, [{"file": str(raw.id)}])
    linked = await schema.execute(
        "mutation L($input: LinkFileInput!) { linkFile(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"file": str(export.id), "sourceOf": [{"kind": "DATASET", "dataset": dataset["id"]}]}},
    )
    assert not linked.errors, linked.errors

    ref = {"kind": "DATASET", "id": dataset["id"]}
    # Of the two files touching this dataset, only the raw one was not exported into.
    assert await _file_names(ctx, {"linkedTo": ref, "notDerived": True}) == {"scan.czi"}
    assert await _file_names(ctx, {"linkedTo": ref, "notDerived": False}) == {"cells.ome.tiff"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unlinked_finds_the_orphan_uploads(db, authenticated_context: HttpContext):
    """`unlinked` is stricter than `notDerived`: no links at all, in either direction."""
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    used = await create_file(ctx, "scan.czi", folder)
    await create_file(ctx, "stray.czi", folder)

    await _create_array_dataset_with_sources(ctx, [{"file": str(used.id)}])

    assert await _file_names(ctx, {"unlinked": True}) == {"stray.czi"}
    assert await _file_names(ctx, {"unlinked": False}) == {"scan.czi"}
    # Both are notDerived -- nothing was exported into either -- which is the weaker question.
    assert await _file_names(ctx, {"notDerived": True}) == {"scan.czi", "stray.czi"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_store_and_metadata_filters(db, authenticated_context: HttpContext):
    """`hasStore` and `populated` are deliberately not complementary."""
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    store = await _big_file_store(ctx, "done")
    await create_file(ctx, "complete.czi", folder, store=store)
    await create_file(ctx, "storeless.czi", folder)
    pending_store = await models.BigFileStore.objects.acreate(path="s3://bigfile/pending", bucket="bigfile", key="pending", populated=False, organization=ctx.request.organization)
    await create_file(ctx, "pending.czi", folder, store=pending_store)

    assert await _file_names(ctx, {"hasStore": True}) == {"complete.czi", "pending.czi"}
    assert await _file_names(ctx, {"hasStore": False}) == {"storeless.czi"}
    assert await _file_names(ctx, {"populated": True}) == {"complete.czi"}
    # The storeless file is absent from BOTH populated answers -- the join drops it.
    assert await _file_names(ctx, {"populated": False}) == {"pending.czi"}
    assert await _file_names(ctx, {"hasStore": False, "populated": False}) == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_extension_normalizes_dot_and_case(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    await create_file(ctx, "scan.CZI", folder)
    await create_file(ctx, "cells.ome.tiff", folder)

    for spelling in ("czi", ".czi", "CZI"):
        assert await _file_names(ctx, {"extension": spelling}) == {"scan.CZI"}, spelling
    # A double extension is matched as written, and does not also match bare `tiff`'s siblings.
    assert await _file_names(ctx, {"extension": "ome.tiff"}) == {"cells.ome.tiff"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_mime_group_classifies_a_vendor_file_the_content_type_cannot(db, authenticated_context: HttpContext):
    """The case the obvious implementation gets wrong.

    A CZI uploads as `application/octet-stream`, so a contentType-prefix rule would file it
    under OTHER -- exactly the set a client filtering for IMAGE wants to find.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    await create_file(ctx, "scan.czi", folder, content_type="application/octet-stream")
    await create_file(ctx, "locs.csv", folder, content_type="text/csv")
    await create_file(ctx, "surface.stl", folder)
    await create_file(ctx, "notes", folder)

    assert await _file_names(ctx, {"mimeGroup": "IMAGE"}) == {"scan.czi"}
    assert await _file_names(ctx, {"mimeGroup": "TABLE"}) == {"locs.csv"}
    assert await _file_names(ctx, {"mimeGroup": "MESH"}) == {"surface.stl"}
    # OTHER is the complement, so an extensionless file lands there and nothing else does.
    assert await _file_names(ctx, {"mimeGroup": "OTHER"}) == {"notes"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_link_lists_are_filterable(db, authenticated_context: HttpContext):
    """`FileLinkFilter` reaches the SDL only by being some field's argument.

    Declared on the django_type alone it was absent from the schema entirely, so this pins
    the seam as well as the behaviour.
    """
    ctx = authenticated_context
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.lif", folder)
    await _create_array_dataset_with_sources(
        ctx,
        [{"file": str(file.id), "seriesIdentifier": "series-3"}, {"file": str(file.id), "seriesIdentifier": "series-7"}],
    )

    result = await schema.execute(
        """
        query { arrayDatasets {
          all: sourceFiles { seriesIdentifier }
          one: sourceFiles(filters: {seriesIdentifier: {exact: "series-3"}}) { seriesIdentifier }
        } }
        """,
        context_value=ctx,
    )
    assert not result.errors, result.errors
    (dataset,) = result.data["arrayDatasets"]
    assert len(dataset["all"]) == 2
    assert [link["seriesIdentifier"] for link in dataset["one"]] == ["series-3"]


def test_filefilter_publishes_no_surface_filterlookup_already_covers() -> None:
    """`sizes` and `contentTypes` are absent on purpose, not by oversight.

    `IntFilterLookup`/`StrFilterLookup` already carry `inList` and `range`, so
    `size: {range: [a, b]}` and `contentType: {inList: [...]}` answer both. A dedicated field
    would be duplicate SDL surface with a second implementation to keep in step.
    """
    sdl = schema.as_str()
    body = sdl[sdl.find("input FileFilter") : sdl.find("\n}", sdl.find("input FileFilter"))]
    assert "sizes:" not in body
    assert "contentTypes:" not in body
    assert "linkedToDataset" not in body, "superseded by linkedTo, which covers all four container kinds"
