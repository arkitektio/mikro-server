"""Mutations for network collections.

A collection is immutable and versioned: refining a tracing produces a new version, it does not
edit an old one. It *is* a konnektion store -- one uploaded prefix holding its manifest, both
catalogs and every octree level -- so registering one is naming that store and the space its
node positions are in, and nothing else. There is deliberately no mutation here that writes
nodes or edges one by one, and no field that reads them back that way.

The graph twin of :mod:`core.mutations.mesh_collection`, and structurally identical to it on
purpose. The one place they genuinely differ is what the manifest declares -- an edge arity and
a coarsening scheme, neither of which a mesh manifest has -- and that difference is why the two
are two stores and two scalars rather than one with a flag.
"""

from django.db import transaction
from kante.types import Info
import strawberry
from pydantic import BaseModel, Field

import kante
from core import models, scalars, types
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.inputs.file_link import SourceFileInput, SourceFileInputModel
from core.inputs.coords import AxisInput, AxisInputModel, DerivedFromInput, DerivedFromSpec
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import file_link as file_link_logic
from core.logic import folder as folder_logic
from core.logic import graph as graph_logic
from core.mutations._generic import make_delete, self_owner
from core.scoping import get_for_org


class CreateNetworkCollectionInputModel(BaseModel):
    version: str
    store: str
    axes: list[AxisInputModel]
    folder: str | None = None
    derived_from: list[DerivedFromSpec] | None = None
    source_files: list[SourceFileInputModel] | None = None
    provenance_metadata: dict | None = None



@prose_errors
@kante.pydantic_input(CreateNetworkCollectionInputModel, description="Input for registering an immutable, versioned network collection. The collection gets a coordinate system of its own, and an edge relates it to the space the network was traced in")
class CreateNetworkCollectionInput:
    """Input for registering a network collection."""

    version: str = strawberry.field(description="The immutable version of this collection, e.g. 'v20260713-a3f9'. A refined tracing is a new version, never an edit to an old one")
    store: scalars.KonnektionLike = strawberry.field(
        description=(
            "The uploaded **konnektion store** holding this collection: one prefix with `konnektion.json`, both catalogs and every octree level. Request it with `requestKonnektionUpload`, write the "
            "tree, land the manifest last, then `finishKonnektionUpload` -- which reads the manifest and refuses a prefix without one. **Nothing about the geometry is declared here**: the "
            "grid, the encoding, the level count and the format version are all stated by the manifest and were read from it, because a second statement of the same fact is free to "
            "disagree with the bytes"
        )
    )
    axes: list[AxisInput] = strawberry.field(
        description=(
            "The axes of the collection's own coordinate system, in order. Required, and deliberately never defaulted: a default here would turn 'declared the wrong order' into 'never "
            "thought about the order', and it is checked against the store's manifest. **State the order your vertices are "
            "actually in.** The rest of this server writes spatial axes in array order (z, y, x) and that is the recommended convention, but konnektion node positions are stored "
            "(x, y, z) -- so if you declare (x, y, z), the edge back to a (z, y, x) parent is a MAP_AXIS reversal, not an IDENTITY. Getting this wrong is silent: the rank still "
            "matches, the edge is accepted, the layer places, and everything draws transposed"
        )
    )
    folder: strawberry.ID | None = strawberry.field(
        default=None,
        description="The folder to file this network collection in. Organisational only -- it says nothing about where the networks sit in space. Defaults to the user's default folder",
    )
    derived_from: list[DerivedFromInput] | None = strawberry.field(
        default=None,
        description="What this network collection was computed from. One entry per source; the first is the primary parent. Each names its source and how this collection's own space relates to that source's: **omit the transform and the edge is UNMAPPABLE**, recording the lineage and claiming no geometry. State IDENTITY when the geometry is expressed directly in the source's grid, SCALE when it was extracted from a downsampled one",
    )
    source_files: list[SourceFileInput] | None = strawberry.field(
        default=None,
        description="Optional statement of which files these networks were loaded from -- the SWC a tracer wrote, the .ims a converter read. **Not a `derivedFrom` entry, deliberately**: a derivation is an edge of the coordinate graph and relates two spaces, while a file has no space. This records lineage between bytes and data and leaves the graph untouched",
    )
    provenance_metadata: scalars.Any | None = strawberry.field(default=None, description="How this collection was produced: the tracing run, its parameters and its inputs")


def _resolve_konnektion_store(info: Info, model: CreateNetworkCollectionInputModel) -> "models.KonnektionStore":
    """The store this collection is, refusing one whose manifest was never read.

    `finishKonnektionUpload` is the step that fetches `konnektion.json` and refuses a prefix without
    one, so a store that never reached it is -- as far as anything here can tell -- a
    half-written upload. Registering it would record a collection whose grid and encoding are
    simply unknown, which is the state this whole design exists to make unrepresentable.
    """
    store = get_for_org(models.KonnektionStore, info, id=model.store)
    if not store.populated:
        raise ValueError(
            f"Konnektion store {store.pk} has not been finished, so its manifest has not been read and nothing is known about what it holds. Call `finishKonnektionUpload` after the tree is "
            f"written -- that step is what reads `konnektion.json`, and it refuses a prefix that has none."
        )
    return store


def _assert_axes_agree(declared: list, manifest_axes: list[str] | None) -> None:
    """Refuse a collection whose declared axes contradict what the writer says it wrote.

    Only possible because a konnektion store carries a manifest, and worth doing because getting
    this wrong is silent in the worst way: the rank still matches, the derivation edge is
    accepted, the layer places, and everything draws transposed.

    A manifest that states no axes is not a disagreement. Nothing in the format decodes through
    them -- node position components, cell size, the bbox columns and the Morton interleave are
    all positional -- so a writer that never claimed an order is simply not answering this
    question, and the collection's own declaration stands.
    """
    if not manifest_axes:
        return

    names = [axis.name for axis in declared or []]
    if names != list(manifest_axes):
        raise ValueError(
            f"This collection declares axes {names} but its konnektion store's manifest says the geometry was written as {list(manifest_axes)}. "
            f"One of the two is wrong, and neither the rank check nor the placement walk would catch it -- the collection would register, place, and draw transposed. "
            f"If the orders genuinely differ, that belongs on the derivation edge as a MAP_AXIS naming each axis on both sides, never as a reordered declaration here."
        )


def _write_collection(
    info: Info,
    model: CreateNetworkCollectionInputModel,
    ctx: CreationContext,
    *,
    spec_version: str,
    grid: dict,
    encoding: dict,
    store: "models.KonnektionStore",
) -> types.NetworkCollection:
    """Write the row, its space and its edges.

    Atomic, because the collection row is written before its axes are checked and before its
    edges are: without this, an axis ordering the space refuses -- or a rank an edge refuses --
    leaves an orphan collection behind and returns an error. The same guarantee
    ``create_coordinate_system`` keeps for a space and its registrations.
    """
    with transaction.atomic():
        collection = models.NetworkCollection.objects.create(
            version=model.version,
            spec_version=spec_version,
            store=store,
            grid=grid,
            encoding=encoding,
            provenance_metadata=model.provenance_metadata or {},
            folder=folder_logic.folder_for_new_container(info, ctx, model.folder, model.derived_from),
            creator=ctx.user,
            organization=ctx.organization,
        )

        system = graph_logic.create_collection_system(
            name=f"{collection.version}/network",
            axes=model.axes,
            owner=collection,
            ctx=ctx,
        )

        # Optional on purpose: a network in some absolute space is derived from nothing.
        coordinate_system_logic.write_derivation_edges(info, name=collection.version, own_system=system, derived_from=model.derived_from or [], ctx=ctx)
        file_link_logic.write_file_links(info, container=collection, source_files=model.source_files or [], ctx=ctx)

    return collection


def create_network_collection(info: Info, input: CreateNetworkCollectionInput) -> types.NetworkCollection:
    """Register an immutable, versioned network collection, in a coordinate system of its own.

    The bytes arrive the way every other store's do: the client requests an upload grant,
    writes the tree, and finishes the upload -- which is where ``fill_info`` reads
    ``konnektion.json`` and refuses a prefix that has none.

    The collection *owns* its coordinate system, and ``derivedFrom`` relates that system to
    whatever the network was traced in -- a table or another collection, not only an image's
    grid. A network traced on a half-resolution grid is a SCALE.

    ``axes`` is required and never defaulted: a default would turn "declared the wrong order"
    into "never thought about the order". What it is checked against is the store's own
    manifest, where the writer says which order it wrote.

    **The geometry is declared nowhere.** A collection is a konnektion store, and a konnektion
    store states its grid, encoding, level count and format version in a manifest the server
    read when the upload was finished. That includes two things a mesh manifest has no analogue
    for: the edge arity, and whether any level coarsened at all -- a traced arbor commonly
    declares `pruning: NONE` and `simplification: NONE`, which is a statement, not a silence. There is deliberately no way to pass those here: a second
    statement of the same fact is free to disagree with the bytes, and nothing downstream could
    say which of the two was right.
    """
    model = input.to_pydantic()

    ctx = CreationContext.from_info(info)
    store = _resolve_konnektion_store(info, model)

    # Read, never declared. `fill_info` already fetched `konnektion.json`, so these are what the
    # writer actually wrote rather than what a caller retyped. `spec_version` is namespaced
    # because a bare "1" says nothing about *which* format's version one it is -- and this
    # server now reads two, whose version counters are independent.
    _assert_axes_agree(model.axes, store.axes)
    return _write_collection(
        info,
        model,
        ctx,
        spec_version=f"konnektion/{store.spec_version}",
        grid=store.grid or {},
        encoding=store.encoding or {},
        store=store,
    )


class DeleteNetworkCollectionInputModel(BaseModel):
    id: str = Field(description="The ID of the network collection to delete")


@kante.pydantic_input(DeleteNetworkCollectionInputModel, description="Input for deleting a network collection by ID")
class DeleteNetworkCollectionInput:
    """Input for deleting a network collection by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the network collection to delete")


delete_network_collection = make_delete(models.NetworkCollection, DeleteNetworkCollectionInput, owner=self_owner)
