"""Filtering objects by a column of the table their source keys into.

The sibling of :mod:`core.render.color_by`, over the same relation and answered from the same
place: a mask's pixels and a collection's objects both carry ids, a ``FIELD`` edge keys them to
a table of per-object rows, and a column of that table decides -- here whether an object is
*drawn* rather than what colour it takes. So the two share their boundary check (the table must be reachable, the column
must exist) and differ only in what they do with the value.

**Which rule shape applies follows from the column's declared role**, exactly as it does for
``colorBy``'s continuous-vs-qualitative colormap: a measure column (COORDINATE, ATTRIBUTE) has an order, so
it is bounded with ``min``/``max``; a categorical one (ID, LABEL, TRACK_ID, COLOR) has none, so
it is matched against an explicit set of ``values``. Naming both is refused at the boundary --
the table already settled which of the two a column is, and a second answer here could only
disagree with it.

The bounds are bare numbers, deliberately, and they are in the column's own declared ``unit``
(``Column.unit``). A quantity here would be a second statement of a unit the table already
carries, free to drift from it; the column is the one place that says what "500" means.

Nothing here is a spatial fact and nothing here is executed server-side: a filter says which
objects a viewer draws, and the viewer runs it against the parquet it already reads. The server
stores the rule and refuses one nothing could run.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from core.render.color_by import AxisPositionModel
from core.render.joins import JoinStepModel


class FilterByModel(BaseModel):
    """Draw only the objects whose row in the keyed table satisfies this rule.

    ``exclude`` inverts the whole rule rather than belonging to one half of it: "everything but
    the debris class" and "everything but the smallest objects" are the same operation over two
    different matches, and giving each half its own negation would be two ways to say one thing.
    """

    # Which sort of source the rule reads, the same discriminator and for the same reason
    # `ColorByModel` carries one: a set of ids reaches a number two ways, and a rule over
    # a matrix slice is as legitimate as one over a column.
    #
    # Defaulted, and that default is what makes this migration-free: every rule stored before
    # this field existed is a column rule, so `FilterByModel(**entry)` fills it in and an old
    # dump rehydrates unchanged -- the trick `ColorByModel.kind` and `join_path` already use.
    #
    # GRAPH is the third shape, network layers only, mirroring `ColorByModel.kind` exactly:
    # a rule over a per-node value the collection itself carries.
    kind: Literal["COLUMN", "SPARSE", "GRAPH"] = "COLUMN"

    # (COLUMN) where the value is read.
    table: str | None = None
    column: str | None = None
    # The same chain :class:`~core.render.color_by.ColorByModel` carries, for the same reason and
    # checked by the same walker: a rule over a column two tables away is as legitimate as a
    # colouring by one. Empty is the direct case. See :mod:`core.render.joins`.
    join_path: list[JoinStepModel] = Field(default_factory=list)

    # (SPARSE) the matrix, and the position along the axes it identifies itself by. One slice,
    # which is a value per object -- exactly what a rule needs to test.
    dataset: str | None = None
    at: list[AxisPositionModel] = Field(default_factory=list)

    # (GRAPH) the per-node value the network collection carries, and which row set the rule
    # hides -- a node's visibility takes its segments and glyphs with it, an EDGE rule hides
    # segments only. An edge inherits its start node's value, the renderer's own convention.
    # `target` is also the validator's stamp on a COLUMN rule over a node/edge table, exactly
    # as on `ColorByModel` -- None on COLUMN is the object-level rule it always was.
    attribute: str | None = None
    target: Literal["NODE", "EDGE"] | None = None

    # The measure half: a closed, half-open or (with one bound) open interval, inclusive on both
    # ends. Two optional bounds rather than an operator and a value, because a range is the shape
    # the question actually has -- "volume between 100 and 500" is one rule, and expressing it as
    # two predicates that must both hold invites a picker entry that is half a rule.
    min: float | None = None
    max: float | None = None

    # The categorical half: the values that match, as strings. Strings even for integer ids,
    # because a class is named the same way wherever it is named -- the same vocabulary a
    # qualitative colouring ranks its distinct values by.
    values: list[str] | None = None

    exclude: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> "FilterByModel":
        """The fields the discriminator selects, and none of the other variant's.

        The mirror of :meth:`core.render.color_by.ColorByModel._one_source`, and stored rather
        than only checked at the boundary for the same reason: this model is what an update
        rehydrates every dump through, and a row carrying both would look valid.
        """
        if self.kind != "GRAPH" and self.attribute is not None:
            raise ValueError(
                "`attribute` belongs to a GRAPH rule -- a per-node value the network collection itself carries. Set `kind: GRAPH` to use it"
            )
        if self.kind == "SPARSE" and self.target is not None:
            raise ValueError(
                "`target` belongs to a GRAPH rule or a COLUMN one over a node/edge table -- a sparse slice is a value per object, so there is no row set to aim it at"
            )

        if self.kind == "COLUMN":
            missing = [name for name in ("table", "column") if getattr(self, name) is None]
            if missing:
                raise ValueError(f"a column rule reads a column of a table, so it requires {missing}")
            if self.dataset is not None or self.at:
                raise ValueError("a column rule does not read `dataset` or `at`; those name a slice of a sparse matrix. Set `kind: SPARSE` to use them")
            return self

        if self.kind == "GRAPH":
            if self.attribute is None:
                raise ValueError("a graph rule tests a per-node value the collection carries, so it requires `attribute`")
            if self.table is not None or self.column is not None or self.join_path:
                raise ValueError("a graph rule does not read `table`, `column` or `joinPath`; those name a column of a table. Set `kind: COLUMN` to use them")
            if self.dataset is not None or self.at:
                raise ValueError("a graph rule does not read `dataset` or `at`; those name a slice of a sparse matrix. Set `kind: SPARSE` to use them")
            if self.values is not None:
                raise ValueError(
                    "a graph rule is measured -- Strahler order, degree, a radius are ordered values every one -- so it is bounded with `min`/`max`, never matched against a `values` set"
                )
            if self.target is None:
                self.target = "NODE"
            return self

        if self.dataset is None or not self.at:
            raise ValueError("a sparse rule tests one slice of a matrix, so it requires `dataset` and the position `at`")
        if self.table is not None or self.column is not None or self.join_path:
            raise ValueError("a sparse rule does not read `table`, `column` or `joinPath`; those name a column of a table. Set `kind: COLUMN` to use them")
        if self.values is not None:
            raise ValueError(
                "a sparse rule is measured -- a slice of a matrix is a value per object -- so it is bounded with `min`/`max`, never matched against a `values` set. "
                "Nothing stores categories sparsely, because the zeros would be a category too"
            )
        return self

    @model_validator(mode="after")
    def _positions_are_a_set(self) -> "FilterByModel":
        """`at` names a slice, and a slice is not ordered. See `ColorByModel`."""
        self.at = sorted(self.at, key=lambda position: position.axis)
        return self

    @model_validator(mode="after")
    def _one_kind_of_rule(self) -> "FilterByModel":
        bounded = self.min is not None or self.max is not None
        if bounded and self.values is not None:
            raise ValueError(
                "`filterBy` takes either bounds (`min`/`max`, for a measure column) or `values` (for a categorical one), never both: which applies follows from the column's declared role, not from a choice here"
            )
        if not bounded and self.values is None:
            raise ValueError("A `filterBy` naming neither a bound nor any values matches every row, which is not a filter -- give it a `min`, a `max`, or a `values` list")
        return self

    @model_validator(mode="after")
    def _bounds_are_a_range(self) -> "FilterByModel":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"`min` is {self.min} and `max` is {self.max}, which is an empty range: nothing can be both above the one and below the other")
        return self

    @model_validator(mode="after")
    def _values_are_not_empty(self) -> "FilterByModel":
        # An empty list is not "match nothing" here, it is a caller who meant to send something.
        # "Draw nothing" is expressible -- switch the layer off -- and is never what a filter is for.
        if self.values is not None and not self.values:
            raise ValueError("`values` is empty, which would match no object at all. Omit the filter to draw everything, or hide the layer to draw nothing")
        return self


class PickerFilterByModel(FilterByModel):
    """One entry of a layer's filter picker: a rule, and what to call it in the UI.

    A subclass for the same reason :class:`~core.render.color_by.PickerColorByModel` is one: the
    caption belongs to the picker rather than to the rule. Two entries over the same column are
    *allowed* here, unlike in the colour picker -- "small cells" and "large cells" are two
    different rules over one measure, and the label is what tells them apart.
    """

    label: str | None = None


class MeshFilterByModel(PickerFilterByModel):
    """One entry of a mesh layer's filter picker, named for the GraphQL type it backs."""


class LabelFilterByModel(PickerFilterByModel):
    """One entry of a label layer's filter picker, named for the GraphQL type it backs."""


class NetworkFilterByModel(PickerFilterByModel):
    """One entry of a network layer's filter picker, named for the GraphQL type it backs.

    The one picker whose rules may be GRAPH-kind, exactly as
    :class:`~core.render.color_by.NetworkColorByModel` is for colourings.
    """
