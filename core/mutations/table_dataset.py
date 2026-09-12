"""Mutations for table datasets: parquet-backed tables of scientific records.

A table dataset is the coordinate graph's home for tabular data -- one row per
segmented object, per localization, per cell. It parallels ``createArrayDataset`` but
is backed by a Parquet store and has no multiscale. Its declared coordinate columns
become the axes of a coordinate system it owns, which is what lets a localization
table be placed in a scene; a table with no coordinate columns degenerates to a
single INDEX axis whose only honest edge is UNMAPPABLE -- the measurement-table case
the old FeatureCollection served.
"""

from typing import Annotated, ClassVar, Literal

import strawberry
from django.db import transaction
from kante.types import Info
from pydantic import BaseModel, ConfigDict, Field

import kante
from kanne_server import scalars as kanne_scalars

from core import enums, models, scalars, types
from core.creation import CreationContext
from core.logic import pickers
from core.inputs.file_link import SourceFileInput, SourceFileInputModel
from core.inputs.coords import AxisInputModel, DerivedFromInput, DerivedFromSpec
from core.inputs.identification import IdentificationInput, IdentificationSpec
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import file_link as file_link_logic
from core.logic import folder as folder_logic
from core.logic import graph as graph_logic
from core.logic import identification as identification_logic
from core.logic import tables as tables_logic
from core.mutations._generic import make_delete, self_owner
from core.scoping import get_for_org

#: The degenerate space of a table with no coordinate columns: one axis enumerating the
#: rows. No unit (nothing to measure between object 3 and object 4), no second axis (the
#: columns are named in the Parquet, not indexed by position). Its only honest edge to a
#: source is UNMAPPABLE.
_INDEX_AXES = [AxisInputModel(name="object", type=enums.AxisType.INDEX)]

#: The coordinate axis types a table column may declare. MICROTIME/SPECTRUM pass the unit and
#: ordering checks but nothing renders them from a table yet, so they are held back.
#:
#: INDEX is here for lineage, not rendering: an id column whose values are a label mask's
#: pixels is the *coordinate* of the space those pixels point into, exactly as an `x` column
#: in nanometres is the coordinate of a spatial axis. A coordinate column's values are always
#: its coordinates -- that rule is what makes this consistent rather than an exception. The
#: rendering rationale that holds MICROTIME back does not reach it, and it cannot leak into a
#: layer: both layer paths filter coordinate columns to SPACE.
_COORDINATE_AXIS_TYPES = {enums.AxisType.SPACE, enums.AxisType.TIME, enums.AxisType.INDEX}

#: The roles a unit means something on. A coordinate's position and a measurement's value are
#: both quantities -- an area is 'micrometer**2', a marker level is 'a.u.' -- and a client that
#: plots either needs the unit as much as the number. An id, a track id, a label and a colour
#: are not measured, so a unit on one would name a metric that does not exist: the same argument
#: the INDEX rule below makes, applied to the roles rather than the axis types.
_UNIT_BEARING_ROLES = {enums.ColumnRole.COORDINATE, enums.ColumnRole.ATTRIBUTE}


class ColumnInputModel(BaseModel):
    """One column of the table: what the file calls it, what it holds, and what its values are."""

    model_config = ConfigDict(extra="forbid")

    name: str
    dtype: str | None = None
    role: enums.ColumnRole | None = None
    axis_type: enums.AxisType | None = None
    unit: str | None = None
    long_name: str | None = None
    description: str | None = None
    identified_by: list[IdentificationSpec] = Field(default_factory=list)


@kante.pydantic_input(
    ColumnInputModel,
    description=(
        "One column of the table -- THE one declaration a column gets. **Every column of the Parquet is declared, and the declaration is checked against the file** -- same names, "
        "same order, same types -- so a declaration that has drifted from the data is refused rather than stored. That check is the whole reason `name` is here: it is a fact about "
        "the file, and stating it is how a caller says which file they think they are describing. `dtype` is **optional** -- the server read every column's type off the Parquet "
        "when the upload finished, so it is checked when given and taken from the file when not. Given, it is a **DuckDB** type name (`BIGINT`, `DOUBLE`, `VARCHAR`), not a pandas "
        "one where a float64 is a `double`. A non-null `axisType` makes the column an axis of the table's own space; **the axis-typed columns, in this list's (= the file's) order, "
        "ARE the space** -- a table has no byte order, its axes are named columns, and an edge wanting a different order states its own `inputAxes`/`outputAxes`. `identifiedBy` "
        "says what the values are, for an axis and a data column alike"
    ),
)
class ColumnInput:
    """One declared column of a table dataset."""

    name: str = strawberry.field(description="The column name, matching the Parquet column at this position")
    dtype: str | None = strawberry.field(
        default=None,
        description=(
            "The column's type as a DuckDB type string -- 'BIGINT', 'DOUBLE', 'VARCHAR', 'BOOLEAN'. **Optional, and omitting it is the ordinary case**: the type is a fact about "
            "the file, which the server already read back off the Parquet when the upload finished, so stating it is transcription rather than information. Given, it is checked "
            "against the file and a mismatch is refused -- worth doing for a column whose type a caller means to assert. Omitted, the file's own answer is recorded"
        ),
    )
    role: enums.ColumnRole | None = strawberry.field(
        default=None,
        description="What a DATA column is for: ATTRIBUTE (the default), ID, TRACK_ID, LABEL or COLOR. Refused alongside `axisType` -- an axis column IS a COORDINATE, and saying so twice would be the same field answering the same question in two places",
    )
    axis_type: enums.AxisType | None = strawberry.field(
        default=None,
        description=(
            "Non-null makes this column an axis of the table's own space: SPACE, TIME or INDEX -- the one thing about a column the bytes cannot say (a float64 is a float64 whether "
            "it is a nanometre or a row id). The axis-typed columns, in declaration (= file) order, are the space; there is no separate axes list, because a table's axes are named "
            "columns and every consumer addresses them by name. Null -- the ordinary case -- is a data column"
        ),
    )
    unit: kanne_scalars.Unit | None = strawberry.field(
        default=None,
        description=(
            "The unit the column's values are in -- 'micrometer**2' for a measured attribute, 'nanometer' for a SPACE axis. A pint unit, validated on the way in; 'a.u.' for "
            "arbitrary units; nothing but parseability is checked (an area is not a length). Carried by ATTRIBUTE columns and SPACE/TIME axes; omit it for pixel-index coordinates "
            "(a table's spatial axes must be all calibrated or all pixel-index), and forbidden on an INDEX axis, which enumerates and has no metric"
        ),
    )
    long_name: str | None = strawberry.field(default=None, description="A human-readable name for the column")
    description: str | None = strawberry.field(default=None, description="A free-form description of what the column holds, e.g. 'mean GFP intensity within the segmented object'")
    identified_by: list[IdentificationInput] = strawberry.field(
        default_factory=list,
        description=(
            "What this column's values ARE, for an axis and a data column alike -- the one spelling of every 'values here identify things there' claim (it retired the old sibling "
            "`references` field). On an INDEX axis: a DATASET, MESH_COLLECTION or NETWORK_COLLECTION authors a FIELD edge from that source into this table -- the direction "
            "`attributePlans` discovers, and the opposite of the lineage `derivedFrom` records; a list, because fan-in is real (a nucleus mask and a cell mask may key one axis, one "
            "edge each). A TABLE authors no edge and states a foreign key instead -- on an INDEX axis (the product-space case) or on a plain data column (an `instance_id` "
            "ATTRIBUTE/ID column referencing a table of tracks: the edge of the join graph `colorBys` walks). A NETWORK_COLLECTION_NODES likewise authors none and says an INDEX "
            "axis' positions are that collection's node ids, scoped by a sibling INDEX axis keyed by the same collection's objects -- one such axis makes the table per-node, two "
            "make it per-edge (source, target, in column order). Refused on SPACE/TIME axes, whose values are positions and cannot also be ids"
        ),
    )


class CreateTableDatasetInputModel(BaseModel):
    name: str
    data: str
    columns: list[ColumnInputModel] = Field(default_factory=list)
    description: str | None = None
    folder: str | None = None
    derived_from: list[DerivedFromSpec] | None = None
    source_files: list[SourceFileInputModel] | None = None


@kante.pydantic_input(
    CreateTableDatasetInputModel,
    description=(
        "Input for creating a table dataset from a Parquet store. A column is declared ONCE, in `columns`: a non-null `axisType` makes it an axis of the coordinate system the "
        "table owns, and the axis-typed columns, in list (= file) order, are the space -- there is no separate axes list, because a table's axes are named columns and every "
        "consumer addresses them by name. Declare no axis-typed columns for a pure measurement table (its rows enumerate objects, its space is a synthetic `object` axis, and its "
        "lineage edge is UNMAPPABLE)"
    ),
)
class CreateTableDatasetInput:
    """Input for creating a table dataset."""

    name: str = strawberry.field(description="The name of the table dataset")
    data: scalars.ParquetLike = strawberry.field(description="The uploaded Parquet store holding the rows. Upload it through the normal parquet path (requestParquetUpload) and pass the store id here")
    columns: list[ColumnInput] = strawberry.field(
        default_factory=list,
        description=(
            "What is true of a column beyond what the Parquet already says -- axis-ness (`axisType`), identification (`identifiedBy`), role, unit, prose. Every column of the "
            "file becomes a column of the table whether or not it is named here; name one only where the default is wrong. The axis-typed entries, in this order, are the table's "
            "space"
        ),
    )
    description: str | None = strawberry.field(default=None, description="An optional description")
    folder: strawberry.ID | None = strawberry.field(
        default=None,
        description="The folder to file this table dataset in. Organisational only -- it says nothing about where the rows sit in space. Defaults to the user's default folder",
    )
    derived_from: list[DerivedFromInput] | None = strawberry.field(
        default=None,
        description="What this table was computed from -- the instance mask its rows were segmented out of, say. One entry per source; the first is the primary parent. Each names its source and how the table's own space relates to that source's: **omit the transform and the edge is UNMAPPABLE**, which records the lineage and claims no geometry, the truth for a measurement table whose rows are not anywhere. To place a localization table, state a mappable kind. Registering the table's space into a scene is a separate step: createTransformation, then the layer",
    )
    source_files: list[SourceFileInput] | None = strawberry.field(
        default=None,
        description="Optional statement of which files this table was loaded from -- the CSV or parquet a converter read. **Not a `derivedFrom` entry, deliberately**: a derivation is an edge of the coordinate graph and relates two spaces, while a file has no space. This records lineage between bytes and data and leaves the graph untouched",
    )


#: The width past which the object is a matrix rather than a table. A table's columns are
#: distinct measurements -- a volume, a marker level, a label -- and each becomes a `Column`
#: row, a picker entry and a term in every hover plan's SELECT. A matrix's columns are
#: *positions along an axis*: gene 4,711 is not a different kind of measurement from gene 4,712,
#: it is the same measurement at another coordinate. `createSparseDataset` is what describes
#: that, and it costs one axis and one picker entry instead of thousands.
_MAX_TABLE_COLUMNS = 3000


def _too_wide(count: int, name: str) -> str:
    """Why a file this wide is not a table, and what to do with it instead.

    Long on purpose: a caller who hits this has a real object and needs to know that the
    refusal is about *shape* rather than size, and that there is somewhere for it to go.
    """
    return (
        f"'{name}' declares {count:,} columns, past the {_MAX_TABLE_COLUMNS:,} at which this stops being a table.\n"
        "\n"
        "The limit is about shape, not size. A table's columns are distinct measurements -- an area, a marker "
        "level, a label -- so each one earns a `Column` row, an entry in every picker, and a term in every hover "
        f"plan's SELECT. {count:,} of those means a {count:,}-column SELECT every time a viewer hovers a pixel, "
        "and a picker nobody can scroll.\n"
        "\n"
        "A file this wide is almost always a *matrix*: an expression matrix over genes, an intensity matrix over "
        "metabolites, a feature matrix over channels. Its columns are not different measurements -- they are the "
        "same measurement at different positions along an axis, and gene 4,711 differs from gene 4,712 only in "
        "where it sits.\n"
        "\n"
        "That is what `createSparseDataset` is for. Upload the matrix as a sparse store (see `requestSparseUpload` "
        "and `finishSparseUpload`), then declare its two axes -- the objects and the features -- and say what "
        "identifies each: a label mask whose pixel values are the object ids, and a table whose rows are the "
        "features. The features become **one axis with one picker entry**, not one entry per feature, and a "
        "colouring names a position along it. The per-feature metadata that would have been column names goes in "
        "an ordinary narrow table, keyed to that axis.\n"
        "\n"
        "If the columns really are distinct measurements and there really are more than "
        f"{_MAX_TABLE_COLUMNS:,} of them, split the table."
    )


def _resolve_store(info: Info, identifier: str, name: str) -> "models.ParquetStore":
    """The store this table is, refusing one that does not know its own columns.

    A table declares *semantics for columns*, and the columns are the file's. A store whose
    upload was never finished has no account of itself, so a table over it could declare
    nothing and infer nothing -- and `columns_for_store` would fall back to describing the
    file over the network, on a path that now runs for every create. Refused loudly instead.

    The sibling of :func:`core.mutations.sparse_dataset._resolve_store`, and the same argument:
    a store that knows nothing about itself is the state this design exists to make
    unrepresentable.
    """
    store = get_for_org(models.ParquetStore, info, id=identifier)
    if not store.populated:
        # `fill_info` is still called from the create path -- nothing invokes
        # `finishParquetUpload` -- so this is reached only if that call also failed.
        store.fill_info()
    if store.columns is None:
        raise ValueError(
            f"Parquet store {store.pk} has no recorded schema, so nothing is known about what '{name}' would hold. Its columns are read by `fill_info` when the upload is "
            "finished; without them the table could neither infer a column nor check a declared one."
        )
    return store


def _validate_declaration(
    file_columns: list, columns: list[ColumnInputModel], name: str
) -> None:
    """Refuse a declaration that does not describe this file, before anything is written.

    **Every column is declared and every declaration is checked**: same names, same order,
    same types as the Parquet's own account of itself, which ``fill_info`` recorded when the
    upload finished. Two statements about the same bytes, and either one alone is worth less
    than the pair -- the caller says which file they think they are describing, and the file
    says whether they are right.

    That check is what ``validateSchema`` promised and never made. Its description claimed it
    rejected "any declared column whose name/dtype does not match the file"; the implementation
    compared ``col.name not in actual`` and discarded the type half, and no test ever set the
    flag. So a table could be uploaded declaring DOUBLE where the file said FLOAT, and nothing
    on either side would say so. It is not a flag now: there is nothing to opt into.
    """
    if len(file_columns) > _MAX_TABLE_COLUMNS:
        raise ValueError(_too_wide(len(file_columns), name))

    declared_names = [column.name for column in columns]
    duplicates = sorted({column for column in declared_names if declared_names.count(column) > 1})
    if duplicates:
        raise ValueError(f"'{name}' declares the column {duplicates} more than once. Each column of the file is declared once.")

    file_names = [column.name for column in file_columns]
    if declared_names != file_names:
        missing = [column for column in file_names if column not in set(declared_names)]
        extra = [column for column in declared_names if column not in set(file_names)]
        detail = []
        if missing:
            detail.append(f"the file has {missing} and the declaration does not")
        if extra:
            detail.append(f"the declaration has {extra} and the file does not")
        if not detail:
            detail.append(f"they are the same columns in a different order -- the file runs {file_names} and the declaration runs {declared_names}")
        raise ValueError(
            f"'{name}' does not describe its Parquet: {'; '.join(detail)}. Every column of the file is declared, in the file's order, because the declaration is a statement "
            "about *these* bytes -- and a declaration that has drifted from the data is worth less than none, since everything downstream reads it as true."
        )

    # A column that declares no dtype asserts nothing about its type, so there is nothing to
    # contradict -- the file's own answer is recorded for it instead. Only a stated type is checked.
    wrong = [
        (declared.name, declared.dtype, recorded.type)
        for declared, recorded in zip(columns, file_columns)
        if declared.dtype is not None and declared.dtype != recorded.type
    ]
    if wrong:
        detail = "; ".join(f"'{column}' is declared {given} and the file records {recorded}" for column, given, recorded in wrong)
        raise ValueError(
            f"'{name}' declares types the Parquet does not have: {detail}. `dtype` is a **DuckDB** type name, which is what the file is read back as -- a pandas float64 is a "
            "`DOUBLE` and a float32 is a `FLOAT`, and the pandas spellings are not these."
        )

    for column in columns:
        if column.axis_type is not None and column.role is not None:
            # An axis column IS a COORDINATE by the fact of `axisType`. A `role` beside it is
            # the same field answering the same question twice -- and the two-doors seam this
            # flattening exists to close.
            raise ValueError(
                f"Column '{column.name}' of '{name}' declares both `axisType` and `role`. An axis column is a COORDINATE by that fact; stating a role beside it would be a "
                "second answer to the same question. Drop `role`."
            )
        if column.role == enums.ColumnRole.COORDINATE:
            raise ValueError(
                f"Column '{column.name}' of '{name}' is declared COORDINATE, but a coordinate column is an axis: say so with `axisType` (SPACE, TIME or INDEX), which is what "
                "makes it one -- its role follows from that."
            )
        if column.axis_type is None and column.unit is not None and (column.role or enums.ColumnRole.ATTRIBUTE) not in _UNIT_BEARING_ROLES:
            raise ValueError(
                f"Column '{column.name}' of '{name}' declares a unit but has role {column.role.value}, which is not measured -- a unit on it would name a metric that does not "
                "exist. Only an ATTRIBUTE column (or an axis) carries one."
            )
        if column.axis_type is not None and column.axis_type not in _COORDINATE_AXIS_TYPES:
            raise ValueError(
                f"Column '{column.name}' of '{name}' has axisType {column.axis_type.value}, but a table's axes are SPACE, TIME or INDEX -- a row holds a position, an instant "
                "or an enumeration, and nothing else is one of those."
            )
        # An INDEX axis enumerates -- object 3, object 4 -- and the distance between two of
        # them is not a small number, it is not a number. A unit would name a metric that does
        # not exist. Refused here because `assert_unit_matches_type` cannot: INDEX is absent
        # from its dimension map, which reads as "any unit is fine".
        if column.axis_type == enums.AxisType.INDEX and column.unit is not None:
            raise ValueError(
                f"Column '{column.name}' of '{name}' is an INDEX axis, which has no metric -- the distance between object 3 and object 4 means nothing -- so it carries no "
                "unit. Drop `unit`."
            )

    for role, label in ((enums.ColumnRole.TRACK_ID, "TRACK_ID"), (enums.ColumnRole.ID, "ID")):
        count = sum(1 for column in columns if column.role == role)
        if count > 1:
            raise ValueError(f"'{name}' declares {count} {label} columns, but a table has at most one.")


def resolve_reference_target(info: Info, target_id: str, label: str) -> models.TableDataset:
    """The table ``target_id`` names, refusing one a single value cannot identify a row of.

    Extracted so the sparse path uses the same rule rather than a second copy of it: a sparse
    dataset's axis references a table exactly as a column does, and "keyed by exactly one INDEX
    coordinate column, and not a synthetic one" is the same requirement in both places.

    ``label`` names the thing doing the referencing, so the refusal reads about the caller's
    column or axis rather than about an id.
    """
    target = get_for_org(models.TableDataset, info, id=target_id)
    axes = target.axes
    if len(axes) != 1 or axes[0].type != enums.AxisType.INDEX.value:
        described = ", ".join(f"{axis.name}:{axis.type}" for axis in axes) or "none"
        raise ValueError(f"{label} references table '{target.name}', but a reference target must be keyed by exactly one INDEX axis (its axes are [{described}]). A composite-keyed table cannot be identified by a single value.")
    if not target.columns.filter(role=enums.ColumnRole.COORDINATE.value, name=axes[0].name).exists():
        raise ValueError(f"{label} references table '{target.name}', whose INDEX axis '{axes[0].name}' is synthetic row enumeration (the table declares no coordinate columns). There is no column to look a value up in, so it cannot be a reference target.")
    return target


def _assert_node_axes_are_scoped(
    name: str,
    node_reference_targets: "dict[str, models.NetworkCollection]",
    keyed: "list[tuple[str, object]]",
) -> None:
    """The two shape rules a node-identified axis carries, refused before anything is written.

    A node id is unique only within its traced object, so a node axis means something exactly
    when a sibling axis is keyed by the same collection's object ids -- without it a row's node
    is ambiguous the moment two objects reuse an id, which is every collection whose tracer
    numbers each neuron from one. And a network's edges are pairwise, so two node axes over one
    collection are its edges' (source, target) and three are a claim its geometry cannot hold.
    """
    from core.inputs.identification import NetworkCollectionIdentifiesInputModel

    object_keyed = {
        identification.source_id
        for _, identification in keyed
        if isinstance(identification, NetworkCollectionIdentifiesInputModel)
    }
    per_collection: dict[int, list[str]] = {}
    for axis_name, collection in node_reference_targets.items():
        per_collection.setdefault(collection.pk, []).append(axis_name)
        if str(collection.pk) not in object_keyed:
            raise ValueError(
                f"Axis '{axis_name}' of '{name}' holds node ids of network collection {collection.pk}, but no sibling axis is keyed by that collection's object ids. "
                f"A node id is unique only within its traced object, so without the object axis a row's node is ambiguous -- declare an INDEX axis beside it with "
                f"`identifiedBy: [{{kind: NETWORK_COLLECTION, networkCollection: {collection.pk}}}]`."
            )
    for pk, axes in per_collection.items():
        if len(axes) > 2:
            raise ValueError(
                f"Axes {sorted(axes)} of '{name}' all hold node ids of network collection {pk}. Three or more axes of node ids over one collection would be a hyperedge, "
                f"which a network's pairwise edges cannot address: one axis is a node table, two are an edge table -- (source, target), in column order."
            )


def create_table_dataset(info: Info, input: CreateTableDatasetInput) -> types.TableDataset:
    """Create a table dataset, its owned coordinate system, and (optionally) its lineage edge."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    # The file's own account, first: the declaration below is checked against it, column for
    # column and type for type. That is what `validateSchema` promised and never did -- it
    # compared names only, and only when a caller opted in. There is nothing to opt into now.
    store = _resolve_store(info, model.data, model.name)
    file_columns = tables_logic.columns_for_store(store)

    _validate_declaration(file_columns, model.columns, model.name)

    # The axis-typed columns, in declaration (= file) order, ARE the space. There is no
    # separate axes list to reorder them with, deliberately: a table has no byte order, its
    # axes are named columns every consumer addresses by name, and an edge that wants a
    # different order states its own axis lists.
    axis_columns = [column for column in model.columns if column.axis_type is not None]

    # The identifications, split into the three things they become: the tables a column
    # enumerates or references, the network collections whose NODES an INDEX axis enumerates,
    # and the sources that author a FIELD edge -- one entry per source, so an axis keyed by
    # two masks appears twice. Shared with the sparse create. One door for every column:
    # this is where the old `ColumnInput.references` lives now, as a TABLE identification on
    # a data column.
    reference_targets, node_reference_targets, keyed = identification_logic.split_identifications(
        info,
        name=model.name,
        entries=[(column.name, column.identified_by) for column in model.columns],
        axis_types={column.name: column.axis_type for column in model.columns},
    )
    _assert_node_axes_are_scoped(model.name, node_reference_targets, keyed)

    # Atomic, because the table row, its columns and its space are all written before its
    # derivation edges are checked: an edge whose rank the axes refuse -- a SCALE onto an
    # INDEX space, say -- would otherwise leave an orphan table behind and return an error.
    # The same guarantee `create_coordinate_system` keeps for a space and its registrations.
    with transaction.atomic():
        dataset = models.TableDataset.objects.create(
            name=model.name,
            description=model.description,
            store=store,
            folder=folder_logic.folder_for_new_container(info, ctx, model.folder, model.derived_from),
            creator=ctx.user,
            organization=ctx.organization,
            **ctx.provenance_kwargs(),
        )

        # One row per declared column, which is one row per column of the file -- the two are
        # the same list, checked against each other above. An axis-typed column is a
        # COORDINATE by that fact rather than by saying so twice, and every other fact reads
        # straight off the one declaration -- the two-doors merge this used to do is gone.
        models.Column.objects.bulk_create(
            [
                models.Column(
                    table=dataset,
                    order=index,
                    name=column.name,
                    # The declared type where one was declared, the file's where not. They agree by
                    # the check above, so this is the same value by either route.
                    dtype=column.dtype if column.dtype is not None else file_columns[index].type,
                    role=(
                        enums.ColumnRole.COORDINATE.value
                        if column.axis_type is not None
                        else (column.role or enums.ColumnRole.ATTRIBUTE).value
                    ),
                    axis_type=column.axis_type.value if column.axis_type is not None else None,
                    unit=column.unit,
                    long_name=column.long_name,
                    description=column.description,
                    references=reference_targets.get(column.name),
                    node_references=node_reference_targets.get(column.name),
                )
                for index, column in enumerate(model.columns)
            ]
        )

        system = models.CoordinateSystem.objects.create(
            name=f"{model.name}/table",
            creator=ctx.user,
            organization=ctx.organization,
        )
        dataset.coordinate_system = system
        dataset.save(update_fields=["coordinate_system"])
        if axis_columns:
            graph_logic.create_table_axes(system, axis_columns)
        else:
            graph_logic.create_pixel_axes(system, _INDEX_AXES)

        coordinate_system_logic.write_derivation_edges(info, name=dataset.name, own_system=system, derived_from=model.derived_from or [], ctx=ctx)
        file_link_logic.write_file_links(info, container=dataset, source_files=model.source_files or [], ctx=ctx)
        # `produces` is stated now rather than derived: every entry names the axis it keys,
        # so a source that supplies a different one is refused naming both halves instead of
        # "one place holds one id".
        coordinate_system_logic.write_key_edges(
            info,
            name=dataset.name,
            own_system=system,
            keyed_by=[identification for _, identification in keyed],
            produces=[axis_name for axis_name, _ in keyed],
            ctx=ctx,
        )

    return dataset


class UpdateTableDatasetInputModel(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None


@kante.pydantic_input(UpdateTableDatasetInputModel, description="Input for renaming or redescribing a table dataset. These two fields are the whole of what is editable: the store, the declared columns and the coordinate system derived from them are fixed at creation, and a recomputation is a new table")
class UpdateTableDatasetInput:
    """Input for updating a table dataset."""

    id: strawberry.ID = strawberry.field(description="The ID of the table dataset to update")
    name: str | None = strawberry.field(default=None, description="A new name")
    description: str | None = strawberry.field(default=None, description="A new description")


def update_table_dataset(info: Info, input: UpdateTableDatasetInput) -> types.TableDataset:
    """Rename a table dataset, or redescribe it. Those two fields are the whole of what is editable.

    Deliberately not here: the store, the declared columns, and the coordinate system derived
    from them. All three are written once by ``create_table_dataset``, and a table's own system
    is refused by ``updateCoordinateSystem`` besides -- axis order is written by enumeration and
    the rest of the graph is measured against it, so an axis edit is a different space, not an
    edit of this one. A recomputation is a new table.
    """
    model = input.to_pydantic()
    dataset = get_for_org(models.TableDataset, info, id=model.id)
    if model.name is not None:
        dataset.name = model.name
    if model.description is not None:
        dataset.description = model.description
    dataset.save()
    return dataset


class DeleteTableDatasetInputModel(BaseModel):
    id: str = Field(description="The ID of the table dataset to delete")


@kante.pydantic_input(DeleteTableDatasetInputModel, description="Input for deleting a table dataset by ID")
class DeleteTableDatasetInput:
    """Input for deleting a table dataset by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the table dataset to delete")


#: PROTECT: a layer's colour or filter picker names its table by id, inside JSON, so there is no
#: foreign key to cascade and a deleted table leaves a join nothing can execute. See
#: :func:`core.logic.pickers.assert_table_not_in_a_picker` for why refusing beats orphaning.
delete_table_dataset = make_delete(models.TableDataset, DeleteTableDatasetInput, owner=self_owner, guard=pickers.assert_table_not_in_a_picker)
