"""The sparse matrix dataset: a measurement over two enumerations, mostly zeros.

A ``SparseDataset`` is the third thing the coordinate graph holds data in, beside
:class:`~core.models.ArrayDataset` (a dense grid) and :class:`~core.models.TableDataset` (rows
of records). It is a matrix over two *enumerated* axes -- objects on one, features on the
other -- and at any real size it is mostly zeros: a Visium HD run at 2 um is 0.12 % dense, so
the same facts are ~1 GB stored sparse against 43.8 GB as a dense table of even its top 2 000
features.

**It exists because a column does not scale.** A colouring names one column, so a colourable
measurement is a column of a table -- right for a 377-feature panel, and impossible for a
transcriptome, which would need 19 059 column declarations to state facts the matrix already
holds. Past some size a feature stops being a *schema* fact and becomes a *data* fact, and
this is the shape that says so.

**Nothing here is specific to transcriptomics.** The same shape is metabolites x cells,
proteins x pixels, peaks x cells, or a connectome. The server names no domain concept; the
caller names its axes, and ``Axis.name`` is free-form.

Both axes are ``INDEX`` -- "an enumeration with no metric" -- and **each is identified by one
of the two relations the graph already has**, which is the whole of the model:

* the objects, by an ``identifiedBy`` naming a mask or a mesh collection, whose own contents
  *are* the ids. Authors a FIELD edge.
* the features, by an ``identifiedBy`` naming a table, stored as a :class:`SparseAxisReference`.
  A table is already in record-land, where the relation is a foreign key rather than an edge.

That split is not invented here -- it is the one ``KeyedBySourceKind`` states -- and it is why
a connectome (both axes keyed off one mask) is the same object as an expression matrix.

**Two layouts, two stores, one dataset.** Which axis a store's ``indptr`` indexes decides
which question it answers in one contiguous read; ask the other and there is no range to read
at all, only a scan of everything. Measured at 16 um: one object is 2.2 ms from the
object-major store and 1 777 ms from the feature-major one. A dataset that must answer both
holds two stores, discriminated by :class:`SparseArray` exactly as a pyramid's levels are
discriminated by ``DataArray.level`` -- and, unlike a level, with no coordinate system and no
edge of its own, because the two layouts are one space with one set of values and an edge is a
spatial claim.

**Not editable.** The stores, the axes, the references and the coordinate system are written
once and by nothing else; ``updateSparseDataset`` reaches the name and the description and
stops. A recomputation is a *new dataset*, not an edit of this one -- the same sentence
``TableDataset`` and ``ArrayDataset`` both carry, and the axis on which none of the three
resembles a ``MeshCollection``, which versions on purpose.
"""

from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.db import models

from authentikate.models import Organization
from datalayer.models import SparseStore
from koherent.fields import ProvenanceField

if TYPE_CHECKING:
    from core.models.coords import CoordinateSystem


class SparseDataset(models.Model):
    """A sparse matrix over two enumerated axes, stored in one or both layouts."""

    name = models.CharField(max_length=1000, help_text="The name of this sparse dataset")
    description = models.CharField(max_length=1000, null=True, blank=True, help_text="The description of this sparse dataset")

    folder = models.ForeignKey(
        "Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sparse_datasets",
        help_text="The folder this sparse dataset is filed in. Organisational only -- it says nothing about what its axes are or where they point",
    )
    coordinate_system = models.ForeignKey(
        "CoordinateSystem",
        on_delete=models.PROTECT,
        # Nullable in the database only because the `historical*` twin carries rows written
        # before this column existed, and a history row must be allowed to say "not
        # recorded". Every write path sets it, so a live row never has none.
        null=True,
        blank=True,
        related_name="sparse_datasets",
        help_text="The coordinate system whose axes are this matrix's two enumerations. Owned by the dataset, and the space a FIELD edge lands in",
    )
    provenance_metadata = models.JSONField(default=dict, help_text="How this matrix was produced (the run, its parameters and its inputs)")

    created_at = models.DateTimeField(auto_now_add=True, help_text="The time this sparse dataset was created")
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, help_text="The user that created this sparse dataset")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization this sparse dataset belongs to")
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this object was created through, if any",
    )
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_%(class)ss",
        help_text="The assigner of the creating task, denormalized for fast filtering",
    )
    provenance = ProvenanceField()

    class Meta:
        """Meta options for the sparse dataset."""

        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Return the dataset's name."""
        return self.name

    @property
    def coordinate_system_or_none(self) -> "CoordinateSystem | None":
        """The coordinate system this matrix owns, or None before it is created."""
        return getattr(self, "coordinate_system", None)

    @property
    def axes(self) -> list:
        """The matrix's axes, in order."""
        system = self.coordinate_system_or_none
        return list(system.axes.all()) if system else []

    @property
    def axis_names(self) -> list[str]:
        """The matrix's axis names, in order. Derived from the owned system's axes."""
        return [axis.name for axis in self.axes]

    @property
    def store(self) -> "SparseStore | None":
        """The one store this matrix lives in. One matrix is one upload, so there is one."""
        array = next(iter(self.arrays.all()[:1]), None)
        return array.store if array else None

    @property
    def shape(self) -> list[int]:
        """The matrix's shape, read off its store rather than declared."""
        store = self.store
        return list(store.shape or []) if store else []

    def array_indexing(self, axis: str) -> "SparseArray | None":
        """The layout whose one contiguous read selects along ``axis``, if this dataset has it.

        The question every surface asks before offering itself: a colouring needs the layout
        indexing the *feature* axis, a per-object lookup the one indexing the *object* axis,
        and a dataset holding only one of them offers only one of those capabilities. Asking
        for the other is not slow, it is a scan of the whole store.

        Returns the *array*, not the store, because both layouts now live in one prefix: a
        reader needs the child path as well as the store to know what to open.
        """
        names = self.axis_names
        if axis not in names:
            return None
        wanted = names.index(axis)
        return next((array for array in self.arrays.all() if array.indexed_axis == wanted), None)


class SparseArray(models.Model):
    """One stored layout of a sparse dataset: a store, and which axis its ``indptr`` indexes.

    The :class:`~core.models.DataArray` of this world, and deliberately thinner. A pyramid
    level is a *different space* -- its own voxel grid, related to the intrinsic one by a
    stored edge whose parameters are derived from the shapes -- so it carries a coordinate
    system and an edge. Two sparse layouts are the *same* space holding the *same* values in a
    different order, so there is nothing spatial to state, and an edge saying so would be a
    stored fact carrying no information. That is the same reason ``DataArray.to_parent`` is
    null for level 0.

    What the row does carry is ``indexed_axis``, and that is not nothing: it decides which
    surface can use the layout, exactly as ``level`` decides which zoom a ``DataArray`` serves.

    **Both layouts live in one store**, because one matrix is one upload: the rows of a dataset
    share a ``store`` and differ by ``path``, which is the child group each one is. That is why
    the pair, not the store, is what a reader is given.
    """

    dataset = models.ForeignKey(SparseDataset, on_delete=models.CASCADE, related_name="arrays")
    store = models.ForeignKey(
        SparseStore,
        on_delete=models.CASCADE,
        related_name="sparse_arrays",
        help_text="The store holding this layout. Its spec, shape and per-layout chunking were read from the artifact when its upload was finished. Both layouts of one matrix share it",
    )
    path = models.CharField(
        max_length=255,
        help_text=(
            "Where this layout sits inside the store's prefix, e.g. `layouts/csr_matrix`. A reader opens the group at this path, not the store root. Taken from the store's own "
            "block at creation -- the writer states which layouts it finished, and a name with nothing behind it was refused before the store was ever registerable"
        ),
    )
    indexed_axis = models.PositiveSmallIntegerField(
        help_text=(
            "Which axis of the dataset this layout makes contiguous, as a position in the declared axis order. Read from the store's own block at creation and never supplied by a "
            "caller: it is what the layout *is*, and a second statement of it could disagree with the bytes. Two axes is one case -- an array of rank n has up to n layouts, one "
            "per axis a reader might select along"
        )
    )

    class Meta:
        """Meta options for the sparse array."""

        # Two layouts indexing the same axis are two copies of one capability, and nothing
        # could say which of them a reader should use. The pair is the discriminator, exactly as
        # (dataset, level) is for a pyramid.
        constraints = [
            models.UniqueConstraint(fields=["dataset", "indexed_axis"], name="one_sparse_array_per_indexed_axis"),
        ]
        ordering = ["indexed_axis"]

    def __str__(self) -> str:
        """Return a readable description of the layout."""
        return f"{self.dataset.name} indexed on axis {self.indexed_axis}"


class SparseAxisReference(models.Model):
    """An axis whose positions are rows of a table -- what identifies it.

    The sparse counterpart of ``Column.references``, and the same relation said of an axis
    rather than of a column: a matrix has no columns to hang it on, but the statement is
    identical -- *the values along this axis identify rows of that table*.

    It is what lets a FIELD edge land here at all. A mask supplies one id, so it can account
    for one axis; the other has to be accounted for by its own identification or
    ``assert_edge_rank`` refuses the edge. See ``core.logic.graph.identified_axes``, which reads
    this and its table counterpart through one definition.
    """

    dataset = models.ForeignKey(SparseDataset, on_delete=models.CASCADE, related_name="axis_references")
    axis = models.CharField(max_length=32, help_text="The name of the axis being identified. One of the dataset's own")
    references = models.ForeignKey(
        "TableDataset",
        on_delete=models.PROTECT,
        related_name="referenced_by_sparse_axes",
        help_text="The table whose rows this axis' positions are. Keyed by its single INDEX coordinate column, which is where a position is looked up -- the same contract `Column.references` carries",
    )

    class Meta:
        """Meta options for the sparse axis reference."""

        constraints = [
            models.UniqueConstraint(fields=["dataset", "axis"], name="one_reference_per_sparse_axis"),
        ]

    def __str__(self) -> str:
        """Return a readable description of the reference."""
        return f"{self.dataset.name}.{self.axis} -> {self.references.name}"
