"""The coordinate system graph (RFC-5 inspired).

Coordinate systems are nodes, transformations are directed edges, and every
spatial fact in the array-dataset world is exactly one node or one edge. Pixel
grids, pyramid levels, crops, physical spaces, registrations and ROIs all live
here; nothing else in the schema carries a duplicate copy of a spatial fact.

Four rules govern this module.

**Edges are facts, paths are queries.** The API ships transformations as
``(input, output, params)`` edges. It does not resolve "to world" on a dataset
or a system, and it stores no composed matrix anywhere: the same dataset can
appear in two scenes under two different registrations, so any single stored
answer would be wrong in one of them. The one sanctioned path *query* hangs off a
layer -- a layer belongs to exactly one scene, so it has a single destination to
answer about (see :class:`core.logic.scene_graph.SceneGraph`).

That query answers in two shapes, and the rule above is what makes both safe.
``Layer.pathToWorld`` gives the ordered list of edges, each with its own validity,
invariance and provenance. ``Layer.asAffine`` gives the same path composed into
one matrix, derived on read like everything else here -- so refining an edge moves
it, and nothing can hold a stale copy. Composing is not the thing the rule forbids;
*storing* the result is, and neither field does.

**Store what was authored or measured; derive everything else.** A registration,
a crop and a physical-space edge took a judgement call, so they are stored. A pyramid
level's absolute scale follows from the shapes, so it is derived once by
:mod:`core.logic.coords` at write time -- and the *result* is stored, never
re-derived at read, so that no two readers can disagree.

**Pixel space is structural; physical space is an interpretation.** A dataset's
INTRINSIC system is its level-0 pixel grid: axes with names and semantic types,
never units. It is always known, never wrong, and never revised, which is why
ROIs and anchors resolve against it. Physical space enters the model exactly
once, as a *physical space*: an ordinary system (axes carrying the units) plus one
edge mapping intrinsic pixels into it. Refining a physical space rewrites that edge;
nothing drawn in pixels moves. The same discipline is *intended* for a
future channel-dependent correction (chromatic drift): a dataset-level fact,
never per-view state on a lens or a layer.

**That correction is not expressible yet, and this paragraph used to claim it was.**
It described the shape as "one ``aligned`` system plus one channel-wise edge" as
though the model already carried it. There is no channel-wise edge: ``input_axes``
and ``output_axes`` select axes *by name*, and nothing on this row -- or anywhere in
the composer -- reads a coordinate **value** to choose parameters. So "for c=2,
translate by (0.3, 0.1)" cannot be written as one edge, and neither can
per-timepoint drift. BY_DIMENSION does not help: naming ``["t"]`` retimes the clock,
it does not make the ``zyx`` block a function of ``t``. The supported pattern today
is one ``Lens`` per channel with its own edge, at the cost of one ``Layer`` each --
see ``Lens``, which states the same thing in the future tense and is the honest one.

**Coordinate systems are nodes, not strings.** RFC-5 nests ``{path, name}``
because Zarr has no global identifiers and a system's name is unique only within
its container. We have IDs, so ``input``/``output`` are foreign keys, exposed as
GraphQL node references: cacheable, dedupable, and unable to dangle. The Zarr
writer translates them back to ``{path, name}`` on serialization. The API shape
and the on-disk shape are deliberately not the same document.
"""

from django.contrib.auth import get_user_model
from django.db import models
from django_choices_field import TextChoicesField
from authentikate.models import Organization
from koherent.fields import ProvenanceField

from core import enums


class CoordinateSystem(models.Model):
    """A coordinate space: a node in the transformation graph, and nothing else.

    The whole model is three concepts. A **space** is a node (this). A **map** between two
    spaces is an edge (:class:`Transformation`). **Data** lives in exactly one space, and says
    so with a foreign key of its own -- ``ArrayDataset.coordinate_system``,
    ``DataArray.coordinate_system``, and the same on every other data model.

    That is the entire ontology. A space does not know what lives in it, does not own
    anything, and carries no classification: ask it for :attr:`residents` and it answers by
    looking at who points at it.

    **Why there is no ownership here any more (RFC-9).** Seven nullable owner FKs used to
    point back at the containers, and they were carrying three jobs, each of which dissolved:

    *Telling a fact from a claim.* ``is_registration_target`` was "no owner FK is set", and
    the walk kept only edges whose output was not one. But that was a second, lossy encoding
    of something :attr:`Transformation.validity` already states -- a pyramid edge is
    VALIDATED because the server derived it, an authored alignment is MANUAL. To know how far
    to trust an edge, read the edge.

    *Lifecycle.* A cascade was how "this space is no longer used" got said. Residence says it
    directly: a space nothing lives in and no scene composes over is garbage, and
    ``deleteOrphanedCoordinateSystems`` collects it together with its edges.

    *``kind``.* It only ever labelled which container pointed back, and the honest question
    about a space -- what data lives here -- is a list, not an enum.

    The old shape also forced two special cases that are simply gone. A *calibration* was a
    dataset-owned PHYSICAL system; it is now an ordinary space with an edge into it, which is
    all it ever was, and several datasets may share one. And a level-0 array and an unsliced
    lens used to own *no* system, with a null standing in for "the dataset's own grid"; they
    now point at that grid like everything else.
    """

    name = models.CharField(max_length=255, help_text="The name of the coordinate space")

    epoch = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "The wall-clock instant this system's time axis has its origin at, so that "
            "`wall_clock = epoch + t * unit`. A property of the *space*, not of any composition over it -- "
            "two scenes sharing one space cannot disagree about when its clock starts. Meaningful only for "
            "a space with a unit-carrying TIME axis; optional even there: an unanchored clock is still a "
            "perfectly composable relative coordinate"
        ),
    )

    # A space has no foreign key to anything now, so `core.scoping._find_org_path` has no
    # path to follow and every scoped read would raise LookupError without this column. It
    # was already required when the owner FKs existed, because all seven were nullable and
    # that walk follows only required ones.
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization this coordinate space belongs to")
    creator = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, help_text="The user that created this coordinate space")
    created_at = models.DateTimeField(auto_now_add=True, help_text="The time this coordinate space was created")

    provenance = ProvenanceField()

    def __str__(self) -> str:
        """The space's name."""
        return self.name


class Axis(models.Model):
    """One named, typed dimension of a coordinate system.

    ``order`` is this axis' position in the system's canonical axis order, and that
    identity is load-bearing: it is what makes "the last spatial axis is x" a
    well-defined statement. For an array-backed system (INTRINSIC/ARRAY) that
    position is the index into the array shape, and it is what ties ``scale[i]`` to
    ``shape[i]``; for a TABLE system it is the canonical order of the coordinate
    columns, and no shape exists to index. Nothing recovers it if it drifts, so it
    is enforced unique per system and always written by enumeration, never supplied
    by a caller.

    **No ordering by axis *type* is required.** RFC-5 states one -- time, then channel
    and custom types, then space -- and it was enforced at ingest for every array-backed
    system, but nothing reads it: :func:`core.logic.coords.resolve_render_axes` finds the
    time, channel and phasor axes by type rather than by position. Enforced, it refused
    ``(z, c, y, x)`` and ``(c, z, y, x)`` -- ordinary ways to write an acquisition -- to
    protect a derivation that never consulted it, and it accepted ``t, x, y`` while
    refusing ``x, y, t`` though both derive exactly the same render axes.

    What the derivation actually reads is narrower: the relative order of the **spatial**
    axes, the last being x, the one before it y. Axis *names* are free-form ("z", "tau"),
    and ``zyx`` ordering among the spatial axes is only a convention -- an unguarded one,
    which is why ``x, y, z`` still derives ``x=z`` when the names are not the screen's.
    The type ordering never guarded that either.

    What is still required is that ``order`` be the store's dimension order, which is the
    rule that genuinely describes bytes. ``create_array_dataset`` checks the axes against
    the store's shape, and every writer assigns ``order`` by enumeration.
    """

    coordinate_system = models.ForeignKey(CoordinateSystem, on_delete=models.CASCADE, related_name="axes", help_text="The coordinate system this axis belongs to")
    order = models.PositiveSmallIntegerField(help_text="This axis' position in the system's canonical axis order. For an array-backed system it is the index into the array shape; for a table system it is the order of the coordinate columns")
    name = models.CharField(max_length=32, help_text="The name of the axis, e.g. 'z', 'c' or 'tau'. Free-form")
    type = TextChoicesField(
        choices_enum=enums.AxisTypeChoices,
        default=enums.AxisTypeChoices.SPACE.value,
        help_text="The semantic kind of the axis, which fixes its position in the axis ordering and drives render-axis derivation",
    )
    unit = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="The physical unit of the axis, e.g. 'micrometer'. A pint unit (the kanne `Unit` scalar), validated on write; 'a.u.' for arbitrary units. Set on unit-carrying axes (a physical space, a world), always null on pixel-grid axes",
    )
    long_name = models.CharField(max_length=255, null=True, blank=True, help_text="A human-readable name for the axis")
    description = models.CharField(max_length=1000, null=True, blank=True, help_text="A free-form description of what the axis measures, e.g. 'distance from the coverslip'")

    class Meta:
        """Meta options for the axis."""

        ordering = ["order"]
        unique_together = [("coordinate_system", "order"), ("coordinate_system", "name")]
        constraints = [
            # A system carries at most one clock. Two TIME axes are otherwise legal --
            # they share an ordering rank, so nothing complains -- and the render-axis
            # derivation silently picks the first and drops the second. A partial unique
            # index, so the rule holds against a write that skips the validation.
            models.UniqueConstraint(
                fields=["coordinate_system"],
                condition=models.Q(type=enums.AxisTypeChoices.TIME.value),
                name="one_time_axis_per_coordinate_system",
            ),
        ]

    def __str__(self) -> str:
        """The axis' name and type."""
        return f"{self.name} ({self.type})"


class Transformation(models.Model):
    """A directed edge of the coordinate graph: a map from ``input`` to ``output``.

    One table, discriminated by ``kind``, with the parameters in JSON -- the same
    shape as ``Layer``, and for the same reason. In GraphQL it is an interface
    whose concrete types unpack ``params`` into typed fields.

    **The converse of the layer's rule (RFC-8).** This row is the *sole carrier* of
    how two spaces relate: the map (``kind`` + ``params``), how well it is known
    (``validity``), what a derivation did to the values (``value_relation``), and -- through
    ``provenance`` -- every refinement it has been through. A view never keeps a copy of any of
    them -- ``Layer.affine_matrix`` and ``Layer.validity`` were exactly that, and
    two layers over one dataset were free to disagree about one registration. What a
    *path* says is derived from these rows and stored nowhere, which is why fixing one
    edge fixes every layer that looks through it.

    Two properties are read off this row rather than stored beside it, for the same
    reason ``CoordinateSystem.kind`` reads its owner FKs: whether the map can be undone
    (:func:`core.logic.graph.is_invertible`) and what it preserves
    (:func:`core.logic.graph.invariance_of` -- isometry, similarity, affine,
    diffeomorphic, nothing). A stored copy of either could contradict ``params``, and
    ``params`` would be right.

    **Direction is always forward: input to output.** Registration libraries
    routinely hand you the inverse map; normalize it at ingest rather than
    recording the direction, or half the graph will point the wrong way and
    nothing will tell you.

    **Not every edge is a map.** An ``UNMAPPABLE`` edge asserts the opposite: the
    two systems are related, and no point of either corresponds to a point of the
    other. It takes no parameters, is bound by no rank, and
    :func:`core.logic.graph.is_traversable` refuses it to every placement search --
    in both directions. It may not be a wrapper child: a SEQUENCE one of whose
    steps maps nothing maps nothing, and would be better written as the one edge
    it is.

    Two limits worth naming, because a reader will assume they were handled.
    Whether an edge can be walked *backwards* is decided by kind and rank
    (:func:`core.logic.graph.is_reverse_traversable`), which is metadata -- so a
    square but **singular** AFFINE (a projection written as a matrix, ``[1,1,0]``)
    is still offered for inversion. Only a determinant catches that, and one does:
    :func:`core.inputs.coords.assert_nonsingular_matrix` runs on every write, both
    through the input union and through :func:`core.logic.graph.assert_edge_values`,
    so the row cannot exist to be offered. The gap is closed one altitude down; this
    predicate stays kind-only on purpose. And a FIELD has no closed-form inverse at
    any rank, which is why kind, and not rank alone, decides.
    """

    kind = TextChoicesField(
        choices_enum=enums.TransformKindChoices,
        default=enums.TransformKindChoices.IDENTITY.value,
        help_text="The kind of transformation, which fixes how `params` is interpreted",
    )
    name = models.CharField(max_length=255, null=True, blank=True, help_text="The name of the transformation")

    # Null only for a child of a SEQUENCE / BY_DIMENSION wrapper --
    # RFC-5 permits omitting them there, because the wrapper supplies them.
    input = models.ForeignKey(CoordinateSystem, on_delete=models.CASCADE, null=True, blank=True, related_name="+", help_text="The coordinate system this transformation maps from")
    output = models.ForeignKey(CoordinateSystem, on_delete=models.CASCADE, null=True, blank=True, related_name="+", help_text="The coordinate system this transformation maps to")

    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children", help_text="The wrapping SEQUENCE / BY_DIMENSION transformation, if this is a child")
    order = models.PositiveSmallIntegerField(default=0, help_text="The position of this child within its wrapping SEQUENCE, applied first to last")

    # BY_DIMENSION only: which axes this child acts on, by NAME. RFC-5's
    # transformation table says List[str] while its byDimension prose says
    # integer indices into the axes; the two contradict. Names are
    # self-documenting and survive a reordering, so we use names.
    input_axes = models.JSONField(null=True, blank=True, help_text="(byDimension) The names of the input axes this child acts on, e.g. ['z', 'y', 'x']")
    output_axes = models.JSONField(null=True, blank=True, help_text="(byDimension) The names of the output axes this child produces")

    params = models.JSONField(
        default=dict,
        help_text="The transformation's parameters, keyed by kind: SCALE {'scale': [...]}, TRANSLATION {'translation': [...]}, AFFINE {'affine': [[...], ...]} (M x (N+1), rows outermost), UNMAPPABLE {'reason': '...'} (optional, purely descriptive)",
    )

    # An edge fact, deliberately: it used to be a column on the layer, where two layers
    # over one dataset carried two copies of how-known one registration is, free to
    # disagree -- and nothing ever wrote it. A layer's validity is now derived: the
    # weakest edge on its path to world.
    validity = TextChoicesField(
        choices_enum=enums.PlacementValidityChoices,
        default=enums.PlacementValidityChoices.VALIDATED.value,
        help_text=(
            "How much this map is actually known. VALIDATED is the default because most edges are derived "
            "by the server from shapes and slices -- exact by construction. A writer that merely reads "
            "metadata says INFERRED, one that records an authored registration says MANUAL, and an edge "
            "the server assumed says UNKNOWN"
        ),
    )
    value_relation = TextChoicesField(
        choices_enum=enums.ValueRelationChoices,
        null=True,
        blank=True,
        help_text=(
            "What the derivation this edge records did to the *values* -- the axis the spatial kind says "
            "nothing about: a threshold is spatially IDENTITY with CATEGORIZED values, a crop is "
            "value-IDENTICAL, a deconvolution TRANSFORMED. One derivation event, one row, two orthogonal "
            "statements -- never a parallel table. Null means unstated. Meaningful only on a derivation "
            "(cross-container fact) edge: a registration relates spaces, and values do not cross it"
        ),
    )

    # FIELD only: the array whose values are the map. A *node*, not a store hanging
    # off this edge. The array is data before it is a map -- a label mask has its own
    # lineage, provenance and placement -- and a payload cannot carry any of that. It
    # also cannot carry axes, which is what once left AxisType.COORDINATE and
    # AxisType.DISPLACEMENT with nothing to sit on: the fact "my values are offsets" had no
    # array to attach to. As a node it does, and this edge reads it rather than restating
    # it -- so both types are live on the read side now (`graph._VALUE_AXIS_TYPES`),
    # even though no production writer emits one yet. Matches DataArray, which owns a system and reaches its dataset's intrinsic
    # space through a stored Transformation: arrays are nodes; edges relate their spaces.
    #
    # **Null means the input is its own field** -- a label mask, whose pixels are the map
    # of the space they index. The same shape as a level-0 DataArray owning no system and
    # an unsliced Lens owning no system: when the answer would be "that one, there", this
    # codebase stores nothing and lets the definition carry it. It is not only an idiom
    # here, it is load-bearing. PROTECT is right for a *separate* array -- deleting a warp
    # field would take a registration nobody named -- but a self-dereference is a fact
    # ABOUT the mask, which `input`'s CASCADE already removes with it. Written as a real
    # self-FK, PROTECT would win that race and the mask could never be deleted at all.
    # Read it through `effective_field`, never directly.
    field = models.ForeignKey(
        CoordinateSystem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fields_of",
        help_text="(FIELD) The coordinate system of the array whose values are this map, when that array is a separate one (a warp field). Null when the input is its own field, as for a label mask whose pixels are the map. Its value axis (COORDINATE or DISPLACEMENT) says whether the values are positions or offsets; no value axis means scalar, and scalar means positions",
    )

    @property
    def effective_field(self) -> "CoordinateSystem | None":
        """The array whose values are this map: the `field`, or the input when it is its own.

        The one reader of the null-means-self convention above. Everything that resolves a
        FIELD's map goes through here, so the convention lives in exactly one place.
        """
        if self.kind != enums.TransformKindChoices.FIELD.value:
            return None
        return self.field or self.input

    # **Where along an axis this edge applies.** Null -- and almost always null -- for an edge
    # that holds everywhere, which is every edge the model had before this column.
    #
    # This is the one thing the graph could not say. `input_axes` and `output_axes` select axes
    # *by name*; nothing else on this row, and nothing in either composer, ever read a coordinate
    # **value** to choose parameters. So chromatic drift ("for c=2, translate by (0.3, 0.1)") and
    # per-timepoint drift correction -- the two most ordinary registration problems in the domain
    # -- could only be expressed as one Lens per channel or timepoint, each with its own system,
    # its own edge, and its own Layer. A hundred-frame drift correction was a hundred layers.
    #
    # A selector-scoped edge is a **partial** map: it is the map where the input coordinate along
    # `axis` equals `index`, and it says nothing anywhere else. Several of them over one axis are
    # one piecewise map, written as the several facts they are rather than as one row with a list
    # in it -- so refining the correction for one channel rewrites one row and moves one channel.
    #
    # **A discrete index, not a range or a continuous position.** `assert_edge_rank` refuses a
    # SPACE axis here and points at FIELD instead: "at x = 3.7" is not a case a piecewise-constant
    # map answers, and an array whose values are the map already answers it exactly. A range would
    # be the natural extension and is deliberately not built -- the shape below leaves room for it
    # without a second column.
    selector = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Where along one axis this edge applies, as {'axis': 'c', 'index': 2}: the map holds where the input coordinate along that axis "
            "equals that index, and makes no claim elsewhere. Null for an edge that holds everywhere, which is the usual case. Several "
            "selector-scoped edges over one axis compose into a piecewise map. A path query only crosses one when it fixes that coordinate "
            "(`at`), because without it the answer genuinely depends on where you are"
        ),
    )

    # How many times this edge has been refined is *not* a column, though it is still a
    # field: `provenance` below writes a history row per save, so a counter beside it would
    # be a second record of one fact -- and the weaker of the two, because it says only that
    # something changed and every writer has to remember to keep it. Only
    # `updateTransformation` ever did, so any other write left a chain that had moved looking
    # as though it had not. `core.types.coords.Transformation.version` counts those rows for
    # one edge and `core.logic.graph.transform_version` counts them along a chain; both
    # answer what the column answered, and neither can be forgotten.

    # See CoordinateSystem.organization: a wrapper child has input, output and
    # every non-self FK null, so there is no path to an organization without it.
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization this transformation belongs to")
    creator = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, help_text="The user that created this transformation")
    created_at = models.DateTimeField(auto_now_add=True, help_text="The time this transformation was created")

    provenance = ProvenanceField()

    class Meta:
        """Meta options for the transformation."""

        ordering = ["order", "id"]

    def __str__(self) -> str:
        """The transformation's kind and endpoints."""
        return f"{self.kind}: {self.input_id} -> {self.output_id}"


class MeshCollection(models.Model):
    """An immutable, versioned collection of meshes, addressed by store rather than by row.

    The collection resolves to a **Parquet store** and a schema; the client asks the
    datalayer for temporary read credentials and queries the Parquet directly (e.g.
    with DuckDB). It is the same upload path every other Parquet object in the
    system takes -- the client requests a presigned upload, writes the object, and
    hands back the store id. A bare URL would sit outside the datalayer: nothing
    would grant read access to it, nothing would scope it to an organization, and
    nothing would clean it up.

    It deliberately exposes no ``meshes`` field: a paginated list would look
    natural, someone would build a UI on it, and it would end up walking tens of
    millions of Parquet rows through GraphQL to feed a render loop.

    **It has a coordinate system of its own** (``MeshCollection.coordinate_system``;
    ownership FKs are gone since RFC-9, so the space points at nothing back), and
    an edge relates that system to the
    dataset the meshes were extracted from. It used to *borrow* the dataset's
    intrinsic system, which forced the vertices to be exactly in that pixel grid
    and left the geometry nowhere to say otherwise -- extract from a
    half-resolution grid and the only honest options were to rewrite every vertex
    or to store a scale factor somewhere no query could find it. As an edge it is
    a fact like any other: an identity when the grids agree, a scale when they do
    not, and refinable without touching a vertex. The edge is *optional*: a mesh in
    some absolute space, belonging to no dataset, simply has none.
    """

    version = models.CharField(max_length=64, help_text="The immutable version of this collection, e.g. 'v20260713-a3f9'")
    spec_version = models.CharField(max_length=64, help_text="The version of the mesh encoding specification this collection conforms to")

    # Filing, not placement -- see the note on `ArrayDataset.folder`. A versioned collection is
    # still one filed thing: each version is its own row, so each is filed on its own.
    folder = models.ForeignKey(
        "Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mesh_collections",
        help_text="The folder this mesh collection is filed in. Organisational only -- it says nothing about where the meshes sit in space",
    )

    # cellSize is IN VOXELS, so that the octree aligns to the label grid it was
    # extracted from rather than to an arbitrary physical box.
    grid = models.JSONField(default=dict, help_text="The octree grid, as read from the store's manifest: {'cellSize': [128, 128, 64], 'levels': 3, 'sortKey': 'MORTON'}. cellSize is in voxels, one size per vertex component")
    encoding = models.JSONField(default=dict, help_text="The geometry encoding, as read from the store's manifest: how positions, normals and indices are packed and compressed")
    # **The collection is its store**: one fabriks prefix holding `fabriks.json`, both catalogs
    # and every octree level. `fill_info` reads that manifest at registration, so the grid and
    # the encoding above record what the writer wrote rather than what a caller retyped.
    #
    # **Required.** A collection with no store would be a row describing geometry nothing can
    # address. There is no state in which a collection exists and its bytes do not.
    store = models.ForeignKey(
        "datalayer.FabriksStore",
        on_delete=models.CASCADE,
        related_name="mesh_collections",
        help_text="The fabriks store holding this collection: one prefix with its manifest, both catalogs and every octree level. Its manifest is where the grid and the encoding come from",
    )
    provenance_metadata = models.JSONField(default=dict, help_text="How this collection was produced (the extraction run, its parameters and its inputs)")

    coordinate_system = models.ForeignKey(
        "CoordinateSystem",
        on_delete=models.PROTECT,
        # Nullable in the database only because the `historical*` twin carries rows written
        # before this column existed, and a history row must be allowed to say "not
        # recorded". Every write path sets it, so a live row never has none.
        null=True,
        blank=True,
        related_name="mesh_collections",
        help_text="The coordinate system this collection's vertices are expressed in. An edge relates it to whatever the meshes were extracted from -- an identity when the grids agree, a scale when they do not",
    )

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization this mesh collection belongs to")
    creator = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, help_text="The user that created this mesh collection")
    created_at = models.DateTimeField(auto_now_add=True, help_text="The time this mesh collection was created")

    provenance = ProvenanceField()

    class Meta:
        """Meta options for the mesh collection."""

        # No `unique_together` on (coordinate_system, version) any more: the system is
        # this collection's own, so the pair was unique by construction and the
        # constraint said nothing. What it used to mean -- one version per anchor --
        # is now a statement about the *anchor edge*, and the graph does not enforce
        # uniqueness on edges anywhere else either.
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """The collection's version."""
        return f"MeshCollection {self.version}"


class NetworkCollection(models.Model):
    """An immutable, versioned collection of node/edge networks, addressed by store.

    The graph sibling of :class:`MeshCollection`: traced neurons, vessel trees, skeletons,
    connectomes, tracking graphs with divisions. Everything :class:`MeshCollection`'s docstring
    says about *why* a collection is a store rather than a row set applies here unchanged --
    it exposes no ``nodes`` field for the same reason, and it owns a coordinate system of its
    own with an edge relating it to whatever the network was traced out of.

    **What differs is what the encoding declares, and both differences matter to a reader.**

    ``encoding.edges`` gives the index arity. A konnektion blob is a segment list -- two indices
    per edge -- and a mesh blob is a triangle list. Nothing about the bytes distinguishes them:
    read at the wrong arity the length still divides, every index is still in range, and the
    picture is a plausible, wrong graph. That is why the two formats are two stores and two
    scalars rather than one with a flag.

    ``encoding.pruning`` and ``encoding.simplification`` say whether any level is a reduction of
    another. A mesh pyramid always coarsens; a network one often does not, because konnektion
    chooses its depth from the data and a traced arbor of a few thousand nodes gets a single
    level with nothing pruned and nothing straightened. ``NONE`` for both is the common case,
    and it is a statement rather than a silence.
    """

    version = models.CharField(max_length=64, help_text="The immutable version of this collection, e.g. 'v20260713-a3f9'")
    spec_version = models.CharField(max_length=64, help_text="The version of the network encoding specification this collection conforms to")

    folder = models.ForeignKey(
        "Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="network_collections",
        help_text="The folder this network collection is filed in. Organisational only -- it says nothing about where the networks sit in space",
    )

    # cellSize is IN VOXELS, so that the octree aligns to the grid the network was traced in
    # rather than to an arbitrary physical box.
    grid = models.JSONField(default=dict, help_text="The octree grid, as read from the store's manifest: {'cellSize': [128, 128, 64], 'levels': 1, 'sortKey': 'MORTON'}. cellSize is in voxels, one size per position component. `levels: 1` is the common case, not a degenerate one")
    encoding = models.JSONField(default=dict, help_text="The geometry encoding, as read from the store's manifest: how positions, edges, node ids, radii and ghosts are packed, and which coarsening operations ran")
    store = models.ForeignKey(
        "datalayer.KonnektionStore",
        on_delete=models.CASCADE,
        related_name="network_collections",
        help_text="The konnektion store holding this collection: one prefix with its manifest, both catalogs and every octree level. Its manifest is where the grid and the encoding come from",
    )
    provenance_metadata = models.JSONField(default=dict, help_text="How this collection was produced (the tracing run, its parameters and its inputs)")

    coordinate_system = models.ForeignKey(
        "CoordinateSystem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="network_collections",
        help_text="The coordinate system this collection's node positions are expressed in. An edge relates it to whatever the network was traced out of",
    )

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization this network collection belongs to")
    creator = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, help_text="The user that created this network collection")
    created_at = models.DateTimeField(auto_now_add=True, help_text="The time this network collection was created")

    provenance = ProvenanceField()

    class Meta:
        """Meta options for the network collection."""

        ordering = ["-created_at"]

    def __str__(self) -> str:
        """The collection's version."""
        return f"NetworkCollection {self.version}"
