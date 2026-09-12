"""Mutations for the edges of the coordinate graph.

This is where registration lives now. It used to be a 4x4 matrix on the layer,
which meant two layers over one dataset carried two copies of one fact and were
free to disagree; it is now a single edge between two coordinate systems -- and
under RFC-6 that edge is *the* truth: unique per (data-tree, shared space), so
authoring it places the data in every scene over that space, with no membership
to declare. Refining it is `updateTransformation`, in place and audited.

A **rival** registration is allowed to sit beside it rather than forking the space,
which is the opposite of what this note used to say. RFC-9 permitted the rival row;
what was missing until now was the rule that ranks the two, so the walk returned
whichever the BFS reached first -- meaning a one-hop guess beat a two-hop measured
chain. `core.logic.graph._bfs_tree` now searches widest-path over `validity`, so the
better-known route wins and the alternative can be kept without hijacking placement.

Direction is always forward: input to output. Registration libraries routinely
hand you the inverse, so normalize before you call these -- there is deliberately
no flag to record which way round a matrix runs, because half the graph would
end up pointing the wrong way and nothing would tell you.
"""

from django.db.models import Q
from kante.types import Info
import strawberry
from pydantic import BaseModel, Field

import kante
from core import enums, models, types
from core.creation import CreationContext
from core.inputs.coords import SelectorInput, SelectorInputModel, TransformInput, TransformSpec
from core.logic import pickers
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import graph as graph_logic
from core.mutations._generic import assert_can_delete, creator_owner, make_delete
from core.scoping import get_for_org


class CreateTransformationInputModel(BaseModel):
    input: str
    output: str
    transform: TransformSpec
    name: str | None = None
    validity: enums.PlacementValidity | None = None
    value_relation: enums.ValueRelation | None = None
    selector: SelectorInputModel | None = None


@kante.pydantic_input(CreateTransformationInputModel, description="Input for creating one edge of the coordinate graph, mapping an input coordinate system to an output one")
class CreateTransformationInput:
    """Input for creating one edge of the coordinate graph."""

    input: strawberry.ID = strawberry.field(description="The coordinate system this transformation maps from")
    output: strawberry.ID = strawberry.field(description="The coordinate system this transformation maps to. Direction is always forward -- if your registration library gave you the inverse, invert it before calling")
    transform: TransformInput = strawberry.field(description="The edge's kind and parameters, as a discriminated union: `kind` selects a member, and only that member's fields are read -- any other supplied field is rejected, never dropped")
    name: str | None = strawberry.field(default=None, description="Optional name for the transformation")
    validity: enums.PlacementValidity | None = strawberry.field(
        default=None,
        description="How much this map is actually known. Defaults to MANUAL -- someone authored it. Say VALIDATED when the registration was checked against the data, INFERRED when the numbers were read from metadata. A layer's validity is the weakest edge on its path to world",
    )
    value_relation: enums.ValueRelation | None = strawberry.field(
        default=None,
        description="(derivation edges only) What the operation did to the *values*, orthogonal to the transform's `kind`: IDENTICAL for a crop, TRANSFORMED for a deconvolution, CATEGORIZED for a threshold or segmentation. It rides whichever edge its author thinks it describes -- there is no class of edge across which values are known not to travel",
    )
    selector: SelectorInput | None = strawberry.field(
        default=None,
        description=(
            "Where along one axis this edge applies, e.g. {axis: \"c\", index: 2} for a per-channel correction. Orthogonal to `kind` -- any kind of map can be "
            "scoped this way. Omit it for an edge that holds everywhere, which is the usual case. A path query crosses a scoped edge only when it fixes that "
            "coordinate with `at`, because without it where the data sits genuinely has no single answer"
        ),
    )


def create_transformation(info: Info, input: CreateTransformationInput) -> types.Transformation:
    """Create one edge of the coordinate graph.

    A thin request-scoped wrapper: it lowers the transform union to its member's
    parameters, resolves the two systems and any field node, then delegates the rank
    check and write to :func:`core.logic.graph.build_registration_edge`, which the
    coordinate-system builder shares. An edge into a shared space is a registration and
    places its data in every scene over that space by existing -- there is no scene to
    name and nothing to endorse. BY_DIMENSION is how a registration crosses a rank
    boundary: a (c,y,x) dataset placed into a (t,z,y,x) world names the axes it acts on
    and says nothing about the world's `t` and `z`, which is exactly the truth.
    """
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    lowered = model.transform.lower()
    input_system = get_for_org(models.CoordinateSystem, info, id=model.input)
    output_system = get_for_org(models.CoordinateSystem, info, id=model.output)
    field = get_for_org(models.CoordinateSystem, info, id=lowered.field) if lowered.field else None

    return graph_logic.build_registration_edge(
        input_system=input_system,
        output_system=output_system,
        kind=lowered.kind,
        name=model.name,
        scale=lowered.scale,
        translation=lowered.translation,
        affine=lowered.affine,
        selector=model.selector.model_dump() if model.selector else None,
        input_axes=lowered.input_axes,
        output_axes=lowered.output_axes,
        field=field,
        reason=lowered.reason,
        validity=model.validity,
        value_relation=model.value_relation,
        ctx=ctx,
    )


class UpdateTransformationInputModel(BaseModel):
    id: str
    name: str | None = None
    scale: list[float] | None = None
    translation: list[float] | None = None
    affine: list[list[float]] | None = None
    validity: enums.PlacementValidity | None = None


def _rank_endpoints(transformation: "models.Transformation") -> tuple | None:
    """The systems and axis lists a refinement's rank must answer to, or None if there are none.

    An edge normally answers to its own endpoints. A **wrapper child** has none -- `_sequence`
    says so itself: *"The children omit input and output: the wrapping sequence supplies them"*
    -- so until this existed the guard below simply skipped, and `assert_edge_rank` never ran
    for any of them. Every stepped lens and every downsampled pyramid level has such a child,
    its id is exposed through `SequenceTransformation.transformations`, and `updatable_params`
    reads the *child's* kind, so a SCALE child accepted a vector of any length. `to_matrix`
    then wrote a two-entry scale's last number into `matrix[1][1]` on a four-axis dataset and
    left the rest unscaled -- silently, which is the failure `assert_edge_rank`'s own docstring
    says it exists to prevent.

    **The parent's kind decides what the child answers to, and getting this wrong manufactures
    a regression.** A SEQUENCE applies each child to the whole space, so a child answers to the
    wrapper's full rank and full axis names. A BY_DIMENSION applies its children to the axes it
    *names*: `_sub_matrix` composes them at ``len(acts_on_input)`` and `_by_dimension_forms`
    labels the rows by ``acts_on_output``. Inheriting the endpoints without that distinction
    would check a BY_DIMENSION child against the full system -- rejecting a two-entry scale
    acting on ``["y", "x"]`` inside a ``(t, z, y, x)`` wrapper for its rank, and rejecting one
    inside a ``(c,y,x) -> (t,z,y,x)`` wrapper for names it does not touch. Relating two spaces
    of different rank and different names is what BY_DIMENSION is *for*, so that is not an
    exotic shape to protect: it is the ordinary one.
    """
    if transformation.input and transformation.output:
        return (transformation.input, transformation.output, None)

    parent = transformation.parent
    if parent is None or not (parent.input and parent.output):
        # An orphan child, or a wrapper that is itself endpoint-less. Nothing fixes the rank,
        # so there is nothing to check it against -- the same answer as before, now said out
        # loud rather than falling out of a truthiness test.
        return None

    if parent.kind == enums.TransformKind.BY_DIMENSION.value:
        # The subset has to be handed over explicitly: `assert_edge_rank` reads the axis lists
        # off the edge only for its own axis-carrying kinds, and this child's kind is SCALE or
        # TRANSLATION. Passed as the two ordered *name lists* rather than a pair of counts,
        # because the checks that would otherwise misfire include two that compare names.
        return (parent.input, parent.output, (list(parent.input_axes or ()), list(parent.output_axes or ())))
    return (parent.input, parent.output, None)


@kante.pydantic_input(UpdateTransformationInputModel, description="Input for refining an edge's parameters. The refinement is recorded in the edge's provenance, which is what tells an ROI its chain has moved")
class UpdateTransformationInput:
    """Input for refining an edge's parameters."""

    id: strawberry.ID = strawberry.field(description="The ID of the transformation to refine")
    name: str | None = strawberry.field(default=None, description="A new name for the transformation")
    scale: list[float] | None = strawberry.field(default=None, description="(SCALE) The refined per-axis scale factors")
    translation: list[float] | None = strawberry.field(default=None, description="(TRANSLATION) The refined per-axis offsets")
    affine: list[list[float]] | None = strawberry.field(default=None, description="(AFFINE / ROTATION) The refined matrix")
    validity: enums.PlacementValidity | None = strawberry.field(
        default=None,
        description="A new validity for the edge -- how it stops being an assumption: set MANUAL when a real registration replaces an assumed one in place, VALIDATED when it was checked against the data. Every layer whose path runs through this edge reflects it immediately, because a layer's validity is derived, never stored",
    )


def update_transformation(info: Info, input: UpdateTransformationInput) -> types.Transformation:
    """Refine an edge's parameters in place.

    The history row the save writes is the signal that every ROI downstream of this edge was
    authored against an older chain. Recomputing their bounding boxes is a separate,
    bulk operation: a registration refinement can touch thousands of them, so it does
    not belong on this request's critical path.
    """
    model = input.to_pydantic()

    transformation = get_for_org(models.Transformation, info, id=model.id)

    supplied = [field for field in ("scale", "translation", "affine") if getattr(model, field) is not None]
    if transformation.kind == enums.TransformKind.UNMAPPABLE.value and supplied:
        # `assert_edge_rank` returns early for an UNMAPPABLE (it has no rank to check), so
        # without this the parameters would be written and simply never read -- a map that
        # exists in the database and nowhere else. If the geometry turns out to survive
        # after all, the edge was the wrong kind, and changing the kind is the honest fix.
        raise ValueError(f"An UNMAPPABLE transformation declares that no point of one space corresponds to a point of the other, so there is nothing to refine: it has no `{supplied[0]}`. If a correspondence does exist, replace the edge with one whose kind can express it.")

    # The kinds gate refinement exactly as they gate creation: a parameter the kind never
    # reads would be written and never read back -- and worse, `invariance_of` classifies a
    # childless composite by its params keys, so a stray `affine` would demote what clients
    # are told survives the map.
    allowed = graph_logic.updatable_params(transformation.kind)
    offending = [field for field in supplied if field not in allowed]
    if offending:
        if transformation.kind == enums.TransformKind.SEQUENCE.value:
            raise ValueError(f"A {transformation.kind} transformation is a wrapper: its parameters live on its children, so there is no `{offending[0]}` here to refine. Refine the child edge instead.")
        reads_clause = "it reads " + ", ".join(f"`{param}`" for param in allowed) if allowed else "it takes no parameters at all"
        raise ValueError(f"A {transformation.kind} transformation does not read `{offending[0]}`: {reads_clause}, so refining it would write a number nothing reads.")

    params = dict(transformation.params)
    for field in ("scale", "translation", "affine"):
        value = getattr(model, field)
        if value is not None:
            params[field] = value

    # A refinement is a write like any other, so it answers to both creation gates. The
    # values gate is easy to miss here because this mutation takes its parameters flat --
    # there is no `TransformInput`, so the union members' validators never run -- and the
    # params dict is assembled by hand rather than through `_assemble_edge_params`. Without
    # this line, refining a SCALE edge to `[0, 0]` is the one way left to store a map that
    # collapses an axis.
    graph_logic.assert_edge_values(params)

    # The rank it lands on is the rank the endpoints already fix, and an update that
    # silently changed it would be a back door around the check `create_transformation` makes.
    # A wrapper child has no endpoints of its own and answers to its parent's -- see
    # `_rank_endpoints`, which is where that lookup and its one subtlety live.
    endpoints = _rank_endpoints(transformation)
    if endpoints is not None:
        input_system, output_system, subset_axes = endpoints
        graph_logic.assert_edge_rank(
            kind=transformation.kind,
            params=params,
            input_axes=transformation.input_axes,
            output_axes=transformation.output_axes,
            input_system=input_system,
            output_system=output_system,
            subset_axes=subset_axes,
        )

    if model.name is not None:
        transformation.name = model.name

    if model.validity is not None:
        transformation.validity = model.validity.value

    transformation.params = params
    # The save is the record. `provenance` writes a history row for it, which is what
    # `graph_logic.transform_version` counts and what tells an ROI its chain has moved --
    # there is no counter here to bump, and so none to forget.
    transformation.save(update_fields=["params", "name", "validity"])

    # A BY_DIMENSION publishes its map to clients as child rows, which this refinement has just
    # made stale. Composition here does not read them -- `_sub_matrix` takes the params it was
    # given -- so this is the client's copy catching up, not the server's answer changing.
    graph_logic._project_by_dimension_children(transformation, CreationContext.from_info(info))

    return transformation


class DeleteTransformationInputModel(BaseModel):
    id: str = Field(description="The ID of the transformation to delete")


@kante.pydantic_input(DeleteTransformationInputModel, description="Input for deleting a transformation by ID")
class DeleteTransformationInput:
    """Input for deleting a transformation by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the transformation to delete")


# `creator_owner`, not `self_owner`: a Transformation has a creator but no
# `created_through_by`, which self_owner reads unconditionally.
#: PROTECT, the second route to a stranded picker: leaving the table in place and removing the
#: crossing strands an entry exactly as deleting the table does. Asked as a hypothetical -- the
#: walk re-run without this edge -- so a rival edge still providing the crossing is not refused.
delete_transformation = make_delete(models.Transformation, DeleteTransformationInput, owner=creator_owner, guard=pickers.assert_edge_not_stranding_a_picker)


class DeleteRegistrationInputModel(BaseModel):
    dataset: str | None = None
    table_dataset: str | None = None
    mesh_collection: str | None = None
    annotation_collection: str | None = None
    coordinate_system: str | None = None
    world: str = Field(description="The shared space the registration goes into")


@kante.pydantic_input(
    DeleteRegistrationInputModel,
    description="Input for un-registering a source from a shared space by naming the source and the space, not the edge. Provide exactly one source -- the same selector registering it took",
)
class DeleteRegistrationInput:
    """Input for un-registering a source from a shared space."""

    dataset: strawberry.ID | None = strawberry.field(default=None, description="Un-register this dataset. Provide exactly one source")
    table_dataset: strawberry.ID | None = strawberry.field(default=None, description="Un-register this table dataset. Provide exactly one source")
    mesh_collection: strawberry.ID | None = strawberry.field(default=None, description="Un-register this mesh collection. Provide exactly one source")
    annotation_collection: strawberry.ID | None = strawberry.field(default=None, description="Un-register this annotation collection. Provide exactly one source")
    coordinate_system: strawberry.ID | None = strawberry.field(default=None, description="Un-register this coordinate system. Provide exactly one source")
    world: strawberry.ID = strawberry.field(description="The space to un-register the source from")


def delete_registration(info: Info, input: DeleteRegistrationInput) -> list[strawberry.ID]:
    """Delete the registration placing a source in a shared space, named by source and space.

    The inverse of a `registrations` entry in `createCoordinateSystem`, for the caller who
    knows *what* is related *where* but not the edge id. It deletes **every** edge from the
    source's space into the named one: under RFC-9 rivals are allowed, so there is no longer
    one edge to mean, and "un-register this source from that space" is only unambiguous if it
    means all of them. Deleting them un-places the source in every scene over the space
    (layers drop to UNREGISTERED); an UNMAPPABLE declaration is not a placement and is not
    matched -- delete it by id with `deleteTransformation`.
    """
    model = input.to_pydantic()

    world = get_for_org(models.CoordinateSystem, info, id=model.world)

    source_system = coordinate_system_logic.resolve_source_system(
        dataset=get_for_org(models.ArrayDataset, info, id=model.dataset) if model.dataset else None,
        table_dataset=get_for_org(models.TableDataset, info, id=model.table_dataset) if model.table_dataset else None,
        mesh_collection=get_for_org(models.MeshCollection, info, id=model.mesh_collection) if model.mesh_collection else None,
        annotation_collection=get_for_org(models.AnnotationCollection, info, id=model.annotation_collection) if model.annotation_collection else None,
        coordinate_system=get_for_org(models.CoordinateSystem, info, id=model.coordinate_system) if model.coordinate_system else None,
    )

    # **Every** matching edge, not one (RFC-9). Under RFC-6 at most one could match, because
    # a fact tree had one claim per space; with rivals allowed there may be several, and
    # "un-register this source from that space" means all of them -- the same shape
    # `clearCoordinateSystem` already has for a whole space. Each is authorised separately,
    # so a rival somebody else authored is refused rather than quietly swept up.
    # Every space the source's data lives in, not just the one named. Registering through a
    # lens and un-registering by the dataset still meet, which is what the old claim-root
    # match bought and what a caller naming a *source* rather than an edge expects.
    dataset_id = graph_logic.residence_map([source_system.pk]).get(source_system.pk)
    inputs = {source_system.pk}
    if dataset_id is not None:
        inputs |= {
            system_id
            for system_id in models.CoordinateSystem.objects.filter(
                Q(datasets__id=dataset_id) | Q(lenses__dataset_id=dataset_id) | Q(data_arrays__dataset_id=dataset_id)
            ).values_list("pk", flat=True)
        }

    edges = list(
        models.Transformation.objects.filter(output=world, input_id__in=inputs, parent__isnull=True)
        .exclude(kind=enums.TransformKindChoices.UNMAPPABLE.value)
        .select_related("input")
        .order_by("pk")
    )
    if not edges:
        raise ValueError(f"Nothing to delete: no edge relates this source to '{world.name}'.")

    deleted: list[strawberry.ID] = []
    for edge in edges:
        assert_can_delete(info, edge, creator_owner)
        deleted.append(strawberry.ID(str(edge.pk)))
        edge.delete()
    return deleted
