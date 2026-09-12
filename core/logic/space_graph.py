"""One shared space's slice of the coordinate graph, fetched once and searched in memory.

The space-rooted twin of :mod:`core.logic.scene_graph`. Both compose the same
:class:`core.logic.edge_universe.EdgeUniverse`; what differs is the seeds and the question.
A scene's graph seeds from its layers and asks "where does this layer sit"; this one seeds
from every resident of every space placeable here and asks "what is in here at all". A space
needs no scene to answer that, and two scenes over one space get the same answer -- because
registrations are a property of the space and nothing here consults a scene.

**Everything composes forward.** There is no matrix inverse in this codebase and there is
not going to be one here. A source is anchored at the system that actually carries its
registration, so the walk runs *with* the edges rather than against them; the rare edge
authored backwards yields a null extent with a state saying so, rather than a number nobody
computed.

**Nothing is stored.** A per-(source, space) box would be a cache that goes stale the
moment anyone refines a registration, and this repo already carries that bug once
(``Annotation.bbox_cube`` against ``created_with_transforms``, whose bulk recompute is
documented as a separate operation that does not exist). The extent is derived per request
from the shapes and the edges, which cannot disagree with themselves.

**An extent is partial, and that is the point.** Composing at one fixed rank is right
inside a dataset and wrong across a registration: a (c,y,x) dataset registered onto the
(y,x) of a (z,y,x) world is a *slab*, extended along z. The extent names the axes it
constrains and stays silent about the rest, because a number written for z would cull the
dataset out of every view it is really in.
"""

from dataclasses import dataclass

from kante.types import Info

from core import enums, models
from core.logic import coords as coords_logic
from core.logic import edge_universe
from core.logic import graph as graph_logic

#: Where a `SpaceGraph` memo lives on the request context, keyed by space pk.
_LOADER_KEY = "space_graphs"


@dataclass(frozen=True)
class Source:
    """One container registered into the space, and the system its registration is anchored at."""

    container: object
    system: "models.CoordinateSystem"
    dataset_id: int | None
    #: The fact-tree node this source searches from. Distinct from ``dataset_id``, which is
    #: the *array dataset* whose shapes and anchors are read below and is None for a
    #: collection: a mesh collection is its own container and searches from itself.
    container_key: tuple | None


@dataclass(frozen=True)
class Hit:
    """One source in view: where it is, how it got there, and which of its anchors are in view."""

    source: Source
    extent: dict[str, list[float]] | None
    extent_state: str
    path: list[tuple["models.Transformation", bool]]
    anchors: list["models.CoordinateAnchor"]


def _resident_box(resident: object, shapes: dict[int, list[int]]) -> tuple[list[float], list[float]] | None:
    """A resident's extent in the space it lives in, half-open around the voxel centre.

    Asked of the **data**, not of the space -- a space has no extent of its own, and under
    residence several residents may share one. A shape ``S`` spans ``[-0.5, S - 0.5]``, the
    convention :func:`coords.vectors_bbox` encodes and that
    :func:`coords.pyramid_transform`'s half-voxel translation exists to keep true across
    levels: level 1's ``[-0.5, 31.5]`` maps onto level 0's ``[-0.5, 63.5]``, the same box,
    which it would not if the origin were the voxel corner.

    None where the server holds no geometry: a mesh collection's vertices and a table's rows
    are in Parquet it never opens.
    """
    if isinstance(resident, models.Lens):
        shape = resident.shape_list
    elif isinstance(resident, models.DataArray):
        shape = resident.shape
    elif isinstance(resident, models.ArrayDataset):
        shape = shapes.get(resident.pk) or []
    else:
        return None

    if not shape:
        return None
    return [-0.5] * len(shape), [float(size) - 0.5 for size in shape]


class SpaceGraph:
    """Every source registered into one space, with the edges that place them, fetched up front."""

    def __init__(self, space: "models.CoordinateSystem", *, organization, loaders: dict | None = None) -> None:
        """Fetch the space's residents, then the edge universe rooted at the space."""
        self.space = space
        self.organization = organization

        # The space itself stays a candidate: when data lives directly in it -- a scene rooted
        # on a dataset's own grid -- that data is in view of itself, with an empty path.
        self._candidate_ids = graph_logic.placeable_system_ids_in(space)

        # **Fetch the residents, not the spaces.** Residence puts `coordinate_system_id` on
        # the data row, so asking "what lives in these spaces" is one indexed `IN` per data
        # model over rows that carry their own shape and dataset -- where the ownership model
        # had to fetch the spaces and then follow seven FKs back out of them.
        #
        # This is the one place an organization has to be honoured, and the reason the whole
        # universe below is scoped too: what escapes to a client here is whole *containers*,
        # and a shared space can have co-tenants whose data this request may not see.
        self._residents: list[object] = []
        if self._candidate_ids:
            for model in (container.model for container in graph_logic.CONTAINERS):
                query = model.objects.filter(coordinate_system_id__in=self._candidate_ids, organization=organization) if hasattr(model, "organization") else model.objects.filter(coordinate_system_id__in=self._candidate_ids)
                self._residents.extend(query.select_related("coordinate_system").prefetch_related("coordinate_system__axes"))

        # Seeded by dataset id, not by space: a hundred tiles share one stage space, and
        # `residence_map` would collapse them to one resident and lose ninety-nine. The
        # resident rows carry the ids already, so there is nothing to look up.
        dataset_ids = {resident.pk if isinstance(resident, models.ArrayDataset) else getattr(resident, "dataset_id", None) for resident in self._residents}
        dataset_ids.discard(None)

        # A collection seeds by its own system, like any other container. It used to go in
        # through a separate `collection_systems` argument, because the universe could only
        # resolve it by fetching its derivation edge first.
        collection_systems = {resident.coordinate_system_id for resident in self._residents if isinstance(resident, tuple(c.model for c in graph_logic.COLLECTION_CONTAINERS)) and resident.coordinate_system_id}

        self.universe = edge_universe.EdgeUniverse(
            space,
            organization=organization,
            seed_datasets=dataset_ids,
            seed_systems=collection_systems,
            loaders=loaders,
        )

        self._shapes: dict[int, list[int]] | None = None
        self._anchors: dict[int, list[models.CoordinateAnchor]] | None = None
        self._sources: list[Source] | None = None

    def adjacency(self, container_key: tuple | None, *, at: dict[str, int] | None = None, admit_scoped: bool = False) -> dict:
        """The searchable universe for one container: its lineage's facts plus this space's claims."""
        return self.universe.adjacency(container_key, at=at, admit_scoped=admit_scoped)

    def shapes(self) -> dict[int, list[int]]:
        """Every reachable dataset's level-0 shape, in one query.

        Batched rather than read off ``ArrayDataset.shape_list``, which is a query per dataset
        and would make the cost of this whole graph grow with its source count.
        """
        if self._shapes is None:
            rows = models.DataArray.objects.filter(dataset_id__in=self.universe.dataset_ids, level=0).values_list("dataset_id", "shape") if self.universe.dataset_ids else []
            self._shapes = {dataset_id: shape for dataset_id, shape in rows}
        return self._shapes

    def _anchors_of(self, dataset_id: int) -> list["models.CoordinateAnchor"]:
        """Every reachable dataset's anchors, in one query, and only once anyone asks.

        Lazy on purpose: a 1000-timepoint four-channel dataset has thousands of anchor rows,
        and a client asking only for extents should not pay for them.
        """
        if self._anchors is None:
            self._anchors = {}
            if self.universe.dataset_ids:
                for anchor in models.CoordinateAnchor.objects.filter(dataset_id__in=self.universe.dataset_ids):
                    self._anchors.setdefault(anchor.dataset_id, []).append(anchor)
        return self._anchors.get(dataset_id, [])

    def sources(self) -> list[Source]:
        """Every container registered into the space, anchored at the system that carries its claim.

        The anchor system matters and is not always intrinsic. A dataset registered through a
        *sliced lens* anchors at the lens' system, whose shape is an exact box that composes
        forward; anchoring it at intrinsic instead would force the walk backwards across the
        lens edge and give no extent at all. Where a fact tree carries rival claims into one
        space the walk still returns one route (RFC-9); which one is a property of the edges,
        not of anything asked here.
        """
        if self._sources is not None:
            return self._sources

        by_container: dict[tuple, Source] = {}
        for resident in self._residents:
            system = resident.coordinate_system
            if system is None:
                continue
            if isinstance(resident, models.ArrayDataset):
                dataset_id = resident.pk
            elif isinstance(resident, (models.Lens, models.DataArray)):
                dataset_id = resident.dataset_id
            else:
                # A collection has no array dataset of its own: no shapes, no coordinate
                # anchors. It used to borrow its *source* dataset's id here, which then read
                # that image's shape as if it were the mesh's. It searches from its own
                # container key instead, which the universe now has directly.
                dataset_id = None

            key = (type(resident).__name__, resident.pk)
            candidate = Source(container=resident, system=system, dataset_id=dataset_id, container_key=self.universe.container_of(system.pk))
            # A dataset's own levels and lenses live in its grid too, and reporting each of
            # them as a separate thing in view would return the same pixels several times.
            # The dataset itself is the thing a client asked about; a level or a lens counts
            # only where no dataset of its own is in the set.
            if isinstance(resident, (models.Lens, models.DataArray)) and ("ArrayDataset", dataset_id) in by_container:
                continue
            by_container.pop(("Lens", None), None)
            by_container[key] = candidate

        # Drop the levels and lenses a dataset in the set already speaks for.
        dataset_pks = {pk for kind, pk in by_container if kind == "ArrayDataset"}
        self._sources = sorted(
            (source for key, source in by_container.items() if not (key[0] in ("Lens", "DataArray") and source.dataset_id in dataset_pks)),
            key=lambda source: (type(source.container).__name__, source.container.pk),
        )
        return self._sources

    def path(self, source: Source, *, at: dict[str, int] | None = None) -> list[tuple["models.Transformation", bool]] | None:
        """The path of edges from a source's system into the space, or None when there is none."""
        return graph_logic._bfs_path(self.adjacency(source.container_key, at=at), source.system.pk, self.space.pk)

    def conditional_path(self, source: Source, *, at: dict[str, int] | None = None) -> list[tuple["models.Transformation", bool]] | None:
        """The path a source has only through its selector-scoped edges, if it has one.

        Asked exactly when :meth:`path` found nothing. A source registered per channel *is* in
        this space, and returning nothing for it made a per-channel registration invisible to
        every spatial query -- the data was there, the graph knew where, and the answer was
        silence.

        What comes back is a route, not a map: its scoped edge holds at one index, so no extent
        is computed from it. The hit says CONDITIONAL and carries the path, which is what lets a
        client see what it would have to fix to get a box.
        """
        return graph_logic._bfs_path(
            self.adjacency(source.container_key, at=at, admit_scoped=True),
            source.system.pk,
            self.space.pk,
        )

    def _forms(self, source: Source, path: list[tuple["models.Transformation", bool]]) -> dict[str, coords_logic.AxedForm] | None:
        """The composed functionals from a source's frame into the space, or None when there are none."""
        if any(inverted for _, inverted in path):
            return None
        steps = [graph_logic._edge_step(edge) for edge, _ in path]
        source_axes = [axis.name for axis in source.system.axes.all()]
        return coords_logic.compose_forms(steps, source_axes)

    def placement(self, source: Source, region: dict[str, list[float]], *, at: dict[str, int] | None = None) -> Hit | None:
        """One source's placement in the space, or None when it is not in view.

        The states are ordered, and the order is a precedence: a source whose geometry the
        server cannot read is UNREADABLE whatever its path looks like, and only then does the
        path's own shape get a say.

        A source is never culled for being unbounded. Refusing to bound something is not the
        same as knowing it is out of view, and a client that has been handed a null extent
        with a reason can fetch the geometry and cull it locally.
        """
        path = self.path(source, at=at)
        if path is None:
            # Nothing unconditional. A selector-scoped route is still a placement, so the source
            # is returned with the route and no box: which box depends on the coordinate this
            # query did not fix, and inventing one would put the data somewhere it is not.
            conditional = self.conditional_path(source, at=at)
            if conditional is None:
                return None
            return Hit(source=source, extent=None, extent_state=enums.ExtentState.CONDITIONAL.value, path=conditional, anchors=[])

        box = _resident_box(source.container, self.shapes())
        if box is None:
            return Hit(source=source, extent=None, extent_state=enums.ExtentState.UNREADABLE.value, path=path, anchors=[])

        forms = self._forms(source, path)
        if forms is None:
            return Hit(source=source, extent=None, extent_state=enums.ExtentState.INVERTED.value, path=path, anchors=[])

        try:
            extent = coords_logic.axed_bbox(box[0], box[1], forms)
        except coords_logic.NonAffineTransformError:
            return Hit(source=source, extent=None, extent_state=enums.ExtentState.NON_AFFINE.value, path=path, anchors=[])

        if not coords_logic.boxes_overlap(extent, region):
            return None

        return Hit(source=source, extent=extent, extent_state=enums.ExtentState.KNOWN.value, path=path, anchors=[])

    def anchors_in(self, source: Source, region: dict[str, list[float]], *, at: dict[str, int] | None = None) -> list["models.CoordinateAnchor"]:
        """The source's anchors whose slab overlaps the region.

        An anchor is already a slab rather than a point: ``coordinates`` pins some axes to
        discrete level-0 indices and is **global along every axis it omits**. So a pinned axis
        is one voxel wide and an omitted one spans the container's whole extent, and the slab
        then composes and intersects exactly like any other box -- no separate treatment of
        discrete versus spatial axes. This is the geometric generalization of
        :attr:`Lens.active_anchors`' "does not contradict" rule, which can stay a ``Q`` only
        because a lens and its anchors share one frame.

        Anchor coordinates are always level-0 *intrinsic* indices, so the slab is pushed from
        the dataset's intrinsic system -- not from the source's anchor system, which may be a
        lens or a level whose indices are its own. The two frames look alike, which is exactly
        how a half-voxel goes missing.
        """
        if source.dataset_id is None:
            return []
        anchors = self._anchors_of(source.dataset_id)
        if not anchors:
            return []

        dataset = source.container if isinstance(source.container, models.ArrayDataset) else None
        intrinsic = dataset.intrinsic_coordinate_system if dataset is not None else None
        if intrinsic is None:
            return []

        shape = self.shapes().get(source.dataset_id) or []
        if not shape:
            return []

        # Unscoped only, and `at` when the caller fixed one: an anchor's slab is pushed into
        # the space, which is a map, so it may not ride a scoped edge the question did not fix.
        # A source whose only route is scoped therefore reports no anchors until `at` is given,
        # which matches its CONDITIONAL extent -- both are the same missing coordinate.
        path = graph_logic._bfs_path(self.adjacency(source.container_key, at=at), intrinsic.pk, self.space.pk)
        if path is None or any(inverted for _, inverted in path):
            return []

        axis_names = [axis.name for axis in intrinsic.axes.all()]
        if len(shape) != len(axis_names):
            # An intrinsic system has exactly one axis per dimension of its store -- that is
            # what `assert_axes_describe_the_store` holds every create to. If the two ever
            # disagree, the box below would be built by pairing them off positionally and
            # filling the shortfall with a *fabricated* half-unit extent, which is a real
            # answer to a question that cannot be answered. Joining the early returns above is
            # the honest reply: a query does not raise on inconsistent stored data, it reports
            # nothing in view. The writer is where this is caught loudly.
            return []

        try:
            forms = coords_logic.compose_forms([graph_logic._edge_step(edge) for edge, _ in path], axis_names)
        except coords_logic.NonAffineTransformError:
            return []

        in_view: list[models.CoordinateAnchor] = []
        for anchor in anchors:
            coordinates = anchor.coordinates or {}
            mins: list[float] = []
            maxs: list[float] = []
            for index, name in enumerate(axis_names):
                if name in coordinates:
                    value = float(coordinates[name])
                    mins.append(value - 0.5)
                    maxs.append(value + 0.5)
                else:
                    # The whole extent of an axis the anchor does not pin. `shape` and
                    # `axis_names` are the same length -- checked above -- so this index is
                    # always real; it used to fall back to a fabricated 0.5.
                    mins.append(-0.5)
                    maxs.append(float(shape[index]) - 0.5)
            if coords_logic.boxes_overlap(coords_logic.axed_bbox(mins, maxs, forms), region):
                in_view.append(anchor)
        return in_view

    def in_view(self, region: dict[str, list[float]], *, with_anchors: bool, at: dict[str, int] | None = None) -> list[Hit]:
        """Every source whose extent meets the region, each with its in-view anchors.

        ``at`` is where the caller is standing, and it does for this query what it does for a
        layer's: fix a coordinate and a per-index registration resolves to a real box; leave it
        out and the source is still returned, badged CONDITIONAL, because it is in the space
        whether or not this question said which channel.
        """
        hits: list[Hit] = []
        for source in self.sources():
            hit = self.placement(source, region, at=at)
            if hit is None:
                continue
            if with_anchors:
                hit = Hit(source=hit.source, extent=hit.extent, extent_state=hit.extent_state, path=hit.path, anchors=self.anchors_in(source, region, at=at))
            hits.append(hit)
        return hits


def region_from_bounds(space: "models.CoordinateSystem", mins: list[float], maxs: list[float]) -> dict[str, list[float]]:
    """A positional region keyed against the space's axes, as a leading prefix.

    A region shorter than the system constrains only its leading axes and says nothing about
    the rest. Deliberately *not* the zero-fill Postgres ``cube`` gives
    ``AnnotationFilter.intersects``: zero-filling a viewport's missing axes pins them to
    ``[0, 0]`` and culls away everything off the first plane.
    """
    if len(mins) != len(maxs):
        raise ValueError(f"A region's `min` and `max` must have the same number of entries, got {len(mins)} and {len(maxs)}")

    axis_names = [axis.name for axis in space.axes.all()]
    if len(mins) > len(axis_names):
        raise ValueError(f"A region of {len(mins)} axes was asked of '{space.name}', whose axes are {axis_names}. A region names a leading prefix of the system's axes; it cannot name more than it has.")

    return {name: [float(low), float(high)] for name, low, high in zip(axis_names, mins, maxs)}


def for_request(info: "Info", space: "models.CoordinateSystem") -> SpaceGraph:
    """This space's graph, built once per request and memoized on the context's loader store."""
    context = info.context
    organization = context.request.organization
    loaders = getattr(context, "_loaders", None)
    if loaders is None:
        return SpaceGraph(space, organization=organization)

    graphs = loaders.setdefault(_LOADER_KEY, {})
    if space.pk not in graphs:
        graphs[space.pk] = SpaceGraph(space, organization=organization, loaders=loaders)
    return graphs[space.pk]
