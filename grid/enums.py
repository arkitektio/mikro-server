"""The grid vocabulary: what a layer reads."""

from enum import Enum

import strawberry
from django.db.models import TextChoices


class GridLayerKindChoices(TextChoices):
    HEATMAP = "heatmap", "Heatmap (an array or a sparse matrix, one cell per position)"
    CELLS = "cells", "Cells (a table's rows, one cell per row at its position, coloured by a column)"


@strawberry.enum(
    description=(
        "The kind of a grid layer, which says what it reads. HEATMAP reads an array or a sparse matrix whose free axes are the grid's: one cell per position, its value "
        "the colour -- genes against cells. CELLS reads a table whose frame is the grid's axes: one cell per row, coloured by the value of a column -- the mean of each "
        "cell in each channel of a long-form table, or a value per well of a plate."
    )
)
class GridLayerKind(str, Enum):
    """The kind of a grid layer: what it reads."""

    HEATMAP = "heatmap"
    CELLS = "cells"
