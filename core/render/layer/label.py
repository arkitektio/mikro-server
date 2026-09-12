"""How a segmentation or instance map becomes color.

A label layer renders an array whose values are *object ids*, and none of the image
layer's vocabulary survives the change of value domain: contrast limits and gamma are
contrast on a continuous intensity, a colormap maps an ordered scalar (ids 41 and 42 are
adjacent in no sense), and the intensity projections -- MIP, mean -- are statistics whose
result is not an object. So this is not a :class:`~core.render.layer.models.TransferFunctionModel`
with a flag, and deliberately not a node union either: a label layer has exactly one
source and no compositing tree to put under a blend node.

What it carries instead is the vocabulary ids actually have: a hash from id to color, a
background id drawn transparent, contour-or-fill, a selection, and -- the two that need
the coordinate graph -- ``color_bys`` and ``filter_bys``, which dereference the ``FIELD``
edge keying this mask's pixels to a table of objects (``createTableDataset(keyedBy:)``).
Colouring instances by a measured column, or a class column, is the same display pick a
point layer makes with ``color_column``: honestly per-layer view state, and RFC-8 says so.
Filtering by one is the same pick over the same join, deciding whether an object is drawn.

**Both are ordered pickers rather than single settings**, exactly as a mesh layer's are and
for the same reason: which reading of a segmentation someone is looking at right now -- area
through a colormap, cell type through a palette, only the large objects -- is a decision
the person at the screen makes, while *which readings are worth switching between* is the
author's. So the author publishes the list and stores the current choice as an index into it.

Nothing here is a spatial fact. The mask's placement is its dataset's, derived over the
graph, exactly as for an image layer.
"""

from pydantic import BaseModel, Field

from core.render.color_by import LabelColorByModel
from core.render.filter_by import LabelFilterByModel

#: Colouring objects by a joined column is not a label-layer fact -- a mesh layer does the
#: same thing through the same FIELD edge -- so the models live in
#: :mod:`core.render.color_by` and :mod:`core.render.filter_by` and are re-exported here.
#: The GraphQL surface keeps two names for the two contexts; the stored shape is one.
__all__ = ["LabelColorByModel", "LabelFilterByModel", "LabelRenderModel"]


class LabelRenderModel(BaseModel):
    """The full render recipe of a label layer."""

    intensity_axis: str | None = None
    intensity_index: int = 0
    seed: int = 0
    background: int = 0
    opacity: float | None = 1.0
    contour: bool = False
    contour_width: float | None = 1.0
    selected: list[int] = Field(default_factory=list)
    selection_color: list[int] | None = None
    show_unselected: bool = True
    # The published colour picker, in the order a menu should show it. Empty means the ids are
    # hashed to colours, which is what having no colouring has always meant.
    color_bys: list[LabelColorByModel] = Field(default_factory=list)
    # An index into that list rather than a copy of the chosen entry, or a flag on it: a
    # duplicate is free to disagree with the entry it duplicates, and a per-entry `active`
    # boolean makes "two active at once" representable when only one can be drawn.
    active_color_by: int | None = None
    # The colour picker's sibling over the same FIELD edge: where a colouring says what an
    # object looks like, a filter says whether it is drawn at all.
    filter_bys: list[LabelFilterByModel] = Field(default_factory=list)
    # A list of indices, not one, because filters *compose*: several being applied at once is
    # the normal case rather than a contradiction. Combined with AND -- an object draws when
    # every active rule keeps it.
    active_filter_bys: list[int] = Field(default_factory=list)
