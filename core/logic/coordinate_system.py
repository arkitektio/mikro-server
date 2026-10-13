"""Creating coordinate systems: shared spaces, the edges into them, and a lens' own space.

A SHARED system is the one coordinate system with no owner (see
:mod:`core.models.coords`): a reference space (a world, an atlas) that datasets, tables
and mesh collections are registered into, and that scenes later adopt as their world.
This is where those registration edges are authored -- explicitly, exactly as
``createTransformation`` authors one, never fabricated.
:func:`core.logic.scene.bootstrap_scene_from_system` only *reads* them.

Everything here makes *spaces and edges*, and none of it takes a scene. That is the line
this module draws against :mod:`core.logic.scene`, which makes scenes and layers: a lens'
coordinate system is a fact about a dataset's spaces, answerable with no composition in
sight, and it lived in the scene module only because the scene bootstrap happened to be the
caller.
"""

import datetime
import math
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, cast

from django.db import IntegrityError, transaction
from django.db.models import Q

from core import base_models, enums, models
from core import descriptors as descriptors_logic
from core.creation import CreationContext
from core.inputs.coords import IDENTITY_TRANSFORM, PhysicalAxisInputModel
from core.logic import compositions as compositions_logic
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from core.logic import pickers as pickers_logic
from core.logic import placement as placement_logic
from core.scoping import get_for_org

if TYPE_CHECKING:
    from django.db.models import QuerySet


def create_world_space(
    *,
    name: str,
    axes: list | None = None,
    epoch: datetime.datetime | None = None,
    ctx: CreationContext,
) -> "models.CoordinateSystem":
    """Mint an ownerless shared space with physical axes, for a scene to adopt.

    The convenience half of `createScene`: a client that passes no `coordinateSystem` gets
    one of these and a scene over it, which is the same pair of rows `createCoordinateSystem`
    followed by `createScene(coordinateSystem:)` produces. It is a *space* either way -- no
    scene owns it, it outlives every scene over it, and only `deleteCoordinateSystem` removes
    it -- which is why the minting lives here and not in the scene module.

    The epoch lands on this system, not on the scene adopting it: it is the origin of the
    *space's* time axis, and two compositions over one space cannot disagree about it.
    """
    axes = axes or DEFAULT_WORLD_AXES
    with transaction.atomic():
        world = models.CoordinateSystem.objects.create(
            name=name,
            epoch=epoch,
            creator=ctx.user,
            organization=ctx.organization,
        )
        graph_logic.create_physical_axes(world, axes)
    return world


def create_coordinate_system(
    *,
    name: str,
    axes: list,
    epoch: datetime.datetime | None = None,
    registrations: Sequence[tuple["models.CoordinateSystem", "models.ZarrStore | None", object]] = (),
    ctx: CreationContext,
) -> "models.CoordinateSystem":
    """Create a shared coordinate system, and author one edge per registered source into it.

    It is created with no owner FK at all, which is exactly what *makes* it SHARED:
    there is no kind to pass because ownership decides it.

    ``registrations`` are ``(source_system, field, spec)`` triples the caller has already
    resolved and scoped; ``spec.transform`` carries the edge's kind and parameters as the
    flat union, or is None for the identity a source that is simply *in* the space states.
    Every edge points source -> space, the direction a placement path walks, and is
    validated by the same :func:`~core.logic.graph.build_registration_edge` the
    transformation mutation uses.
    """
    with transaction.atomic():
        system = models.CoordinateSystem.objects.create(
            name=name,
            epoch=epoch,
            creator=ctx.user,
            organization=ctx.organization,
        )
        graph_logic.create_physical_axes(system, axes)

        for source_system, field, spec in registrations:
            lowered = spec.transform.lower() if spec.transform else IDENTITY_TRANSFORM
            graph_logic.build_registration_edge(
                input_system=source_system,
                output_system=system,
                kind=lowered.kind,
                name=spec.name,
                scale=lowered.scale,
                translation=lowered.translation,
                affine=lowered.affine,
                input_axes=lowered.input_axes,
                output_axes=lowered.output_axes,
                field=field,
                reason=lowered.reason,
                validity=spec.validity,
                ctx=ctx,
            )

    return system


def resolve_source_system(
    *,
    dataset: "models.ArrayDataset | None" = None,
    lens: "models.Lens | None" = None,
    table_dataset: "models.TableDataset | None" = None,
    mesh_collection: "models.MeshCollection | None" = None,
    network_collection: "models.NetworkCollection | None" = None,
    annotation_collection: "models.AnnotationCollection | None" = None,
    coordinate_system: "models.CoordinateSystem | None" = None,
) -> "models.CoordinateSystem":
    """The coordinate system a source is placed by, given the already-fetched owner.

    Exactly one owner must be non-null. A dataset is reached through its intrinsic pixel
    grid, a lens through its own space, a collection through the system it owns, a
    coordinate system directly.

    Shared by registrations and derivations: both name "some container", and the answer to
    "which space stands for it" cannot sensibly differ between them.
    """
    provided = [value for value in (dataset, lens, table_dataset, mesh_collection, network_collection, annotation_collection, coordinate_system) if value is not None]
    if len(provided) != 1:
        raise ValueError("A registration must name exactly one source: a dataset, a lens, a table dataset, a mesh collection, a network collection, an annotation collection, or a coordinate system.")

    if coordinate_system is not None:
        return coordinate_system

    if lens is not None:
        # A whole lens stands in its container's own system, a crop in the space it owns.
        return lens.coordinate_system

    if dataset is not None:
        system = dataset.intrinsic_coordinate_system
        if system is None:
            raise ValueError(f"Dataset '{dataset.name}' has no intrinsic coordinate system to register.")
        return system

    if table_dataset is not None:
        system = table_dataset.coordinate_system_or_none
        if system is None:
            raise ValueError(f"Table dataset '{table_dataset.name}' has no coordinate system to register.")
        return system

    if annotation_collection is not None:
        system = annotation_collection.coordinate_system_or_none
        if system is None:
            raise ValueError(f"Annotation collection '{annotation_collection.name}' has no coordinate system to register.")
        return system

    if network_collection is not None:
        system = getattr(network_collection, "coordinate_system", None)
        if system is None:
            raise ValueError(f"Network collection '{network_collection.label}' has no coordinate system to register.")
        return system

    system = getattr(mesh_collection, "coordinate_system", None)
    if system is None:
        raise ValueError(f"Mesh collection '{mesh_collection.label}' has no coordinate system to register.")
    return system


#: The model each `DerivationSourceKind` names, and the keyword `resolve_source_system`
#: takes it under. The one place the discriminator meets the ORM -- `core.inputs.coords`
#: must not learn about `core.models`, so the lowered member carries an unresolved id and
#: this table is what turns it into a row.
_DERIVATION_SOURCES: dict[str, tuple[type, str]] = {
    enums.DerivationSourceKind.LENS.value: (models.Lens, "lens"),
    enums.DerivationSourceKind.DATASET.value: (models.ArrayDataset, "dataset"),
    enums.DerivationSourceKind.TABLE_DATASET.value: (models.TableDataset, "table_dataset"),
    enums.DerivationSourceKind.MESH_COLLECTION.value: (models.MeshCollection, "mesh_collection"),
    enums.DerivationSourceKind.ANNOTATION_COLLECTION.value: (models.AnnotationCollection, "annotation_collection"),
    enums.DerivationSourceKind.COORDINATE_SYSTEM.value: (models.CoordinateSystem, "coordinate_system"),
    # `write_key_edges` looks identification kinds up here too -- the two vocabularies share
    # their spellings on purpose. NETWORK_COLLECTION is an `IdentificationKind` only:
    # `DerivationSourceKind` has no such member, so this entry is unreachable from a
    # `derivedFrom` and exists for `keyedBy` alone. A network can be a derivation *child*,
    # never a named derivation source.
    enums.IdentificationKind.NETWORK_COLLECTION.value: (models.NetworkCollection, "network_collection"),
}


def source_label(model: type, source) -> str:  # noqa: ANN001 - any container row
    """What to call a source in an edge name or an error, whatever kind it is.

    ``f"{child} <- {source}"`` used to read the source's *dataset* name -- which a table, a
    mesh collection or a bare coordinate system does not have. A collection answers ``label``
    (its name, or its store's key), and reading the wrong attribute is an AttributeError rather
    than a sentence, so the fallback chain lives here and every caller shares it.
    """
    return getattr(source, "name", None) or getattr(source, "label", None) or f"{model.__name__} {source.pk}"


#: The lens kinds that are never a named derivation source: a network is a derivation *child*
#: only, and nothing is computed from a sparse matrix's positions.
_NEVER_A_SOURCE: tuple[type, ...] = (models.NetworkLens, models.SparseLens)


def resolve_derivation_source(info, lowered) -> "tuple[models.CoordinateSystem, str, models.Annotation | None]":  # noqa: ANN001 - kante's Info, and a LoweredDerivation
    """The space a derivation derives *from*, a label for the edge's name, and the shape it names.

    **One path for every spelling.** A source is a lens or a bare space: an entry naming a
    container names that container's whole lens, so ``{DATASET: d}`` and ``{LENS: d's whole
    lens}`` resolve to one and the same thing and are deduplicated as such, and the rule that a
    network or a sparse dataset is never a named source is applied to the resolved lens,
    whichever arm named it. Org-scoped through ``get_for_org`` like every other id a client
    sends, and returned with a human label because ``f"{child} <- {source}"`` used to read
    the source's *dataset* name -- which a table, a mesh collection or a bare coordinate
    system does not have.

    **A shape is its collection's space, and which shape.** ANNOTATION resolves exactly as
    ANNOTATION_COLLECTION on the shape's collection does -- an annotation owns no space, it
    is drawn in its collection's -- and hands the annotation back beside it, for the edge to
    carry. It is the only arm whose third member is not None, and deliberately not an entry
    of ``_DERIVATION_SOURCES``: that table is also what a ``keyedBy`` reads, and a shape
    keys nothing.
    """
    if lowered.source_kind == enums.DerivationSourceKind.ANNOTATION.value:
        annotation = cast(models.Annotation, get_for_org(models.Annotation, info, id=lowered.source_id))
        collection = annotation.collection
        return whole_lens(collection).coordinate_system, f"{source_label(models.AnnotationCollection, collection)} / {annotation.name or annotation.pk}", annotation
    model, keyword = _DERIVATION_SOURCES[lowered.source_kind]
    source = get_for_org(model, info, id=lowered.source_id)
    if keyword == "coordinate_system":
        return source, source_label(model, source), None
    lens = source if keyword == "lens" else whole_lens(source)
    if isinstance(lens, _NEVER_A_SOURCE):
        raise ValueError(f"Lens {lens.pk} is a {type(lens).KIND.lower()} lens; a {type(lens.container).__name__} is never a named derivation source -- it is derived, not derived from.")
    return lens.coordinate_system, source_label(type(lens.container), lens.container), None

def write_derivation_edges(info, *, name: str, own_system: "models.CoordinateSystem", derived_from: Sequence, ctx: CreationContext) -> list["models.Transformation"]:
    """Write one edge per source this data was computed from, child space -> source space.

    The one writer for every container. An array dataset, a table, a mesh collection and an
    annotation collection are all saying the same sentence -- *my space, and how it relates
    to the one I came from* -- and each used to say it in its own code: the dataset through
    lenses only, the three collections through a bare coordinate system, none of them able
    to name the other kinds.

    **The order is the priority, and the first entry is the primary parent.** Written in
    input order so pk order *is* the creator's declared priority, which is the rule
    ``primary_derivation_edge`` and the placement walks act on. A mappable entry hiding
    behind an UNMAPPABLE first entry would silently break that -- the walks refuse the
    primary while a workable parent sits behind it -- so that ordering is rejected before
    anything is written.

    Everything is resolved before anything is written, for the same reason: a mistyped
    transform on the third entry must not leave the first two behind as a half-recorded
    lineage.
    """
    if not derived_from:
        return []

    lowered = [entry.lower() for entry in derived_from]
    unmappable = enums.TransformKind.UNMAPPABLE.value
    if lowered[0].transform.kind == unmappable and any(low.transform.kind != unmappable for low in lowered):
        raise ValueError(
            "The first derivedFrom entry is the primary parent -- the one that places this data -- so it cannot be UNMAPPABLE while a mappable entry follows. "
            "An entry with no `transform` *is* UNMAPPABLE: naming a source claims no geometry. Put the mappable source first, or state its transform"
        )
    sources = [resolve_derivation_source(info, low) for low in lowered]
    # Deduplicated on what was *resolved*, not on how it was spelled: `{DATASET: d}` and
    # `{LENS: d's whole lens}` are one source, and two entries landing in one space would
    # write two edges into it.
    #
    # A shape is the exception that is not one: two shapes of one collection land in one
    # space and are two sources, so the shape is part of what was resolved. A collection
    # named beside one of its own shapes still collides -- the shape already says the
    # collection, and the bare entry would add an edge that names nothing.
    systems = [system.pk for system, _, _ in sources]
    shapes = [annotation.pk if annotation is not None else None for _, _, annotation in sources]
    resolved = list(zip(systems, shapes))

    def collides(index: int) -> bool:
        system, shape = resolved[index]
        return any(other != index and system == other_system and (shape is None or other_shape is None or shape == other_shape) for other, (other_system, other_shape) in enumerate(resolved))

    duplicates = sorted({label for index, (_, label, _) in enumerate(sources) if collides(index)})
    if duplicates:
        raise ValueError(f"Each derivedFrom entry must name a distinct source, but {', '.join(duplicates)} appear{'s' if len(duplicates) == 1 else ''} more than once (a container and its whole lens are one source). One entry per source: its transform already says everything about how the data maps back")
    fields = [get_for_org(models.CoordinateSystem, info, id=low.transform.field) if low.transform.field else None for low in lowered]

    edges: list[models.Transformation] = []
    with transaction.atomic():
        for low, (source_system, label, annotation), field in zip(lowered, sources, fields):
            transform = low.transform
            edges.append(
                graph_logic.write_relation_edge(
                    name=f"{name} <- {label}",
                    input_system=own_system,
                    output_system=source_system,
                    kind=transform.kind,
                    scale=transform.scale,
                    translation=transform.translation,
                    affine=transform.affine,
                    input_axes=transform.input_axes,
                    output_axes=transform.output_axes,
                    field=field,
                    reason=transform.reason,
                    value_relation=low.value_relation,
                    annotation=annotation,
                    ctx=ctx,
                )
            )
    return edges


def write_key_edges(info, *, name: str, own_system: "models.CoordinateSystem", keyed_by: Sequence, ctx: CreationContext, produces: Sequence[str] | None = None) -> list["models.Transformation"]:  # noqa: ANN001 - kante's Info, and a list of KeyedByInput members
    """Write one FIELD edge per source keying this table, source space -> table space.

    The sibling of :func:`write_derivation_edges`, and deliberately not folded into it: a
    derivation runs child -> source, and this runs the other way. Both sentences are true
    of the same pair -- the table was computed from the mask, *and* the mask's pixels index
    into the table -- and they are two edges because they are two directions. A FIELD has
    no closed-form inverse, so neither can stand in for the other, and only the source ->
    table one is an edge ``attributePlans`` can find: it looks for FIELD edges *landing on*
    a table.

    **Two kinds of source, one relation.** A label mask is the case where the array being
    mapped is the array doing the mapping, so the mask's own grid is both the edge's input
    and its field. A mesh collection is the same sentence over a different substrate: the
    ids ride on its geometry rows rather than in pixels, so its own vertex space is input
    and field alike. What both share -- and what earns a FIELD its place as an edge -- is
    that standing somewhere in the source's space yields an id. A relation that needs a
    *row* first is not this; it is ``Column.references``.
    :func:`core.logic.graph.build_registration_edge` stores that self-field as NULL, which
    is what keeps a dereferenced source deletable.

    **The axis split is derived, not stated.** The rank rule
    (:func:`core.logic.graph.assert_edge_rank`) says the axes a FIELD does not consume pass
    through by name, and that leaves exactly one split for a given pair of systems::

        consumed = source axes - table axes
        produced = table axes - source axes

    so a ``(t, y, x)`` mask keying a ``(t, instance)`` table consumes ``(y, x)``, produces
    ``instance`` and passes ``t`` through, with no caller having had to work it out. The
    same rule reads a collection correctly without a special case: a ``(z, y, x)`` mesh
    keying an ``(object)`` table consumes all three -- it shares no axis with the table, so
    nothing passes through -- while a per-frame ``(t, y, x)`` one keying ``(t, object)``
    consumes ``(y, x)`` and passes ``t``. Asking a caller for the split would only be an
    opportunity to state it wrong -- and a FIELD whose axes are wrong is not refused at
    read, it is silently skipped, because a plan is discovered by the shape of its edge
    rather than looked up by name.

    ``produces``, when given, is the axis each entry's caller *said* the source keys, one per
    entry and in the same order. It is checked against the derivation above rather than replacing
    it: a sparse dataset carries identification on the axis, so the caller already knows which one
    a mask supplies and saying so lets the refusal name both halves -- "you said the mask keys
    `gene`" instead of "one place holds one id". Tables pass nothing and derive as they always did,
    which is the right default: asking a caller for `consumed` would be asking them to restate the
    two systems' axes at each other, and that is only an opportunity to state it wrong.

    Everything is resolved before anything is written, for the same reason
    :func:`write_derivation_edges` does it: a bad second entry must not leave the first
    behind as a half-written dereference.
    """
    if not keyed_by:
        return []

    resolved = []
    for entry in keyed_by:
        kind = entry.kind.value if hasattr(entry.kind, "value") else entry.kind
        try:
            model, keyword = _DERIVATION_SOURCES[kind]
        except KeyError:
            # A bare lookup here was a 500. It is reachable only through a bug -- every
            # caller filters on `AUTHORS_EDGE` first -- but the kinds that key and the
            # kinds that identify are two overlapping vocabularies, and the one that is
            # in both under a *different* spelling is the table: `TABLE_DATASET` here,
            # `TABLE` there. A refusal that says why beats a traceback that says KeyError.
            raise ValueError(
                f"'{kind}' cannot key '{name}'. A source keys by having contents that *are* the ids -- a mask's pixel "
                f"values, a collection's geometry -- which is a claim about space, and therefore an edge. A table is "
                f"already in record-land: an axis whose positions are a table's rows states a foreign key instead, and "
                f"authors no edge. Keyable kinds are {', '.join(sorted(_DERIVATION_SOURCES))}."
            ) from None
        source = get_for_org(model, info, id=entry.source_id)
        resolved.append((entry, source_label(model, source), resolve_source_system(**{keyword: source})))

    named = [system.pk for _, _, system in resolved]
    duplicates = sorted({label for (_, label, system) in resolved if named.count(system.pk) > 1})
    if duplicates:
        raise ValueError(f"Each keyedBy entry must name a distinct source, but {', '.join(duplicates)} appears more than once. A second edge between the same pair says nothing the first did not")

    table_axes = [axis.name for axis in own_system.axes.all()]
    own_axes = set(table_axes)
    # Hoisted out of the per-entry loop below: two ORM traversals, and the answer is a
    # property of `own_system` alone -- nothing in the loop touches it. It was recomputed
    # once per keying source.
    identified = graph_logic.identified_axes(own_system)

    stated = list(produces) if produces is not None else [None] * len(resolved)
    if len(stated) != len(resolved):
        raise ValueError(f"'{name}' names {len(resolved)} keying sources but {len(stated)} produced axes; they are one per entry, in the same order")

    edges: list[models.Transformation] = []
    with transaction.atomic():
        for (entry, label, source_system), wanted in zip(resolved, stated):
            source_axes = [axis.name for axis in source_system.axes.all()]
            supplied = set(source_axes)
            # Axes the table identifies itself are accounted for without the source supplying
            # them, so they are neither consumed nor produced -- they are the product-space
            # half of a table indexed by a pair. One definition, shared with the rank check;
            # computed once above, since it depends on `own_system` and nothing else.
            consumed = [axis for axis in source_axes if axis not in own_axes]
            produced = [axis for axis in table_axes if axis not in supplied and axis not in identified]

            if not consumed:
                raise ValueError(
                    f"'{label}' cannot key '{name}': its axes {source_axes} are all axes of the table {table_axes} as well, so the edge would consume nothing and there is no map. "
                    "A source keys a table by collapsing some of its axes into an id the table is indexed by; the axes the two share pass through instead"
                )
            if wanted is not None and produced != [wanted] and len(produced) <= 1:
                # The caller named the axis and the derivation disagrees. Checked before
                # "produces nothing" below because it is the more specific failure -- that is
                # what this looks like from the derivation's side, and says nothing about which
                # axis the caller meant. Not before the *two ids* refusal, though: `len(produced)
                # > 1` is a fact about the table's shape and has prose of its own that took real
                # argument to write, and now that the table path states `produces` too, this
                # branch would otherwise shadow it on every product-space mistake.
                because = (
                    f"'{label}' has an axis of that name too, so '{wanted}' passes through rather than being supplied"
                    if wanted in supplied
                    else f"the axes it does supply are {produced}"
                )
                raise ValueError(
                    f"'{label}' was declared to key '{wanted}' of '{name}', but the axes say otherwise -- {because}. "
                    f"'{label}' spans {source_axes} and '{name}' spans {table_axes}; a source supplies the axes the target has and it does not."
                )
            if not produced:
                raise ValueError(
                    f"'{label}' cannot key '{name}': the table's axes {table_axes} are all axes of '{label}' {source_axes} as well, so the edge would produce nothing. "
                    "The table needs a coordinate the source's ids supply -- an INDEX column of object ids"
                )
            # One place holds one id, whether it is a pixel or a surface, so one source
            # supplies one id. `assert_field_produces` refuses this too, but from the
            # field's side -- it reads as though the source were at fault and suggests
            # giving it a value axis, which turns a label mask into a warp field and is not
            # what anyone keying a table wants. The table's second id column is the thing to
            # fix, so say that instead.
            if len(produced) > 1:
                raise ValueError(
                    f"'{label}' cannot key '{name}': one place holds one id, so a source supplies one, but the table has {produced} that '{label}' has no axis for and would need it to supply {len(produced)}. "
                    "Every axis a source does not produce has to be one it shares with the table, which passes through by name, or one the table identifies itself. "
                    "Three shapes do work, and they say different things: declare the second id as a data column identified by the other table (`identifiedBy: [{kind: TABLE, table: ...}]`), when it is an attribute *of* a row; "
                    "when a row is identified by the *pair*, keep it an axis -- `axisType: INDEX` with the same TABLE identification -- which says its positions are that table's rows and leaves this edge only one id to supply; "
                    "or, when the second id is a node of a network collection an object axis already keys, declare it INDEX with a NETWORK_COLLECTION_NODES identification, which scopes it by that axis and again leaves this edge one id"
                )

            edges.append(
                graph_logic.build_registration_edge(
                    input_system=source_system,
                    output_system=own_system,
                    kind=enums.TransformKind.FIELD.value,
                    name=entry.name or f"{label} -> {name}",
                    input_axes=consumed,
                    output_axes=produced,
                    field=source_system,
                    validity=entry.validity,
                    ctx=ctx,
                )
            )
    return edges


# The scene's world space, when the caller does not author one. A scene is
# spatio-temporal by default: microscopy data is a timelapse more often than not, and
# a world with nowhere to put time forces every temporal dataset to either drop its t
# axis at the registration or invent a scene-specific convention for it.
#
# Time first, then z/y/x in array order. Nothing requires that order any more, but it is
# still the right default: array order means the world composes with a dataset's intrinsic
# axes without a permutation, and the spatial suffix is what `resolve_render_axes` reads.
#
# Seconds, not a frame index: world is a *calibrated* space, and `t` here is a
# duration from the space's origin. The world system's `epoch` anchors that origin to
# wall-clock when it is known.
DEFAULT_WORLD_AXES = [
    PhysicalAxisInputModel(name="t", type=enums.AxisType.TIME, unit="second"),
    PhysicalAxisInputModel(name="z", type=enums.AxisType.SPACE, unit="micrometer"),
    PhysicalAxisInputModel(name="y", type=enums.AxisType.SPACE, unit="micrometer"),
    PhysicalAxisInputModel(name="x", type=enums.AxisType.SPACE, unit="micrometer"),
]

#: The axis types a world has a slider for. A CHANNEL axis is something a layer
#: *samples* (each position its own render node), and a MICROTIME or SPECTRUM axis is
#: something a render node *reduces* -- neither is a place, so neither belongs to a
#: shared space two datasets are registered into.
NAVIGABLE_TYPES = (enums.AxisTypeChoices.TIME.value, enums.AxisTypeChoices.SPACE.value)

def _assert_lens_placeable(dataset: "models.ArrayDataset") -> "models.CoordinateSystem":
    """The intrinsic system a lens over this dataset is measured against, or a refusal in prose."""
    intrinsic = dataset.intrinsic_coordinate_system
    if intrinsic is None:
        raise ValueError(f"Dataset {dataset.pk} has no intrinsic coordinate system")
    if dataset.data_arrays.order_by("level").first() is None:
        raise ValueError(f"Dataset {dataset.pk} has no level-0 data array to place the lens against")
    return intrinsic


def lens_kind_for(container: object) -> "type[models.Lens]":
    """The lens model that selects over this container, or a refusal naming what can be selected over."""
    kind = models.Lens.KIND_BY_CONTAINER.get(type(container))
    if kind is None:
        raise ValueError(f"A {type(container).__name__} is not something a lens selects over; a lens selects over one of: {', '.join(sorted(model.__name__ for model in models.Lens.KIND_BY_CONTAINER))}")
    return kind


def _get_or_create(kind: "type[models.Lens]", *, container: object, selection: list, make: "Callable[[], models.Lens]") -> "models.Lens":
    """The one row for (container, selection), making it when it is not there.

    The database holds the identity (a unique constraint per kind), so two requests
    minting the same selection at once resolve by one of them losing the race and reading
    what the other wrote: no lock, and no second row.
    """
    field, column = kind.CONTAINER_FIELD, "slices" if kind is models.ArrayLens else "windows"
    lookup = {field: container, column: selection}
    existing = kind.objects.filter(**lookup).first()
    if existing is not None:
        setattr(existing, field, container)
        return existing
    try:
        with transaction.atomic():
            return make()
    except IntegrityError:
        return kind.objects.get(**lookup)


def _mint(kind: "type[models.Lens]", **fields: object) -> "models.Lens":
    """Write a lens row with the spec of what it selects: one insert, the spec computed from the unsaved row.

    Every lens is made here, so every lens states what it is from its first moment. The
    spec is a function of the container and the selection, both in hand before the row is.
    """
    lens = kind(**fields)
    lens.stored_spec = [member.value for member in lens.derive_spec()]
    lens.stored_descriptors = descriptors_logic.stored_lens_descriptors(lens)
    lens.save()
    return lens


def whole_lens(container: object) -> "models.Lens":
    """The lens selecting all of a container: the one row that exists for it, made if it does not.

    Every container has this lens from creation (each `create*` mutation ends with it), and
    every lens mutation reaches its container's row through here, so the lookup is the common
    path and the create branch the rare one: a container whose row is missing -- a canonical
    spelling that changed under it, or a row removed by hand -- gets it on first use, as
    ``merge_duplicate_lenses`` gives it to every such container in one pass.

    It takes no creation context because nothing is minted beside the row: a whole lens
    selects everything, so its space *is* the container's, and it points at that node rather
    than owning a second one joined by an identity edge. Its provenance is the container's --
    it is the container's handle, made with the container by whoever made that, and minted
    for an older container by the setup job or by the first layer over it, neither of whom
    made the selection.
    """
    kind = lens_kind_for(container)
    if isinstance(container, models.ArrayDataset):
        system = _assert_lens_placeable(container)
    else:
        system = getattr(container, "coordinate_system", None)
        if system is None:
            raise ValueError(f"{type(container).__name__} {container.pk} has no coordinate system, so there is no space to select in")  # type: ignore[attr-defined]

    def make() -> "models.Lens":
        return _mint(
            kind,
            **{kind.CONTAINER_FIELD: container},
            coordinate_system=system,
            organization=container.organization,  # type: ignore[attr-defined]
            **_container_provenance(container),
        )

    return _get_or_create(kind, container=container, selection=[], make=make)


def _container_provenance(container: object) -> dict[str, int | None]:
    """A container's creator and task, as the ids a whole lens is stamped with. A mesh or network collection carries no task."""
    return {
        "creator_id": getattr(container, "creator_id", None),
        "created_through_id": getattr(container, "created_through_id", None),
        "created_through_by_id": getattr(container, "created_through_by_id", None),
    }


def create_windowed_lens(container: object, windows: list, ctx: CreationContext) -> "models.Lens":
    """The lens keeping these windows of a container that is not an array: the one that exists, or a new one.

    A window re-indexes nothing, so the lens lives in the container's own system and no edge
    is written; what is stored is the selection, in its one normalized spelling
    (:func:`core.logic.coords.normalize_windows`). Windows that keep everything are
    :func:`whole_lens`. A new row is stamped with ``ctx``: the first to ask for a selection is
    its creator, and asking again hands back the row unchanged.
    """
    kind = lens_kind_for(container)
    if kind is models.ArrayLens:
        raise ValueError("An array dataset is selected over by slices (`createLens`), not by windows")
    system = getattr(container, "coordinate_system", None)
    if system is None:
        raise ValueError(f"{type(container).__name__} {container.pk} has no coordinate system, so there is no space to select in")  # type: ignore[attr-defined]
    normalized = coords_logic.normalize_windows([axis.name for axis in system.axes.all()], windows)
    if not normalized:
        return whole_lens(container)

    def make() -> "models.Lens":
        return _mint(
            kind,
            **{kind.CONTAINER_FIELD: container},
            coordinate_system=system,
            organization=container.organization,  # type: ignore[attr-defined]
            windows=normalized,
            creator=ctx.user,
            **ctx.provenance_kwargs(),
        )

    return _get_or_create(kind, container=container, selection=normalized, make=make)


def create_lens(
    dataset: "models.ArrayDataset",
    slices: list,
    ctx: CreationContext,
) -> "models.ArrayLens":
    """The array lens making this selection over this dataset: the one that exists, or a new one.

    A lens is a deterministic function of what it selects -- one row per (dataset, normalized
    slices) -- so asking twice for the same voxels hands back the same id, and two scenes, two
    charts or two action calls over one selection agree about its identity. The slices are
    resolved by :func:`core.logic.coords.normalize_slices` first, so every spelling of a
    selection (negative bounds, open bounds, a stop past the end) is the same row; a selection
    that keeps the whole array is :func:`whole_lens`.

    The lens' shape and axes are not written: they follow from the dataset and the slices,
    and a second copy could only drift from the first. Its spec is (`_mint`), because a
    filter reads it. A *sliced* lens shifts voxel
    coordinates, which is a real fact, so it owns a coordinate system and the derived edge
    recording the shift (:func:`core.logic.graph.create_lens_edge`), both made here with the
    row. Lenses are immutable, so the decision is final at creation.
    """
    intrinsic = _assert_lens_placeable(dataset)
    normalized = coords_logic.normalize_slices(dataset.shape_list, dataset.axis_names, slices)
    if not normalized:
        return cast(models.ArrayLens, whole_lens(dataset))

    def make() -> "models.Lens":
        # The space before the lens, so there is one write each.
        lens_system = models.CoordinateSystem.objects.create(
            name=f"{dataset.name}/lens",
            creator=ctx.user,
            organization=ctx.organization,
        )
        lens = _mint(
            models.ArrayLens,
            dataset=dataset,
            coordinate_system=lens_system,
            organization=dataset.organization,
            slices=normalized,
            creator=ctx.user,
            **ctx.provenance_kwargs(),
        )

        # A lens sees the same axes as the array it slices; only the extent changes.
        graph_logic.create_pixel_axes(lens_system, dataset.axes)

        # Without this edge, slicing shifts voxel coordinates and nothing records the
        # shift: an ROI drawn on a cropped lens has no defined path back to its dataset.
        # The parent is the intrinsic system: it IS the level-0 voxel space.
        graph_logic.create_lens_edge(
            lens_system=lens_system,
            parent_system=intrinsic,
            dataset_axis_names=dataset.axis_names,
            slices=lens.slices_list,
            ctx=ctx,
        )
        return lens

    return cast(models.ArrayLens, _get_or_create(models.ArrayLens, container=dataset, selection=normalized, make=make))


def assert_lens_deletable(lens: "models.Lens") -> None:
    """Refuse to delete a lens something still looks through, and a container's whole lens.

    The PROTECT half of ``deleteLens``. ``Layer.lens`` and ``ChartLayer.lens`` cascade, and a
    lens is shared -- every scene over one selection holds the same row -- so deleting one
    would take layers out of scenes the caller never named. The whole lens is the handle an
    action is handed for the container itself; it goes when the container goes, not before.
    """
    if lens.is_whole:
        container = lens.container
        raise ValueError(f"Lens {lens.pk} selects the whole of {type(container).__name__} {container.pk} and cannot be deleted on its own: it is the container's handle, and goes with the container.")

    drawing = layers_drawing_through(lens)
    layers = list(drawing.select_related("scene").order_by("pk")[:5])
    chart_layers = list(lens.chart_layers.select_related("chart").order_by("pk")[:5])
    if not layers and not chart_layers:
        return

    total = drawing.count() + lens.chart_layers.count()
    described = [f"layer {layer.pk} in scene '{layer.scene.name}'" for layer in layers] + [f"chart layer {layer.pk} in chart '{layer.chart.name}'" for layer in chart_layers]
    more = f" (and {total - len(described)} more)" if total > len(described) else ""
    raise ValueError(
        f"Lens {lens.pk} cannot be deleted: {total} layer(s) draw through it -- {', '.join(described)}{more}. Delete those layers first, or leave the lens; it is shared by every "
        "composition over this selection. Deleting the container it selects over is the one thing that takes every layer over it with it."
    )


def lens_ids_within(lens: "models.Lens") -> list[int]:
    """The ids of every lens of the same container whose selection lies inside this one, this one included.

    What "drawn in a scene" means for a lens once a channel is itself a lens: a layer over
    channel 2 of a crop draws the crop, and a layer over any crop draws the whole lens. Decided
    on the selections (:func:`core.logic.coords.lens_within`, :func:`core.logic.coords.windows_within`),
    since a lens is one row per selection and keeps no tree.
    """
    lens = lens.get_real_instance()
    if isinstance(lens, models.ArrayLens):
        dataset = lens.dataset
        shape, names = dataset.shape_list, dataset.axis_names
        return [
            candidate.pk
            for candidate in models.ArrayLens.objects.filter(dataset_id=lens.dataset_id).order_by("pk")
            if coords_logic.lens_within(shape, names, lens.slices_list, candidate.slices_list)
        ]
    kind = type(lens)
    siblings = kind.objects.filter(**{f"{kind.CONTAINER_FIELD}_id": lens.container_id}).order_by("pk")
    return [candidate.pk for candidate in siblings if coords_logic.windows_within(lens.windows_list, candidate.windows_list)]  # type: ignore[attr-defined]


def layers_drawing_through(lens: "models.Lens") -> "QuerySet[models.Layer]":
    """Every layer whose picture this lens is part of: its own; for an array lens those naming it as an RGB component or a render-graph source; for a sparse lens those whose pickers read its slice."""
    query = Q(lens=lens)
    if isinstance(lens, models.ArrayLens):
        query |= Q(red_lens=lens) | Q(green_lens=lens) | Q(blue_lens=lens) | Q(channel_lenses=lens)
    if isinstance(lens, models.SparseLens):
        # A colour or filter picker names the slice it reads by id inside a JSON document; the
        # guard is the PROTECT a foreign key would be.
        query |= pickers_logic.names_sparse_lens(str(lens.pk))
    return models.Layer.objects.filter(query).distinct()


def lens_own_edges(lens: "models.ArrayLens") -> "Iterable[models.Transformation]":
    """The top-level edge(s) from a sliced array lens' own system into its dataset's intrinsic system; none for a whole lens."""
    if lens.coordinate_system_id == lens.dataset.coordinate_system_id:
        return ()
    return models.Transformation.objects.filter(input_id=lens.coordinate_system_id, output_id=lens.dataset.coordinate_system_id)


def lens_space_is_shared_because(lens: "models.ArrayLens") -> str | None:
    """Why a sliced array lens' own system cannot go with it, or None when it can.

    The same three questions ``deleteCoordinateSystem`` asks -- residents, compositions,
    edges -- plus the two PROTECT relations a delete would trip over. A crop's space is its
    own until someone builds on it: registers it elsewhere, roots a scene at it, draws a
    collection whose boxes are stated in it. Then the space outlives the crop, as a bare
    space with the edge it had.
    """
    system = lens.coordinate_system
    if system.pk == lens.dataset.coordinate_system_id:
        return "it lives in the intrinsic system"
    for relation in graph_logic.RESIDENT_RELATIONS:
        residents = getattr(system, relation).all()
        if relation == "lenses":
            residents = residents.exclude(pk=lens.pk)
        if residents.exists():
            return f"other {relation.replace('_', ' ')} live in its system"
    try:
        compositions_logic.assert_no_composition_over(system)
    except ValueError:
        return "a scene or chart is laid out over its system"
    own = [edge.pk for edge in lens_own_edges(lens)]
    if models.Transformation.objects.filter(Q(input=system) | Q(output=system)).exclude(pk__in=own).exists():
        return "its system has registration edges besides its own"
    if system.fields_of.exists():
        return "its system is the field of a transformation"
    if system.annotation_bbox_frames.exists():
        return "annotation boxes are expressed in its system"
    return None


def delete_lens(lens: "models.Lens") -> str | None:
    """Delete a lens; a sliced array lens' own system and edge go with it unless something else is attached.

    Returns why the space was left behind, or None when nothing was. The lens row goes first
    -- its system is PROTECTed by it -- then the edges in and out of the system, then the
    system; its axes cascade. A windowed lens and a whole lens own nothing and simply go.
    """
    sliced = isinstance(lens, models.ArrayLens) and lens.coordinate_system_id != lens.dataset.coordinate_system_id
    reason = lens_space_is_shared_because(lens) if sliced else None
    system = lens.coordinate_system if sliced and reason is None else None
    lens.delete()
    if system is not None:
        models.Transformation.objects.filter(Q(input=system) | Q(output=system)).delete()
        system.delete()
    return reason


def create_sub_lens(parent: "models.ArrayLens", slices: list, ctx: CreationContext) -> "models.ArrayLens":
    """A crop of a crop: ``slices`` stated in the parent lens' own index space, as the dataset's lens that selects those voxels.

    Composed through the parent's slices (:func:`core.logic.coords.compose_slices`) and then
    made as any other lens of the dataset, so identity falls out: a crop of a crop that
    selects what an existing crop selects *is* that crop. Over a whole lens the composition
    is the identity, which is how every ``createLens`` goes through here.
    """
    dataset = parent.dataset
    composed = coords_logic.compose_slices(dataset.shape_list, dataset.axis_names, parent.slices_list, slices)
    return create_lens(dataset, [base_models.SliceInputModel(**entry) for entry in composed], ctx)


def create_sub_windowed_lens(parent: "models.Lens", windows: list, ctx: CreationContext) -> "models.Lens":
    """A window of a window: the tighter of the two bounds per axis, as the container's lens keeping them (:func:`core.logic.coords.intersect_windows`)."""
    narrowed = coords_logic.intersect_windows(parent.windows_list, windows)  # type: ignore[attr-defined]
    return create_windowed_lens(parent.container, [base_models.WindowInputModel(**entry) for entry in narrowed], ctx)


def _box_to_dataset_slices(dataset: "models.ArrayDataset", system: "models.CoordinateSystem", bounds: dict[str, list[float]], *, pins: dict[str, int] | None = None) -> list[dict[str, int | str]]:
    """A box in ``system``, as the dataset's slices: composed into its pixel grid along the one placement path, then cut on the half-open voxel rule.

    The same walk a layer's `asAffine` takes (:class:`core.logic.placement.PlacementGraph`),
    rooted at the dataset's grid and seeded with the box's system, so a box stated in a
    sibling dataset's grid is reached through the world both are registered into, walking
    that sibling's edge backwards. An axis of the grid the map does not constrain is kept
    whole, unless ``pins`` names it.
    """
    intrinsic = _assert_lens_placeable(dataset)
    names = [axis.name for axis in system.axes.all()]
    unknown = [axis for axis in bounds if axis not in names]
    if unknown:
        raise ValueError(f"The box names axes {unknown} that coordinate system '{system.name}' does not have; its axes are {names}")
    mins = [bounds[axis][0] if axis in bounds else -math.inf for axis in names]
    maxs = [bounds[axis][1] if axis in bounds else math.inf for axis in names]
    if system.pk == intrinsic.pk:
        forms = {axis: coords_logic.AxedForm(coefficients=tuple(1.0 if index == column else 0.0 for column in range(len(names))), constant=0.0) for index, axis in enumerate(names)}
        source_axes = names
    else:
        graph_logic.assert_placeable_in(intrinsic, system, destination=f"the pixel grid of dataset '{dataset.name}'")
        condensed = placement_logic.PlacementGraph(intrinsic, seed_systems=[system.pk]).condensed(system)
        if condensed is None:
            raise ValueError(f"Coordinate system '{system.name}' has no placement path into the pixel grid of dataset '{dataset.name}', so a box stated in it cannot be cut from the dataset")
        by_axis = dict(zip(names, zip(mins, maxs)))
        source_axes = list(condensed.input_axes)
        mins = [by_axis[axis][0] if axis in by_axis else -math.inf for axis in source_axes]
        maxs = [by_axis[axis][1] if axis in by_axis else math.inf for axis in source_axes]
        forms = coords_logic._forms_from_matrix(condensed.matrix, condensed.output_axes, len(source_axes))
    return coords_logic.box_to_slices(forms, source_axes, mins, maxs, dataset.axis_names, dataset.shape_list, pins=pins)


def create_lens_from_box(dataset: "models.ArrayDataset", system: "models.CoordinateSystem", windows: list, ctx: CreationContext) -> "models.ArrayLens":
    """The dataset's lens for the voxels under a box stated in another coordinate system -- micrometres in a physical space, a sibling's grid through a shared world."""
    normalized = coords_logic.normalize_windows([axis.name for axis in system.axes.all()], windows)
    bounds = coords_logic.window_bounds([base_models.WindowInputModel(**entry) for entry in normalized])
    slices = _box_to_dataset_slices(dataset, system, bounds)
    return create_lens(dataset, [base_models.SliceInputModel(**entry) for entry in slices], ctx)


def create_lens_from_annotation(dataset: "models.ArrayDataset", annotation: "models.Annotation", ctx: CreationContext, *, within: "models.ArrayLens | None" = None) -> "models.ArrayLens":
    """The dataset's lens that bounds an annotation: the voxels under the shape's stored box, in the frame the collection states it in.

    ``within`` is a lens of the same dataset to cut inside of: the result is then what that
    lens selects *under* the box (:func:`core.logic.coords.slices_under`) -- a channel it
    pins stays pinned, a range it keeps is intersected with the box, and a shape that misses
    it is refused. The box itself is still stated in the shape's own frame, never relative
    to the crop; only the answer is narrowed. This is the crop a viewer asks for: the shape
    it clicked, on the lens the layer under it draws.

    ``intrinsicBbox`` is already half a voxel padded, so a rectangle on rows 1..4 is the box
    ``[0.5, 4.5]`` and cuts exactly rows 1 to 4. A pin on an axis the drawing space lacks
    ("on channel 1" of the image under a (y, x) tracing) keeps that one position.

    A shape may have fewer components than its frame has axes -- a rectangle on a (c, y, x)
    image -- and its box is then read as :func:`core.logic.coords.annotation_box` reads its
    vertices: over the axes it does not pin, or the trailing ones. An axis of the frame it
    pins is that one plane, and one it says nothing about is kept whole.
    """
    stored = annotation.intrinsic_bbox
    system = annotation.collection.effective_bbox_system
    if not stored or system is None:
        raise ValueError(f"Annotation {annotation.pk} has no geometry to cut a lens from: its collection states no box for it")
    names = [axis.name for axis in system.axes.all()]
    pinned = annotation.coordinates or {}
    rank = len(stored["min"])
    unpinned = [axis for axis in names if axis not in pinned]
    spanned = unpinned if len(unpinned) == rank else names[max(len(names) - rank, 0) :]
    bounds = {axis: [float(low), float(high)] for axis, low, high in zip(spanned, stored["min"], stored["max"])}
    bounds.update({axis: [float(pinned[axis]), float(pinned[axis])] for axis in names if axis in pinned and axis not in spanned})
    pins = {axis: int(value) for axis, value in pinned.items() if axis not in names and axis in dataset.axis_names}
    slices = _box_to_dataset_slices(dataset, system, bounds, pins=pins)
    if within is not None:
        slices = coords_logic.slices_under(dataset.shape_list, dataset.axis_names, within.slices_list, [base_models.SliceInputModel(**entry) for entry in slices])
    lens = create_lens(dataset, [base_models.SliceInputModel(**entry) for entry in slices], ctx)
    # The cut is recorded beside the lens, never on it: the lens is the dataset's row for
    # these voxels, whoever asked and however, and this is the one place that knows a shape
    # did. Get-or-create, as the lens is: cutting twice is one cut, and its first asker's.
    models.LensCut.objects.get_or_create(annotation=annotation, lens=lens, defaults=dict(creator=ctx.user, **ctx.provenance_kwargs()))
    return lens
