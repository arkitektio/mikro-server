"""The parquet-backed tabular dataset, sibling of :class:`~core.models.ArrayDataset`.

A ``TableDataset`` is what the coordinate graph had no first-class home for: a
table whose rows are scientific records -- one row per segmented object with its
measurements, one row per single-molecule localization with its coordinates, one
row per cell with its marker levels. It parallels ``ArrayDataset`` (its geometry derived
from an owned coordinate system, provenance through a task, and neither of them
editable once made) but is backed by a single Parquet store rather than a Zarr
pyramid, and has no multiscale.

Its columns are declared -- name, dtype, and a role. The *coordinate* columns are
special: they become the axes of the table's own coordinate system, which is what
makes a localization table placeable in a scene (its rows are points in a real
space). A table with no coordinate columns degenerates to a single INDEX axis: its
rows enumerate objects, nothing maps a pixel to a row, and the edge to the image it
came from is UNMAPPABLE. That degenerate case is exactly the old FeatureCollection.
"""

from typing import TYPE_CHECKING

from django.db import models
from django.contrib.auth import get_user_model

from authentikate.models import Organization
from datalayer.models import ParquetStore
from koherent.fields import ProvenanceField
from django_choices_field import TextChoicesField

from core import enums

if TYPE_CHECKING:
    from core.models.coords import CoordinateSystem


class TableDataset(models.Model):
    """A parquet-backed table whose rows are scientific records, placed by its coordinate columns.

    **Not editable.** The store, the declared columns and the coordinate system they derive
    are written once, by ``create_table_dataset``, and by nothing else: no mutation swaps the
    Parquet, adds a column, or touches an axis. ``updateTableDataset`` reaches the name and
    the description and stops there, and a table's own system is refused by
    ``updateCoordinateSystem``, which serves shared spaces only. A recomputation is a *new table*, not
    an edit of this one.

    The absence of a ``version`` field is not permission to edit in place -- it is the one
    axis on which this differs from a :class:`MeshCollection`, which versions on purpose.
    This one simply is what it was created as.

    Its axes and units are not stored here at all: they live on the owned coordinate system,
    derived from the declared coordinate columns, so there is no second copy that can
    disagree.
    """

    name = models.CharField(max_length=1000, help_text="The name of this table dataset")
    description = models.CharField(max_length=1000, null=True, blank=True, help_text="The description of this table dataset")

    # Filing, not placement -- see the note on `ArrayDataset.folder`.
    folder = models.ForeignKey(
        "Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="table_datasets",
        help_text="The folder this table dataset is filed in. Organisational only -- it says nothing about where the rows sit in space",
    )

    store = models.ForeignKey(
        ParquetStore,
        on_delete=models.CASCADE,
        related_name="table_datasets",
        help_text="The Parquet store holding the rows. The client reads it directly with a datalayer access grant",
    )
    provenance_metadata = models.JSONField(default=dict, help_text="How this table was produced (the run, its parameters and its inputs)")

    coordinate_system = models.ForeignKey(
        "CoordinateSystem",
        on_delete=models.PROTECT,
        # Nullable in the database only because the `historical*` twin carries rows written
        # before this column existed, and a history row must be allowed to say "not
        # recorded". Every write path sets it, so a live row never has none.
        null=True,
        blank=True,
        related_name="table_datasets",
        help_text="The coordinate system this table's coordinate columns are expressed in. Its axes are derived from those columns, so there is no second copy that can disagree",
    )

    created_at = models.DateTimeField(auto_now_add=True, help_text="The time this table dataset was created")
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, help_text="The user that created this table dataset")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization this table dataset belongs to")
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
        """Meta options for the table dataset."""

        ordering = ["-created_at"]

    def __str__(self) -> str:
        """The table dataset's name."""
        return self.name

    @property
    def coordinate_system_or_none(self) -> "CoordinateSystem | None":
        """The coordinate system this table owns, or None before it is created."""
        # The reverse of CoordinateSystem.table_dataset, which raises rather than
        # returning None when the system has not been created yet.
        return getattr(self, "coordinate_system", None)

    @property
    def axes(self) -> list:
        """The table's axes (its coordinate columns, materialized as Axis rows), in order."""
        system = self.coordinate_system_or_none
        return list(system.axes.all()) if system else []

    @property
    def axis_names(self) -> list:
        """The table's axis names, in order. Derived from the owned system's axes."""
        return [axis.name for axis in self.axes]

    def columns_by_role(self, role: str) -> list["Column"]:
        """The declared columns of a given role, in declared order."""
        return list(self.columns.filter(role=role).order_by("order"))


class Column(models.Model):
    """One declared column of a :class:`TableDataset`: its name, dtype, and role.

    The role is load-bearing. A ``COORDINATE`` column carries an axis type and
    becomes an axis of the table's coordinate system; every other role is data a
    layer may read but which never places the row. Name, type and unit on a
    coordinate column are the same fact as the derived ``Axis`` -- they are written
    from here, once, in the same transaction, never edited into disagreement.

    The unit is not the coordinate's alone: an ``ATTRIBUTE`` carries one too, because
    an area in square micrometres is as much a quantity as a position in nanometres,
    and the client plotting it needs the unit as much as the number. The roles that
    are not measured -- an id, a track id, a label, a colour -- refuse it.
    """

    table = models.ForeignKey(TableDataset, on_delete=models.CASCADE, related_name="columns", help_text="The table dataset this column belongs to")
    # Relations between tables are schema facts, not coordinate facts. A FIELD edge is the
    # one crossing from geometry into record-land (it consumes spatial axes); once inside,
    # "this column's values identify rows of that table" does no coordinate work -- no walk
    # can use it, no metric applies -- so it lives here, on the schema, as the foreign key it
    # is. The target is the *table*, never one of its columns: which column holds the target's
    # row identity is already declared there (its single INDEX coordinate column), and an FK
    # to that column would restate a derivable fact -- the two-copies-of-one-truth pattern
    # this codebase kills wherever it appears. PROTECT for the same reason a warp field is
    # PROTECTed: deleting a table out from under a column keying it would orphan the meaning
    # of every value in that column.
    references = models.ForeignKey(
        TableDataset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="referenced_by",
        help_text="The table whose rows this column's values identify. Its declared schema says which column carries that identity (its single INDEX coordinate column); this FK states only *which table*, and the rest is derived. A data column may reference; so may an INDEX **coordinate** column, which is the product-space case -- its values are already ids, so naming the table it enumerates is what the enumeration is *of*. A SPACE or TIME coordinate may not: a position in nanometres and a row id are different things",
    )
    # `references`' graph twin, and the second no-edge identification storage. A node id is
    # unique only within its traced object, so a column carrying one is meaningful exactly
    # when a sibling INDEX axis is keyed by the same collection's object ids -- the create
    # path refuses the column without it. Counted by `identified_axes` and
    # `product_space_tables` alongside `references`, which is what keeps such a table out of
    # every object-level picker walk (an object id alone cannot address its rows) and out of
    # `write_key_edges`' produced set (so the object axis' edge stays the one id supplied).
    node_references = models.ForeignKey(
        "NetworkCollection",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="node_referenced_by",
        help_text="The network collection whose NODE ids this INDEX coordinate column's values are, scoped by a sibling INDEX axis keyed by the same collection's object ids (a node id is unique only within its traced object, so the pair is the row's key). One such column makes the table per-node; two over one collection are its edges' (source, target), in axis declaration order. PROTECT for the reason `references` is: deleting the collection would orphan the meaning of every value in the column",
    )
    order = models.PositiveSmallIntegerField(help_text="The column's position in the declared schema, which is the file's order -- the two are checked against each other at creation. Deliberately not the axis order: the axes are a sequence the caller states in `axes`, and a coordinate column's position in the file has nothing to do with its position in the space")
    name = models.CharField(max_length=255, help_text="The column name, matching the Parquet column")
    dtype = models.CharField(max_length=64, help_text="The column's data type, as a DuckDB type string, e.g. 'DOUBLE', 'BIGINT'")
    role = TextChoicesField(choices_enum=enums.ColumnRoleChoices, default=enums.ColumnRoleChoices.ATTRIBUTE, help_text="What the column is for: a coordinate that places the row, or data hanging off it")
    axis_type = TextChoicesField(choices_enum=enums.AxisTypeChoices, null=True, blank=True, help_text="(coordinate) The semantic axis type this column samples, SPACE or TIME")
    unit = models.CharField(max_length=64, null=True, blank=True, help_text="The unit the column's values are in, e.g. 'nanometer' for a coordinate or 'micrometer**2' for a measured area. A pint unit, validated at the API boundary. Null for pixel-index coordinates and for anything not measured (an id, a label, a colour, which refuse one)")
    long_name = models.CharField(max_length=255, null=True, blank=True, help_text="A human-readable name for the column")
    description = models.CharField(max_length=1000, null=True, blank=True, help_text="A free-form description of what the column holds, e.g. 'mean GFP intensity within the segmented object'")

    class Meta:
        """Meta options for the table column."""

        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["table", "name"], name="unique_table_column_name"),
            models.UniqueConstraint(fields=["table", "order"], name="unique_table_column_order"),
        ]

    def __str__(self) -> str:
        """The column's name and role."""
        return f"{self.name} ({self.role})"
