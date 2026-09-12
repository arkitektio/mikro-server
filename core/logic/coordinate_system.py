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
from collections.abc import Sequence

from django.db import transaction

from core import enums, models
from core.creation import CreationContext
from core.inputs.coords import IDENTITY_TRANSFORM, PhysicalAxisInputModel
from core.logic import graph as graph_logic
from core.scoping import get_for_org


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
        # An unsliced lens owns no system -- its space *is* the dataset's intrinsic space --
        # so a derivation from it is a derivation from intrinsic, one hop shorter.
        system = graph_logic.lens_source_system(lens)
        if system is None:
            raise ValueError(f"Lens {lens.pk} has no coordinate system, so there is no space to derive from.")
        return system

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
            raise ValueError(f"Network collection '{network_collection.version}' has no coordinate system to register.")
        return system

    system = getattr(mesh_collection, "coordinate_system", None)
    if system is None:
        # `version`, not `name`: a MeshCollection has no name field, and reading one here
        # raised an AttributeError -- a 500 -- instead of this sentence.
        raise ValueError(f"Mesh collection '{mesh_collection.version}' has no coordinate system to register.")
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
    mesh collection or a bare coordinate system does not have. A ``MeshCollection`` carries
    ``version`` instead of ``name``, and reading the wrong one is an AttributeError rather
    than a sentence, so the fallback chain lives here and every caller shares it.
    """
    return getattr(source, "name", None) or getattr(source, "version", None) or f"{model.__name__} {source.pk}"


def resolve_derivation_source(info, lowered) -> "tuple[models.CoordinateSystem, str]":  # noqa: ANN001 - kante's Info, and a LoweredDerivation
    """The space a derivation derives *from*, and a label for the edge's name.

    Org-scoped through ``get_for_org`` like every other id a client sends, and returned
    with a human label because ``f"{child} <- {source}"`` used to read the source's
    *dataset* name -- which a table, a mesh collection or a bare coordinate system does
    not have.
    """
    model, keyword = _DERIVATION_SOURCES[lowered.source_kind]
    source = get_for_org(model, info, id=lowered.source_id)
    system = resolve_source_system(**{keyword: source})
    return system, source_label(model, source)


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

    # Keyed on (kind, id), not the id alone: two entries naming different sorts of source
    # could share a numeric id, and two naming the same table could not collide at all
    # while the key was the lens field.
    named = [(low.source_kind, low.source_id) for low in lowered]
    duplicates = sorted({f"{kind} {source_id}" for kind, source_id in named if named.count((kind, source_id)) > 1})
    if duplicates:
        raise ValueError(f"Each derivedFrom entry must name a distinct source, but {', '.join(duplicates)} appear{'s' if len(duplicates) == 1 else ''} more than once. One entry per source: its transform already says everything about how the data maps back")

    unmappable = enums.TransformKind.UNMAPPABLE.value
    if lowered[0].transform.kind == unmappable and any(low.transform.kind != unmappable for low in lowered):
        raise ValueError(
            "The first derivedFrom entry is the primary parent -- the one that places this data -- so it cannot be UNMAPPABLE while a mappable entry follows. "
            "An entry with no `transform` *is* UNMAPPABLE: naming a source claims no geometry. Put the mappable source first, or state its transform"
        )

    sources = [resolve_derivation_source(info, low) for low in lowered]
    fields = [get_for_org(models.CoordinateSystem, info, id=low.transform.field) if low.transform.field else None for low in lowered]

    edges: list[models.Transformation] = []
    with transaction.atomic():
        for low, (source_system, label), field in zip(lowered, sources, fields):
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

def create_lens(
    dataset: "models.ArrayDataset",
    slices: list,
    ctx: CreationContext,
) -> "models.Lens":
    """Create a lens -- and, only if it slices, its coordinate system and the edge recording the shift.

    The lens' shape and axes are not written: they follow from the dataset and the
    slices, and a second copy could only drift from the first. The same rule decides
    whether it gets a coordinate system at all: an unsliced lens selects everything,
    so its space is the dataset's intrinsic space *by definition* -- materializing a
    second node for it, joined by an identity edge, would store nothing. Lenses are
    immutable, so the decision is final at creation.
    """
    intrinsic = dataset.intrinsic_coordinate_system
    if intrinsic is None:
        raise ValueError(f"Dataset {dataset.pk} has no intrinsic coordinate system")

    if dataset.data_arrays.order_by("level").first() is None:
        raise ValueError(f"Dataset {dataset.pk} has no level-0 data array to place the lens against")

    slice_models = [slice.model_dump() for slice in slices]
    sliced = any(slice_models)

    # An unsliced lens lives in the dataset's own grid -- it selects everything, so its space
    # *is* that space -- and points at the same node. Only a sliced one needs a space of its
    # own, and gets it before the lens so there is one write each.
    lens_system = intrinsic
    if sliced:
        lens_system = models.CoordinateSystem.objects.create(
            name=f"{dataset.name}/lens",
            creator=ctx.user,
            organization=ctx.organization,
        )

    lens = models.Lens.objects.create(
        dataset=dataset,
        coordinate_system=lens_system,
        slices=slice_models,
    )

    if not lens.slices_list:
        return lens

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
