"""Attach metadata to an array dataset or a table dataset after ingest.

`createArrayDataset` and `createTableDataset` take their anchors inline, which serves a
converter that knows the microscope state as it writes the pixels. It does not serve the
task that computes a histogram a week later, or the user who labels a channel of a table
someone else uploaded. This is the one mutation for that: name the container, the
coordinates, and the spokes, and they land on the one anchor at those coordinates --
created if it is not there, and with a spoke stated twice replaced rather than doubled.

The phasor mutations are the array-only special case of this and stay where they are.
"""

from django.db import transaction
from kante.types import Info
from pydantic import BaseModel
import strawberry

import kante
from core import enums, models, types
from core.mutations.array_dataset import (
    CoordinateAnchorInput,
    CoordinateAnchorInputModel,
    _get_or_create_anchor,
    _write_anchor_spokes,
    assert_anchors_name_axes,
)
from core.scoping import get_for_org


class CreateCoordinateAnchorInputModel(BaseModel):
    dataset: str | None = None
    table: str | None = None
    sparse: str | None = None
    anchor: CoordinateAnchorInputModel


@kante.pydantic_input(
    CreateCoordinateAnchorInputModel,
    description=(
        "Attach metadata spokes to an array, table or sparse dataset after ingest. Exactly one of `dataset`, `table` and `sparse` names the container; `anchor` carries the coordinates "
        "and the spokes. Get-or-create on (container, coordinates): a second call at the same coordinates adds its spokes to the one anchor, and a spoke stated twice is replaced"
    ),
)
class CreateCoordinateAnchorInput:
    """Input for attaching metadata spokes to an existing container."""

    dataset: strawberry.ID | None = strawberry.field(default=None, description="The array dataset to anchor into. Exactly one of `dataset`, `table` and `sparse`")
    table: strawberry.ID | None = strawberry.field(default=None, description="The table dataset to anchor into. Exactly one of `dataset`, `table` and `sparse`")
    sparse: strawberry.ID | None = strawberry.field(default=None, description="The sparse dataset to anchor into. Exactly one of `dataset`, `table` and `sparse`")
    anchor: CoordinateAnchorInput = strawberry.field(
        description=(
            "The coordinates to pin to and the spokes to pin there. For an array dataset the axis anchors name its axes and level-0 indices; for a table dataset they name its "
            "axis-typed columns and values along them; for a sparse dataset they name its enumerated axes and positions along them. An empty `axisAnchors` list is an anchor global over the whole container. Phasor spokes are array-only"
        )
    )


def create_coordinate_anchor(info: Info, input: CreateCoordinateAnchorInput) -> types.CoordinateAnchor:
    """Attach metadata spokes to the anchor at some coordinates of a dataset or a table."""
    model = input.to_pydantic()

    if sum(identifier is not None for identifier in (model.dataset, model.table, model.sparse)) != 1:
        raise ValueError("Name exactly one of `dataset`, `table` or `sparse`: an anchor pins into one container.")

    with transaction.atomic():
        if model.dataset is not None:
            container: models.ArrayDataset | models.TableDataset | models.SparseDataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
            axis_specs = container.axis_specs
            assert_anchors_name_axes([model.anchor], [spec.name for spec in axis_specs], what="dataset")
        elif model.table is not None:
            container = get_for_org(models.TableDataset, info, id=model.table)
            axis_specs = None
            assert_anchors_name_axes([model.anchor], [column.name for column in container.columns_by_role(enums.ColumnRoleChoices.COORDINATE.value)], what="table")
        else:
            container = get_for_org(models.SparseDataset, info, id=model.sparse)
            axis_specs = None
            assert_anchors_name_axes([model.anchor], container.axis_names, what="sparse dataset")

        anchor = _get_or_create_anchor(container, model.anchor.axis_anchors)
        _write_anchor_spokes(anchor, model.anchor, axis_specs=axis_specs)

    return anchor
