"""Creating a sparse dataset: two identified axes, and one or both stored layouts.

The shape is stated in :mod:`core.models.sparse_dataset`. What this module adds is the
checking, and every check here exists because the thing it catches is otherwise silent:

* a store whose declared shape contradicts the axes, which would place every lookup one
  position out and never raise;
* a store whose layouts do not cover the axes the dataset claims, or cover one twice;
* a keyed axis the source cannot actually supply -- stated by the caller now, so the
  derivation can be checked against it rather than only performed.

An axis identified twice, or not at all, is no longer among them: identification lives *on* the
axis (:class:`core.inputs.sparse.SparseAxisInput`), so "exactly once" is a property of the input
rather than a check on it. That is the whole point of the shape -- an axis nothing identifies is
not a lax dataset, it is one no source could ever key, and the best time to catch it is at the
keystroke rather than in a rank mismatch three functions later.

Nothing about the *matrix* is declared: the spec, the shape, each layout's encoding, its
nonzero count and its chunking were read off the artifact when the upload was finished, and are
compared against the declaration rather than taken from it. The cross-store check this module
used to make -- that two separately registered stores describe the same matrix -- is gone, and
not because it moved: one matrix is one upload, so there is only one declaration and two of them
can no longer disagree.
"""

import strawberry
from django.db import transaction
from kante.types import Info
from pydantic import BaseModel, Field

import kante
from core import enums, models, scalars, types
from core.creation import CreationContext
from core.inputs.coords import AxisInputModel, DerivedFromInput, DerivedFromSpec
from core.inputs.file_link import SourceFileInput, SourceFileInputModel
from core.inputs.sparse import SparseAxisInput, SparseAxisInputModel
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import file_link as file_link_logic
from core.logic import folder as folder_logic
from core.logic import graph as graph_logic
from core.logic import identification as identification_logic
from core.logic import pickers
from core.mutations._generic import make_delete, self_owner
from core.scoping import get_for_org

#: The lowest rank a sparse dataset can have. Two, because a single compressed axis needs at
#: least one other axis to hold the positions.
#:
#: **There is no highest.** A layout is one axis made contiguous, so an array of rank *n* has up
#: to *n* of them, and the store format, the datalayer reader and this mutation are all written
#: that way -- a (object, feature, timepoint) matrix is a legal sparse dataset with three
#: identified axes.
#:
#: **The colouring surface generalises with it, and there is no rank > 2 refusal anywhere.**
#: `_resolve_sparse_slice` asks that `at` name exactly the identified axes and that some layout
#: index one of them; both statements are rank-agnostic, so a rank-three matrix is coloured by
#: naming a position along each of its two identified axes. An earlier version of this comment
#: claimed such a colouring "refuses with its own message" -- it does not, and never did.
_MIN_RANK = 2


class CreateSparseDatasetInputModel(BaseModel):
    name: str
    store: str
    axes: list[SparseAxisInputModel] = Field(default_factory=list)
    description: str | None = None
    folder: str | None = None
    derived_from: list[DerivedFromSpec] | None = None
    source_files: list[SourceFileInputModel] | None = None


@kante.pydantic_input(
    CreateSparseDatasetInputModel,
    description=(
        "Create a sparse dataset from one uploaded sparse store, which holds the matrix in one or more layouts. A sparse matrix is a grid of numbers with no row labels and no column "
        "labels, so **every axis says what its positions are** through its own `identifiedBy` -- a source whose contents are the ids, or the table whose rows they are. Carried on the "
        "axis, identified-exactly-once is a property of this input rather than a rule the server enforces. Nothing about the matrix itself is declared: the spec, shape, each "
        "layout's encoding and its chunking were read from the store when its upload was finished, and are checked against these axes rather than taken from them"
    ),
)
class CreateSparseDatasetInput:
    """Input for creating a sparse dataset."""

    name: str = strawberry.field(description="The name of the sparse dataset")
    store: scalars.SporadikLike = strawberry.field(
        description=(
            "The uploaded sparse store holding this matrix. **One matrix is one upload**: the store's prefix holds one or both layouts under `layouts/<encoding>`, and which axis a "
            "layout's `indptr` indexes decides which question it answers in one contiguous read -- asking the other is a scan of everything rather than a slower read. A store "
            "holding both layouts gives the dataset both capabilities"
        )
    )
    axes: list[SparseAxisInput] = strawberry.field(
        description=(
            "The matrix's axes, **in the order its store's `shape` is written** -- checked against it, so a declaration that disagrees with the bytes is refused rather than "
            "placing every lookup one position out. Each carries its own `identifiedBy`, which is the whole of what a sparse dataset needs beyond its bytes: a grid of numbers "
            "with no row labels and no column labels, plus one statement per axis of what its positions are"
        )
    )
    description: str | None = strawberry.field(default=None, description="A description of the sparse dataset")
    folder: strawberry.ID | None = strawberry.field(default=None, description="The folder to file it in")
    derived_from: list[DerivedFromInput] | None = strawberry.field(default=None, description="The data this matrix was computed from")
    source_files: list[SourceFileInput] | None = strawberry.field(default=None, description="The files it was converted from")


def _resolve_store(info: Info, identifier: str, name: str) -> "models.SparseStore":
    """The store this dataset is, refusing one whose upload was never finished.

    An unfinished store knows nothing about itself -- its spec, shape and layouts are read at
    ``finishSparseUpload`` -- so registering one would record a matrix whose layouts are simply
    unknown, which is the state this whole design exists to make unrepresentable.
    """
    store = get_for_org(models.SparseStore, info, id=identifier)
    if not store.populated:
        raise ValueError(
            f"Sparse store {store.pk} has not been finished, so nothing is known about what it holds. Call `finishSparseUpload` after the prefix is written -- that step is what "
            f"reads the store's own block, and it refuses one whose block is missing, whose spec is unknown, or that names a layout the prefix does not hold."
        )
    if not store.layouts:
        raise ValueError(
            f"Sparse store {store.pk} holds no layouts, so '{name}' would have no matrix. A store is its layouts -- one per axis its `indptr` could index."
        )
    return store


def _assert_store_agrees(store: "models.SparseStore", axes: list[AxisInputModel], name: str) -> dict[int, dict]:
    """Check the store against the declared axes, and return its layouts by the axis each indexes.

    The shape check is the one that matters, and it is only possible because the store read its
    own: a declaration that disagrees with the bytes places every lookup one position out and
    raises nothing, which is the failure `_assert_axes_agree` guards a mesh collection against
    for the same reason.
    """
    shape = [int(size) for size in (store.shape or [])]
    if len(shape) != len(axes):
        raise ValueError(
            f"'{name}' declares {len(axes)} axes {[axis.name for axis in axes]} but sparse store {store.pk} holds a matrix of shape {shape}. The axes describe the store, so they are the same number of them."
        )

    by_axis: dict[int, dict] = {}
    for layout in store.layouts or []:
        # Read from the layout rather than re-derived from its encoding: above rank two every
        # layout is a `csr_matrix` over the raveled view, so the encoding no longer names the
        # axis and a second derivation of it would be wrong exactly where rank stops being two.
        indexed = layout.get("indexed_axis")
        if not isinstance(indexed, int) or not 0 <= indexed < len(axes):
            raise ValueError(
                f"Sparse store {store.pk} holds a layout compressing axis {indexed!r}, which is not an axis of '{name}' ({[axis.name for axis in axes]})."
            )
        if indexed in by_axis:
            # Refused at registration too, so reaching it here would mean a store row written
            # around `finishSparseUpload`. Checked all the same: the cost is a dict lookup and
            # the failure it prevents is a reader picking one of two layouts arbitrarily.
            raise ValueError(
                f"Sparse store {store.pk} holds two layouts indexing axis {indexed} ('{axes[indexed].name}'). That is one capability twice, and nothing could say which a reader should use."
            )
        by_axis[indexed] = layout

    return by_axis


def _resolve_identifications(
    info: Info, model: CreateSparseDatasetInputModel
) -> tuple[dict[str, "models.TableDataset"], list[tuple[str, object]]]:
    """The referenced tables, resolved, and the sources keying each axis, in declaration order.

    Small, because the shape does the work and the splitting is shared -- see
    :func:`core.logic.identification.split_identifications`, which the table create runs too.
    ``axis_types=None`` because every axis of a sparse matrix is INDEX by construction, so the
    narrowing that guards a table's SPACE axes and data columns has nothing to guard here.

    What is left is the two things a shape cannot state: that an axis is identified at all,
    and that *something* keys this matrix.
    """
    empty = [axis.name for axis in model.axes if not axis.identified_by]
    if empty:
        # The one line the list form costs, and it is worth it: `identifiedBy` was singular and
        # so this was free, but a singular field cannot say "keyed by a nucleus mask and a cell
        # mask", which write_key_edges has always supported and which is an ordinary case.
        raise ValueError(
            f"'{model.name}' declares {empty} with an empty `identifiedBy`. An axis of a sparse matrix is positions and nothing else, so one that says what they are is one no "
            "source could ever key -- there is no FIELD edge onto it and no colouring along it. Name a mask, a collection, or the table whose rows the positions are."
        )

    references, node_references, keyed = identification_logic.split_identifications(
        info,
        name=model.name,
        entries=[(axis.name, axis.identified_by) for axis in model.axes],
        axis_types=None,
    )

    if node_references:
        # Deferral stated as refusal, never silence: a sparse axis has no column to carry a
        # node-scoped identification and no sibling axis convention to scope it by, so the
        # composite (object, node) position has nowhere honest to live here.
        named = sorted(node_references)
        raise ValueError(
            f"'{model.name}' identifies {named} by a network collection's nodes, which a sparse matrix cannot carry: a node id is unique only within its object, and a matrix "
            "axis has no sibling-column convention to scope it by. Per-node values want a TABLE keyed by (object, node) -- create one with createTableDataset and the "
            "NETWORK_COLLECTION_NODES identification instead."
        )

    # A matrix every axis of which a table identifies -- a gene-by-pathway membership, a
    # cell-by-cell contact map -- authors no FIELD edge, so no layer stands on it and no
    # colouring over it is constructible. It used to be refused for that. It is reachable all
    # the same: a plan that lands in one of those tables hops *into* the matrix along the axis
    # that table identifies (:mod:`core.logic.join_walk`), and a hover that walked mask ->
    # matrix -> gene table -> this matrix -> pathway table is exactly what such a dataset is
    # for. So it is legal, and reachable only through a hop, which is honest rather than useless.
    return references, keyed


def create_sparse_dataset(info: Info, input: CreateSparseDatasetInput) -> types.SparseDataset:
    """Create a sparse dataset, its owned coordinate system, and the edges identifying its axes."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    if len(model.axes) < _MIN_RANK:
        raise ValueError(
            f"'{model.name}' declares {len(model.axes)} axes, but a sparse array has at least {_MIN_RANK} -- a single compressed axis needs at least one other to hold the positions. "
            "The number of axes is checked against the store's own shape as well, so it is the matrix that decides the rank, not the declaration."
        )
    duplicates = sorted({axis.name for axis in model.axes if [entry.name for entry in model.axes].count(axis.name) > 1})
    if duplicates:
        raise ValueError(f"'{model.name}' declares the axis {duplicates} more than once. An axis name is how a colouring names a position along it, so it has to pick one axis.")

    # No INDEX check: `SparseAxisInput` has no `type`, because both axes of a sparse matrix
    # enumerate and neither has a metric, so INDEX was the only value the field could ever hold.
    # A field that exists only to be got wrong is one refusal and one input field fewer without it.
    store = _resolve_store(info, model.store, model.name)
    by_axis = _assert_store_agrees(store, model.axes, model.name)
    targets, keyed = _resolve_identifications(info, model)

    # Atomic for the reason `create_table_dataset` is: the row, its axes and its references are
    # written before the edges are checked, and an edge the rank rule refuses would otherwise
    # leave an orphan dataset behind and return an error.
    with transaction.atomic():
        system = models.CoordinateSystem.objects.create(name=f"{model.name}/sparse", creator=ctx.user, organization=ctx.organization)
        dataset = models.SparseDataset.objects.create(
            name=model.name,
            description=model.description,
            coordinate_system=system,
            folder=folder_logic.folder_for_new_container(info, ctx, model.folder, model.derived_from),
            creator=ctx.user,
            organization=ctx.organization,
            **ctx.provenance_kwargs(),
        )
        # INDEX is supplied here rather than declared, which is now the one place it lives.
        graph_logic.create_pixel_axes(
            system,
            [
                AxisInputModel(name=axis.name, type=enums.AxisType.INDEX, long_name=axis.long_name, description=axis.description)
                for axis in model.axes
            ],
        )

        models.SparseArray.objects.bulk_create(
            [
                models.SparseArray(dataset=dataset, store=store, path=str(layout.get("path")), indexed_axis=indexed)
                for indexed, layout in sorted(by_axis.items())
            ]
        )
        # Before the key edges, and that ordering is load-bearing: `write_key_edges` derives its
        # axis split from what the target identifies, so a reference written afterwards would
        # leave the edge trying to produce an axis it should have left alone. Visible in the input
        # now -- an axis says which of the two it is -- but the write order still has to match.
        models.SparseAxisReference.objects.bulk_create(
            [models.SparseAxisReference(dataset=dataset, axis=axis, references=target) for axis, target in targets.items()]
        )

        coordinate_system_logic.write_derivation_edges(info, name=dataset.name, own_system=system, derived_from=model.derived_from or [], ctx=ctx)
        file_link_logic.write_file_links(info, container=dataset, source_files=model.source_files or [], ctx=ctx)
        # The caller stated which axis each source keys, so the derivation is checked against it
        # rather than only performed. `consumed` and the passthrough are still derived -- asking
        # for those would be asking a caller to restate the two systems' axes at each other.
        coordinate_system_logic.write_key_edges(
            info,
            name=dataset.name,
            own_system=system,
            keyed_by=[identification for _, identification in keyed],
            ctx=ctx,
            produces=[axis for axis, _ in keyed],
        )

    return dataset


class UpdateSparseDatasetInputModel(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None


@kante.pydantic_input(UpdateSparseDatasetInputModel, description="Input for renaming or redescribing a sparse dataset")
class UpdateSparseDatasetInput:
    """Input for updating a sparse dataset."""

    id: strawberry.ID = strawberry.field(description="The ID of the sparse dataset to update")
    name: str | None = strawberry.field(default=None, description="A new name")
    description: str | None = strawberry.field(default=None, description="A new description")


def update_sparse_dataset(info: Info, input: UpdateSparseDatasetInput) -> types.SparseDataset:
    """Rename a sparse dataset, or redescribe it. Those two are the whole of what is editable.

    Not here: the store, the axes, the references and the coordinate system. All are written
    once at creation, and a sparse dataset's own system is refused by ``updateCoordinateSystem``
    besides. A recomputation is a new dataset.
    """
    model = input.to_pydantic()
    dataset = get_for_org(models.SparseDataset, info, id=model.id)
    if model.name is not None:
        dataset.name = model.name
    if model.description is not None:
        dataset.description = model.description
    dataset.save()
    return dataset


class DeleteSparseDatasetInputModel(BaseModel):
    id: str = Field(description="The ID of the sparse dataset to delete")


@kante.pydantic_input(DeleteSparseDatasetInputModel, description="Input for deleting a sparse dataset by ID")
class DeleteSparseDatasetInput:
    """Input for deleting a sparse dataset by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the sparse dataset to delete")


#: PROTECT, for the reason a table dataset is protected: a colouring names its source by id
#: inside a JSON column, so there is no foreign key to cascade and a deleted matrix leaves an
#: entry nothing can execute. See :func:`core.logic.pickers.assert_sparse_dataset_not_in_a_picker`.
delete_sparse_dataset = make_delete(models.SparseDataset, DeleteSparseDatasetInput, owner=self_owner, guard=pickers.assert_sparse_dataset_not_in_a_picker)
