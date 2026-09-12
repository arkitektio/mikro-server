"""GraphQL types for the sparse matrix dataset, its stored layouts and its identified axes."""

from typing import TYPE_CHECKING, Annotated, List, Optional

import strawberry
from strawberry import auto

import kante
from kante.types import Info

from datalayer.types import SparseStore

from core import filters, models, order, scalars
from core.logic import file_link as file_link_logic
from core.types.auth import ProvenanceEntry, Task, User
from core.types.coords import CoordinateSystem, Transformation
from core.types._shared import apply_link_filters
from core.logic import graph as graph_logic

if TYPE_CHECKING:
    from core.types.file_link import FileLink
    from core.types.folder import Folder
    from core.types.table_dataset import TableDataset


@kante.django_type(
    models.SparseArray,
    description=(
        "One stored layout of a sparse matrix: a store, and which axis its `indptr` indexes. The `DataArray` of this world and deliberately thinner -- two layouts are the same space "
        "holding the same values in a different order, so unlike a pyramid level there is no coordinate system and no edge, because there is nothing spatial to state"
    ),
)
class SparseArray:
    """One stored layout of a sparse matrix."""

    id: auto
    store: SparseStore = kante.django_field(description="The store holding this layout. Both layouts of one matrix share it -- one matrix is one upload -- so `path` is what says which of them this is. Ask the store for an access grant and read the three arrays directly")
    path: str = kante.django_field(
        description="Where this layout sits inside the store's prefix, e.g. `layouts/csr_matrix`. Open the group at this path, not at the store root"
    )
    indexed_axis: int = kante.django_field(
        description=(
            "Which axis of the dataset this layout's `indptr` indexes, as a position in the declared axis order. Selecting one position along it is a single contiguous read; selecting "
            "along the other axis is a scan of everything, which is why a dataset that must answer both questions holds two of these"
        )
    )

    @kante.django_field(description="The name of the axis this layout indexes, from the dataset's declared order")
    def indexed_axis_name(self, info: Info) -> str | None:
        """The name of the indexed axis."""
        del info
        names = self.dataset.axis_names
        return names[self.indexed_axis] if 0 <= self.indexed_axis < len(names) else None


@kante.django_type(
    models.SparseAxisReference,
    description=(
        "An axis whose positions are rows of a table. The sparse counterpart of `Column.references` -- the same statement said of an axis, because a matrix has no columns to hang "
        "it on -- and what lets a FIELD edge land beside it: a mask supplies one id, so the other axis has to be accounted for by its own identification"
    ),
)
class SparseAxisReference:
    """An axis identified by a table."""

    id: auto
    axis: str = kante.django_field(description="The name of the identified axis")
    references: Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")] = kante.django_field(
        description="The table whose rows this axis' positions are. Keyed by its single INDEX coordinate column, which is where a position is looked up"
    )


@kante.django_type(
    models.SparseDataset,
    filters=filters.SparseDatasetFilter,
    ordering=order.SparseDatasetOrder,
    pagination=True,
    description=(
        "A sparse matrix over two enumerated axes -- objects on one, features on the other -- stored as anndata-spelled zarr groups. It exists because a colouring names one *column*, "
        "so a colourable measurement is a column of a table: right for a few hundred features and impossible for a transcriptome, where a feature stops being a schema fact and becomes "
        "a data one. **Each axis is identified exactly once**, by its own `identifiedBy` -- a source whose contents are the ids, or the table whose rows the positions are. Its "
        "stores, axes and coordinate system are fixed at creation; a recomputation is a new dataset"
    ),
)
class SparseDataset:
    """A sparse matrix dataset."""

    id: auto
    name: auto
    description: str | None
    folder: Optional[Annotated["Folder", strawberry.lazy("core.types.folder")]] = kante.django_field(description="The folder it is filed in. Organisational only")
    coordinate_system: CoordinateSystem = kante.django_field(description="The coordinate system whose axes are this matrix's two enumerations. Owned by the dataset, and the space a FIELD edge lands in")
    arrays: List[SparseArray] = kante.django_field(description="The stored layouts, one per axis a store's `indptr` indexes. One is legal and offers one capability")
    axis_references: List[SparseAxisReference] = kante.django_field(description="The axes identified by a table rather than by a keying source")
    provenance_entries: List[ProvenanceEntry] = kante.django_field(description="The recorded history of this dataset. Only `name` and `description` can change")
    created_through: Task | None = kante.django_field(description="The task this dataset was created through, if any")
    created_through_by: User | None = kante.django_field(description="Who assigned that task")

    @kante.django_field(description="The matrix's axis names, in the order its stores' `shape` is written")
    def axis_names(self, info: Info) -> List[str]:
        """The matrix's axis names."""
        del info
        return self.axis_names

    @kante.django_field(description="The shape of the matrix, read off its stores rather than declared. Every layout of one dataset holds the same shape")
    def shape(self, info: Info) -> List[int]:
        """The matrix's shape."""
        del info
        return self.shape

    @kante.django_field(
        description=(
            "The axes this dataset can select a single position along in one contiguous read -- one per stored layout. An axis absent here is one it holds, but can only answer about "
            "by scanning every byte, so a surface needing that answer will not offer this dataset"
        )
    )
    def indexable_axes(self, info: Info) -> List[str]:
        """The axes a stored layout indexes."""
        del info
        names = self.axis_names
        return [names[array.indexed_axis] for array in self.arrays.all() if 0 <= array.indexed_axis < len(names)]

    @kante.django_field(description="Every edge from this matrix's space back into the data it was computed from, in declared order")
    def derived_from(self, info: Info) -> List[Transformation]:
        """The edges relating this matrix's space to what it came from."""
        del info
        system = getattr(self, "coordinate_system", None)
        return graph_logic.collection_derivation_edges(system) if system else []

    @kante.django_field(description="The files this dataset was converted from", prefetch_related=["file_links__file"])
    def source_files(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file this dataset was produced from."""
        import core.enums as enums

        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.SOURCE), filters, info)

    @kante.django_field(description="How this matrix was produced: the run, its parameters and its inputs")
    def provenance_metadata(self, info: Info) -> scalars.Any:
        """How this matrix was produced."""
        del info
        return self.provenance_metadata
