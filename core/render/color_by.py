"""Coloring objects by a column of the table their source keys into.

One model, because it is one fact. A label layer and a mesh layer both render *objects*
carrying ids, both reach a table of per-object rows through the same ``FIELD`` edge
(``createTableDataset(keyedBy:)``), and both then pick one of its columns to color by.
The layers differ in everything else -- a mask has a hash, a background id, contours; a
collection has a material, a wireframe and a shading model -- but not in what one of these
says, so storing that twice would be two copies of one truth free to drift.

Both kinds now publish an ordered *picker* of these rather than one colouring, which is why
the caption sits on :class:`PickerColorByModel` between the base and the two named
subclasses: an author publishes the readings worth switching between and the person at the
screen chooses among them, and that is as true of a mask's objects as of a collection's.

The GraphQL surface keeps two *names* (``LabelColorBy`` and ``MeshColorBy``), because a
type named for label layers reads wrong on a mesh and the descriptions differ. What they
share is this model, and one validator at the mutation boundary
(``core.mutations.layer._build_color_by``), which is where the two claims that cannot be
checked from the input alone are checked: that the table really is reachable by a FIELD
edge, and that the column exists on it.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from core import enums
from core.render.joins import JoinStepModel


class AxisPositionModel(BaseModel):
    """One position along one named axis: which slice of a matrix a colouring reads.

    The same pair `CoordinateAnchorInput` carries, and deliberately the same shape: naming a
    position along an axis is one idea, whether it is picking a channel of an image or a
    feature of an expression matrix. Note this *names* a position rather than enumerating them
    -- a matrix with 19 059 features has 19 059 positions and one of them is stored here.
    """

    axis: str
    value: int


class ColorByModel(BaseModel):
    """Color objects by a column of the table their source's ``FIELD`` edge keys into.

    Each table is named, never its key: which column of a table holds row identity is
    already declared there (its single INDEX coordinate column), and the edge that makes
    the first lookup possible is already in the coordinate graph. A second per-layer copy
    of either could disagree with the fact it copies.

    ``join_path`` is how a column further than one table away is reached -- a chain of
    ``references`` hops, empty for the common case. See :mod:`core.render.joins`.

    ``colormap`` is the one way a column becomes colour, and *which sort* of colormap applies
    follows from the column's declared role rather than from a choice here: a measure column
    takes a continuous one over its range, a categorical one (an id, a class label) takes a
    qualitative one over its distinct values. Naming the wrong sort is refused at the boundary.

    There used to be a second way -- ``class_colors``, an explicit value-to-RGBA map -- on the
    grounds that "a colormap would impose an order the values do not have". True of every
    colormap this enum then held, and not true of colormaps as such: a qualitative palette is
    exactly a colormap that imposes no order. So the map is gone and
    :data:`~core.enums.QUALITATIVE_COLORMAPS` is what replaced it. Nothing is lost that was
    real: the only caller ever to send one generated it as an evenly spaced hue per class,
    which is what a qualitative colormap *is*, and a colour that genuinely belongs to a class
    rather than to a layer's display state belongs in a ``COLOR`` column of the table, where it
    is a per-row fact instead of a copy on every picker entry.

    ``min`` and ``max`` window the colormap: the value mapped to its bottom and the value
    mapped to its top, so the map's whole width spends itself on the range that matters
    instead of being stretched flat by one outlier. They belong to the colormap half only --
    a categorical column has no order to window -- and an omitted end leaves the viewer to
    stretch the map over the values it actually reads.
    """

    # **Which sort of source the value is read from.** Two shapes, one relation: a column of a
    # table the ids key into, or a slice of a sparse matrix indexed by the same ids. Flat with a
    # discriminator rather than a union of two stored models, and rather than an interface on
    # the GraphQL side -- the same compromise `KeyedByInput` makes and for the same reason: the
    # alternative is a type explosion and a fragment in every client query that reads a picker.
    #
    # Defaulted, and that default is what makes this migration-free. Every entry stored before
    # this field existed is a column colouring, so `ColorByModel(**entry)` fills it in and an
    # old dump rehydrates unchanged -- exactly the trick `join_path` uses one field below.
    #
    # GRAPH is the third shape, network layers only: a per-node value the collection itself
    # carries -- Strahler order, degree, a stored radius, a writer's own column -- named by
    # `attribute` and validated against the collection's manifest rather than against a table.
    kind: Literal["COLUMN", "SPARSE", "GRAPH"] = "COLUMN"

    # Where the value is read, for `kind="column"`. With an empty `join_path` this is the table
    # the FIELD edge landed on -- what every colouring written before join paths existed means,
    # and still the common case. With a path, it is the table the last hop points at.
    #
    # Optional only because the sparse variant reads from a dataset instead; a column colouring
    # without them is refused by `_one_source` below, so "optional" never means "absent".
    table: str | None = None
    column: str | None = None

    # Where the value is read, for `kind="sparse"`. The dataset, and the position along the
    # axis it does *not* share with the ids -- one slice of the matrix, which is a value per
    # object and therefore a colouring. A list rather than a single position so a matrix over
    # more than two axes needs no new shape here, only a longer one.
    dataset: str | None = None
    at: list[AxisPositionModel] = Field(default_factory=list)

    # Where the value is read, for `kind="GRAPH"`: an attribute the network collection's own
    # manifest declares (or `radius`, when its encoding carries one). Per node, not per object,
    # and `target` says which of the network's two row sets it paints -- an edge derives its
    # value from its start node, since simplification re-links edges between levels and an
    # edge therefore owns no durable value of its own.
    #
    # `target` is ALSO legal on a COLUMN entry, where it is the validator's stamp rather than
    # the caller's choice: a table whose axes are node-identified colours per node
    # (`target: NODE`) or per edge (`target: EDGE`), and the boundary writes which from the
    # table's own shape. None on a COLUMN entry is the object-level colouring every entry was
    # before node tables existed, so old dumps rehydrate unchanged. A SPARSE entry stays
    # targetless: a matrix slice is a value per object.
    attribute: str | None = None
    target: Literal["NODE", "EDGE"] | None = None
    # Empty is the direct case, so a stored dump written before this field existed rehydrates
    # as one: `ColorByModel(**entry)` fills the default, which is why this needs no migration.
    join_path: list[JoinStepModel] = Field(default_factory=list)
    colormap: enums.ColorMap | None = None
    # The colormap's window, inclusive on both ends and in the column's own declared `unit` --
    # bare numbers for the same reason a filter's bounds are (see core.render.filter_by): the
    # column is the one place that says what "500" means, and a quantity here would be a second
    # statement of it, free to drift. Stored dumps written before these existed rehydrate to
    # None, which is exactly what they meant: stretch over what you see.
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "ColorByModel":
        """Exactly the fields the discriminator selects, and none of the other variant's.

        Stored rather than only checked at the boundary, because this model is what
        `updateLabelLayer` rehydrates every dump through: a row that carried both would look
        valid and render whichever the reader happened to check first.
        """
        if self.kind != "GRAPH" and self.attribute is not None:
            raise ValueError(
                "`attribute` belongs to a GRAPH colouring -- a per-node value the network collection itself carries. Set `kind: GRAPH` to use it"
            )
        if self.kind == "SPARSE" and self.target is not None:
            raise ValueError(
                "`target` belongs to a GRAPH colouring or a COLUMN one over a node/edge table -- a sparse slice is a value per object, so there is no row set to aim it at"
            )

        if self.kind == "COLUMN":
            missing = [name for name in ("table", "column") if getattr(self, name) is None]
            if missing:
                raise ValueError(f"a column colouring reads a column of a table, so it requires {missing}")
            if self.dataset is not None or self.at:
                raise ValueError("a column colouring does not read `dataset` or `at`; those name a slice of a sparse matrix. Set `kind: SPARSE` to use them")
            return self

        if self.kind == "GRAPH":
            if self.attribute is None:
                raise ValueError("a graph colouring reads a per-node value the collection carries, so it requires `attribute`")
            if self.table is not None or self.column is not None or self.join_path:
                raise ValueError("a graph colouring does not read `table`, `column` or `joinPath`; those name a column of a table. Set `kind: COLUMN` to use them")
            if self.dataset is not None or self.at:
                raise ValueError("a graph colouring does not read `dataset` or `at`; those name a slice of a sparse matrix. Set `kind: SPARSE` to use them")
            if self.target is None:
                # NODE is what an omitted target has always meant -- there was nothing else to
                # target before edges could be addressed -- so old dumps rehydrate unchanged.
                self.target = "NODE"
            return self

        if self.dataset is None or not self.at:
            raise ValueError("a sparse colouring reads one slice of a matrix, so it requires `dataset` and the position `at`")
        if self.table is not None or self.column is not None or self.join_path:
            raise ValueError("a sparse colouring does not read `table`, `column` or `joinPath`; those name a column of a table. Set `kind: COLUMN` to use them")
        return self

    @model_validator(mode="after")
    def _positions_are_a_set(self) -> "ColorByModel":
        """`at` names a slice, and a slice is not ordered.

        `{gene: 7, adduct: 2}` and `{adduct: 2, gene: 7}` are one position, so they are stored
        as one -- sorted by axis. Without this the picker's duplicate check keys them apart and
        stores two entries that render identically, which is the very thing that check exists to
        prevent. `join_path` is deliberately *not* canonicalised alongside it: a chain of hops is
        ordered, and reordering one would change where it lands.
        """
        self.at = sorted(self.at, key=lambda position: position.axis)
        return self


class PickerColorByModel(ColorByModel):
    """One entry of a layer's colour picker: a joined column, and what to call it in the UI.

    The caption is the only thing a picker entry adds to a colouring, and it belongs to the
    picker rather than to the colouring: it is what a menu row says, not part of what gets
    drawn. Which is exactly why it is *not* what distinguishes two entries -- the same
    (table, column, colormap, window) twice under two names is refused at the
    boundary, because a picker whose two rows render identically is a bug wearing two labels.

    Shared by both layer kinds. A mask is one map and a collection is a set of surfaces, but
    "these are the readings worth switching between, and this one is showing now" is the same
    statement over both, and the two subclasses below exist only so the GraphQL surface can
    name it twice.
    """

    label: str | None = None


class MeshColorByModel(PickerColorByModel):
    """One entry of a mesh layer's colour picker, named for the GraphQL type it backs."""


class LabelColorByModel(PickerColorByModel):
    """One entry of a label layer's colour picker, named for the GraphQL type it backs."""


class NetworkColorByModel(PickerColorByModel):
    """One entry of a network layer's colour picker, named for the GraphQL type it backs.

    The one picker whose entries may be GRAPH-kind -- the other two subclasses never store an
    `attribute`, because a mask and a mesh have no per-node values to read.
    """
