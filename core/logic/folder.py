"""Where a container gets filed, and who is allowed to say.

Two rules, and the second is why this module is more than a lookup:

**Only root data is filed explicitly.** A container computed from another one -- a
deconvolution, a segmentation, a measurement table, a mesh extracted from a mask -- is
filed *with the thing it came from*, and neither the create mutation nor
``put<Things>InFolder`` will take a folder for it. Filing a derivation somewhere else would
put a fact about lineage in two places and let them disagree: a user looking at a folder
would see the parent and not the child, or the child and not the parent, and nothing in the
model would say which was the mistake.

**Derived data follows its primary parent.** ``folder_id`` is written on the derived row
too, so `Folder.array_datasets`, the `folder` filters and `children` stay plain column queries
instead of recursive lineage walks. That is a second copy of the parent's answer, and it is
safe only because the guard leaves exactly one writer: re-filing a parent rewrites every
descendant (:func:`refile`), and nothing else can touch a derived row's folder.

**The primary parent, at every hop**, matching the rule the whole coordinate graph already
uses: a fusion of two acquisitions owes both historically, but it *sits* where its first
declared source sits. ``derivedFrom`` order is that declaration.

**Kind-blind.** An UNMAPPABLE derivation still counts. Filing is a historical question --
"where did this come from" -- not a spatial one, so a measurement table whose geometry did
not survive is still filed with the mask it was measured from. This is deliberately *not*
:func:`core.logic.graph.lineage_ancestors`, which stops at an UNMAPPABLE primary because it
answers the spatial question instead.

A derivation that names only a bare coordinate system -- a world, a space owned by nothing --
has no parent container and therefore nothing to inherit, so it counts as a root and is
explicitly fileable.
"""

from typing import Any, cast

from core import models
from core.creation import CreationContext
from core.logic import graph as graph_logic
from core.scoping import get_for_org
from kante.types import Info

#: Anything fileable that can also be derived: the four containers. `Image`, `File` and
#: `Table` are fileable but have no lineage, so nothing here ever sees one.
Container = models.ArrayDataset | models.TableDataset | models.MeshCollection | models.AnnotationCollection

#: The models a `derivedFrom` entry can name, by its source kind. `LENS` is absent on
#: purpose: a lens is a selection over a dataset, not a filed thing, so a child derived
#: from one is filed with the lens' *dataset*.
_SOURCE_MODELS: dict[str, type] = {
    "DATASET": models.ArrayDataset,
    "TABLE_DATASET": models.TableDataset,
    "MESH_COLLECTION": models.MeshCollection,
    "ANNOTATION_COLLECTION": models.AnnotationCollection,
}

#: The container keys that name something fileable. `container_map` also returns
#: `("system", pk)` for a space owned by nothing, which has no folder to inherit.
_FILEABLE_KEYS = frozenset({"dataset", "tabledataset", "meshcollection", "annotationcollection"})

_DERIVED_REFUSAL = "{label} was computed from {parent}, so it is filed with it and cannot be filed on its own. Derived data follows its primary parent's folder: move {parent} instead, and everything derived from it moves with it"


def resolve_folder(info: Info, ctx: CreationContext, folder_id: str | None) -> models.Folder:
    """The folder named by the input, or the user's default folder (created on first use)."""
    if folder_id:
        return cast(models.Folder, get_for_org(models.Folder, info, id=folder_id))
    return cast(models.FolderManager, models.Folder.objects).get_current_default(ctx)


def _container_for_system(system_id: int) -> Container | None:
    """The fileable container owning a space, or None when the space is owned by nothing.

    Goes through ``container_map`` rather than asking each model in turn, so a lens' or a
    level's space resolves to the *dataset* -- they are one node of the fact tree, and the
    dataset is the node.
    """
    kind, pk = graph_logic.container_map({system_id})[system_id]
    if kind not in _FILEABLE_KEYS:
        return None
    return graph_logic.MODEL_BY_KEY[kind].objects.filter(pk=pk).first()


def parent_from_specs(info: Info, derived_from: list[Any] | None) -> Container | None:
    """The container a *not yet created* thing will be filed with, read off its input.

    Resolved from the declared sources rather than from written edges, because
    ``create_array_dataset`` is not wrapped in a transaction: a refusal raised after the row
    exists would leave it behind. The first entry is the primary parent.
    """
    if not derived_from:
        return None

    primary = derived_from[0].lower()
    model = _SOURCE_MODELS.get(primary.source_kind)
    if model is not None:
        return cast(Container, get_for_org(model, info, id=primary.source_id))

    if primary.source_kind == "LENS":
        lens = get_for_org(models.Lens, info, id=primary.source_id)
        return cast(models.Lens, lens).dataset

    # COORDINATE_SYSTEM: a bare space may still be owned by a container (a dataset's own
    # grid named the long way round), and it may be owned by nothing at all.
    system = get_for_org(models.CoordinateSystem, info, id=primary.source_id)
    return _container_for_system(system.pk)


def _own_system(container: Container) -> "models.CoordinateSystem | None":
    """The space a container's derivation edges leave from."""
    if isinstance(container, models.ArrayDataset):
        return container.intrinsic_coordinate_system
    return getattr(container, "coordinate_system", None)


def _derivation_edges(container: Container) -> list["models.Transformation"]:
    """Every edge saying what this container was computed from, in the creator's order."""
    if isinstance(container, models.ArrayDataset):
        return graph_logic.derivation_edges(container)
    system = _own_system(container)
    return graph_logic.collection_derivation_edges(system) if system else []


def derivation_parent(container: Container) -> Container | None:
    """The container this one is filed with, or None when it is a root.

    The graph-side counterpart of :func:`parent_from_specs`, for a container that already
    exists. Reads the primary (first) derivation edge and resolves what owns the space it
    lands in.
    """
    edges = _derivation_edges(container)
    if not edges or edges[0].output is None:
        return None
    return _container_for_system(edges[0].output.pk)


def _label(container: Container) -> str:
    """A container in an error message: its kind and the name a user would recognise."""
    name = getattr(container, "name", None) or getattr(container, "version", None) or container.pk
    return f"{type(container).__name__} '{name}'"


def assert_explicitly_fileable(container: Container) -> None:
    """Refuse to file a derived container: it goes where its primary parent goes."""
    parent = derivation_parent(container)
    if parent is not None:
        raise ValueError(_DERIVED_REFUSAL.format(label=_label(container), parent=_label(parent)))


def folder_for_new_container(info: Info, ctx: CreationContext, folder_id: str | None, derived_from: list[Any] | None) -> "models.Folder | None":
    """The folder a container being created is filed in.

    Derived data inherits, and naming a folder for it is refused rather than ignored -- a
    silently dropped folder would look like it worked. A root is filed where the input says,
    or in the user's default folder.
    """
    parent = parent_from_specs(info, derived_from)
    if parent is None:
        return resolve_folder(info, ctx, folder_id)

    if folder_id:
        raise ValueError(_DERIVED_REFUSAL.format(label="This data", parent=_label(parent)))
    return parent.folder


def _container_key(container: Container) -> tuple[str, int] | None:
    """This container's key in the fact tree, from the one registry that defines them."""
    for entry in graph_logic.CONTAINERS:
        if type(container) is entry.model and entry.root_field == "pk":
            return (entry.key, container.pk)
    return None


def _primary_children(container: Container) -> list[Container]:
    """The containers whose *primary* parent is this one, and which therefore follow it.

    Every edge landing in this container's spaces names a child, but only a child whose
    first declared source is this container is filed here: a fusion that named this source
    second belongs with its own primary, exactly as placement does.
    """
    key = _container_key(container)
    if key is None:
        return []

    edges = list(models.Transformation.objects.filter(graph_logic.container_q([key], field="output"), parent__isnull=True).select_related("input", "output").order_by("pk"))
    if not edges:
        return []

    keys = graph_logic.container_map({edge.input.pk for edge in edges if edge.input} | {edge.output.pk for edge in edges if edge.output})

    seen: set[tuple[str, int]] = {key}
    children = []
    for edge in edges:
        child_key = keys.get(edge.input.pk) if edge.input else None
        if child_key is None or child_key in seen or child_key[0] not in _FILEABLE_KEYS:
            continue
        if not graph_logic.is_derivation_edge(edge, of_container=child_key, keys=keys):
            continue
        seen.add(child_key)
        child = graph_logic.MODEL_BY_KEY[child_key[0]].objects.filter(pk=child_key[1]).first()
        # Only the primary parent carries the filing, and only this container's primary
        # children follow it. A fusion listing this source second stays where its own first
        # source is.
        if child is None or derivation_parent(child) != container:
            continue
        children.append(child)
    return children


def refile(container: Container, folder: "models.Folder | None") -> None:
    """File a container, and move everything derived from it with it.

    The propagation is what keeps the stored copy honest: a derived row's folder is a copy
    of its parent's answer, so the parent moving has to rewrite it. Depth-first, with a
    visited set, because a cycle in the lineage is nonsense but must not hang a request.
    """
    seen: set[tuple[str, int]] = set()

    def walk(current: Container) -> None:
        marker = (type(current).__name__, current.pk)
        if marker in seen:
            return
        seen.add(marker)
        # django-stubs infers the descriptor as write-None-only for a nullable FK.
        cast(Any, current).folder = folder
        current.save(update_fields=["folder"])
        for child in _primary_children(current):
            walk(child)

    walk(container)
