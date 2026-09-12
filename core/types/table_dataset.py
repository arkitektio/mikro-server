"""GraphQL types for the parquet-backed table dataset and its declared columns."""

from typing import TYPE_CHECKING, Annotated, List, Optional

import strawberry
from strawberry import auto

import kante
from kante.types import Info
from kanne_server import scalars as kanne_scalars

from datalayer.types import ParquetStore

from core import enums, filters, models, order, scalars
from core.logic import file_link as file_link_logic
from core.logic import graph as graph_logic
from core.types.auth import ProvenanceEntry, Task, User
from core.types.coords import CoordinateSystem, Transformation
from core.types._shared import apply_link_filters

if TYPE_CHECKING:
    # Only for the lazy annotation on the file-link fields below; importing it at runtime
    # would be a cycle, since `core.types.file_link` references this module in return.
    from core.types.file_link import FileLink

    # Same reason: `core.types.folder` imports this module for the layer types.
    from core.types.folder import Folder


@kante.django_type(
    models.Column,
    description="One declared column of a table dataset: its name, dtype and role. A COORDINATE column is also an axis of the table's space",
)
class Column:
    """One declared column of a table dataset."""

    id: auto
    order: int
    name: str
    dtype: str
    role: enums.ColumnRole
    axis_type: enums.AxisType | None
    # The kanne Unit scalar, exactly as `Axis.unit` is: for a coordinate column the two are
    # the same fact, and it would be odd for the SDL to call one `Unit` and the other a
    # string. Non-null on an ATTRIBUTE too -- what the measurement is in.
    unit: kanne_scalars.Unit | None
    long_name: str | None
    description: str | None
    table: "TableDataset" = kante.django_field(
        description="The table this column is declared on. Trivial when the column was read through its table, load-bearing when it was not -- an options list is flat, and a column with no table on it cannot be named in a `colorBy`"
    )
    references: Optional["TableDataset"] = kante.django_field(
        description="The table whose rows this column's values identify -- a declared foreign key, e.g. an `instance_id` column referencing a table of tracks. The target is keyed by its single INDEX coordinate column; look a value up there. Null for a column that identifies nothing"
    )
    node_references: Optional[Annotated["NetworkCollection", strawberry.lazy("core.types.coords")]] = kante.django_field(
        description=(
            "The network collection whose NODE ids this INDEX coordinate column's values are, scoped by the sibling INDEX axis keyed by the same collection's object ids. One such "
            "column makes the table per-node, two make it per-edge -- (source, target), in axis declaration order -- which is what a client resolving a per-node picker entry reads "
            "the key columns from. Null for every column of every other table"
        )
    )


@kante.django_type(
    models.TableDataset,
    filters=filters.TableDatasetFilter,
    ordering=order.TableDatasetOrder,
    pagination=True,
    description="A parquet-backed table whose rows are scientific records (segmented objects, localizations, cells). It owns a coordinate system whose axes are its coordinate columns, which is what makes a localization table placeable; a table with no coordinate columns enumerates its rows and its lineage edge is UNMAPPABLE. Its store, its columns and that coordinate system are fixed at creation -- only `name` and `description` can be updated, and a recomputation is a new table rather than an edit of this one. Read the rows directly from the Parquet store with a datalayer access grant rather than paginating through GraphQL",
)
class TableDataset:
    """A parquet-backed table dataset."""

    folder: Optional[Annotated["Folder", strawberry.lazy("core.types.folder")]] = kante.django_field(
        description="The folder this table dataset is filed in. Organisational only: it says where a user keeps this table, never where its rows sit in space -- that is `coordinateSystem` and the edges out of it"
    )

    @kante.django_field(
        description=(
            "The files this table dataset was converted from -- the CZI a converter read to write these arrays, named per series. **Read this alongside `derivedFrom`, not instead of "
            "it**: `derivedFrom` says which *data* this was computed from and relates two coordinate systems, while this says which *bytes* it was read out of and relates to no "
            "space at all, because a file has none. Both can be non-empty and complete"
        ),
        prefetch_related=["file_links__file"],
    )
    def source_files(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file this table dataset was produced from."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.SOURCE), filters, info)

    @kante.django_field(
        description="The files written out of this table dataset: an OME-TIFF export, a rendered snapshot registered as a file. The mirror of `sourceFiles`",
        prefetch_related=["file_links__file"],
    )
    def exports(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file written out of this table dataset."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.RENDITION), filters, info)

    id: auto
    name: auto
    description: str | None
    provenance_entries: List["ProvenanceEntry"] = kante.django_field(
        description="Every change made to this table: who created it, and every subsequent rename or redescription, attributed to the client, user and task it happened under. Only `name` and `description` can change -- the store, the columns and the coordinate system derived from them are fixed at creation"
    )
    store: ParquetStore = kante.django_field(description="The Parquet store holding the rows. Request an access grant from it and read the Parquet directly")
    columns: List[Column] = kante.django_field(description="The declared column schema, in order. The COORDINATE columns are the axes of this table's coordinate system")
    coordinate_system: CoordinateSystem = kante.django_field(description="The coordinate system this table owns. Its axes are the table's coordinate columns (or a single INDEX axis for a pure measurement table)")
    created_through: Task | None = kante.django_field(description="The task this table was created through, if any")
    created_through_by: User | None = kante.django_field(description="The assigner of the creating task, if any")
    referenced_by: List[Column] = kante.django_field(
        description="Every column, in any table, that declares this table as its reference target -- the reverse of `Column.references`. This table cannot be deleted while any of them exist"
    )

    @kante.django_field(
        description="Every edge from this table's space back into data it was computed from, in declared order -- the first is the primary parent, the one that places it. UNMAPPABLE where the lineage is recorded but no geometry is claimed; empty for a freestanding table. The same relation a derived dataset's `derivedFrom` records"
    )
    def derived_from(self, info: Info) -> List[Transformation]:
        """The edges relating this table's space to the data it came from."""
        system = getattr(self, "coordinate_system", None)
        return graph_logic.collection_derivation_edges(system) if system else []

    @kante.django_field(description="The table's axis names, in order. Derived from the coordinate columns")
    def axis_names(self, info: Info) -> List[str]:
        """The table's axis names."""
        return self.axis_names

    @kante.django_field(description="How this table was produced: the run, its parameters and its inputs")
    def provenance_metadata(self, info: Info) -> scalars.Any:
        """How this table was produced."""
        return self.provenance_metadata
