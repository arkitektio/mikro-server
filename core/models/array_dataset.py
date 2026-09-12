
from django.db import models
from django.contrib.auth import get_user_model
from core import enums
from koherent.fields import ProvenanceField
from django_choices_field import TextChoicesField
from authentikate.models import Organization
from django.db.models import Q
from datalayer.models import MediaStore, ZarrStore
from django.contrib.postgres.indexes import GinIndex
from core import base_models
from core.logic import coords as coords_logic
from core.models.coords import CoordinateSystem, Transformation, MeshCollection  # noqa: F401  (re-exported via core.models)


class ArrayDataset(models.Model):
    """A multi-dimensional array of data, with one or more pyramid levels attached as DataArrays.

    The dataset's dimensions and their types live on the axes of its INTRINSIC
    :class:`~core.models.CoordinateSystem` -- its level-0 pixel grid -- and its
    shape is the shape of its level-0 array. Physical units live on its
    physical spaces (unit-carrying systems), never here. Almost none of it is duplicated on
    columns: the properties below derive it, so there is no second copy that can
    disagree. That includes ``multiscale``, which is simply "more than one level".

    The one materialized exception is ``stored_spec`` (read back through the ``spec``
    property): the list of :class:`~core.enums.ArrayDatasetSpec` the axes satisfy, written
    once at creation. It is safe to store precisely because the axes are immutable (see
    below) -- a value computed from immutable inputs at write time cannot disagree with
    its source, the same reason ``DataArray`` stores its absolute scale on the edge at
    write time rather than re-deriving it. The single source of truth stays
    :func:`core.logic.coords.specs_for_axes`; the column is materialized *from* it by the
    axis writer, never re-derived on read.

    **Only ``name`` and ``description`` are editable**, through ``updateArrayDataset``. Everything
    that says where the data *is* -- the arrays, the axes, the systems built from them -- is
    written at creation and never after: ``Axis.order`` is written by enumeration and the rest
    of the graph is measured against it, so an axis edit is a different space rather than a
    correction, and ``updateCoordinateSystem`` refuses a dataset's own system for that reason
    (it serves shared spaces alone). A recomputation is a new dataset.

    Both editable fields are audited. ``provenance`` records a history row per save, attributed
    to the client, user and task the change happened under, and reads back as
    ``provenanceEntries``. A rename is the only thing about a dataset that can change, which is
    exactly why it is worth knowing who changed it.
    """

    name = models.CharField(max_length=1000, help_text="The name of the data source")
    description = models.CharField(max_length=1000, help_text="The description of the data source", null=True)

    # Residence, not ownership (RFC-9). The dataset lives in a space; the space does not
    # belong to the dataset. A plain FK rather than a one-to-one because several datasets
    # genuinely may share one frame -- a hundred tiles acquired on one stage -- while two
    # unrelated acquisitions get their own because the writer creates one each.
    coordinate_system = models.ForeignKey(
        CoordinateSystem,
        on_delete=models.PROTECT,
        # Nullable in the database only because the `historical*` twin carries rows written
        # before this column existed, and a history row must be allowed to say "not
        # recorded". Every write path sets it, so a live row never has none.
        null=True,
        blank=True,
        related_name="datasets",
        help_text="The coordinate system this dataset's pixels are expressed in: its level-0 grid. PROTECT, because a space cannot be deleted while data lives in it",
    )

    # A *choice*, not a fact, which is the whole reason it may be stored. Which scenes show
    # this dataset is derivable from the graph and is `ArrayDataset.scenes`; a second copy of that
    # would be free to disagree with it, and `core.logic.scene` rejects one by name. Which of
    # them to open first is derivable from nothing -- a dataset anchored in five scenes leaves
    # the graph with no opinion and a person with one. This records the opinion.
    #
    # Inert, in the same register as `AnnotationCollection.scene`: no walk crosses it, it gates
    # no placement, and nothing but the thumbnail lookup reads it. SET_NULL for that model's
    # reason too -- deleting a scene must never delete or hide the data it showed.
    default_scene = models.ForeignKey(
        "Scene",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for",
        help_text=(
            "The scene to open for this dataset, and the one its thumbnail is taken from. Bookkeeping only: it claims nothing about where the data sits and is not an answer to "
            "which scenes *show* this dataset -- that is `scenes`, a question the coordinate graph answers. Several datasets may nominate one scene. Cleared, not cascaded, when the scene is deleted"
        ),
    )

    # Filing, not placement. A folder says where a user keeps this dataset; it says nothing
    # about what space the data is in -- that is the coordinate graph's job, and the two must
    # never be read as one. Nullable because a dataset can exist unfiled, and because every
    # row predating this column has no answer; the create mutation files new ones in the
    # user's default folder, exactly as `create_image_from_array` has always done for images.
    folder = models.ForeignKey(
        "Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="array_datasets",
        help_text="The folder this dataset is filed in. Organisational only -- it says nothing about where the data sits in space",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="The time the data source was created")
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, help_text="The user that created the data source")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization the data source belongs to")
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this object was created through, if any",
    )
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_%(class)ss",
        help_text="The assigner of the creating task, denormalized for fast filtering",
    )
    provenance = ProvenanceField()

    stored_spec = models.JSONField(
        default=list,
        help_text=(
            "What this dataset structurally is: the raw ArrayDatasetSpec values (one spatial member plus a "
            "modifier per acquisition axis) that its intrinsic axes satisfy, materialized at creation by "
            "the axis writer from core.logic.coords.specs_for_axes. Immutable because the axes are, so it "
            "cannot disagree with them. Read it back as enum members through the `spec` property. Empty "
            "while the intrinsic system does not exist yet."
        ),
    )

    class Meta:
        indexes = [GinIndex(fields=["stored_spec"], name="array_dataset_spec_gin")]

    @property
    def intrinsic_coordinate_system(self):
        """The space this dataset's pixels live in. An alias for `coordinate_system`.

        Kept because a great deal of the graph layer says "intrinsic" to mean "the grid the
        geometry is measured against", and that is still what this is -- it is simply no
        longer a *kind* of system, just the one this dataset lives in.
        """
        return self.coordinate_system

    @property
    def multiscale(self) -> bool:
        """Whether this dataset carries a resolution pyramid. Derived: more than one level."""
        return self.data_arrays.count() > 1

    @property
    def axes(self) -> list:
        """The dataset's axes, in array order."""
        system = self.intrinsic_coordinate_system
        return list(system.axes.all()) if system else []

    @property
    def axis_specs(self) -> list[coords_logic.AxisSpec]:
        """The dataset's axes, coerced for :mod:`core.logic.coords`."""
        return [coords_logic.AxisSpec(name=axis.name, type=axis.type) for axis in self.axes]

    @property
    def axis_names(self) -> list:
        """The dataset's axis names, in array order. Derived from the intrinsic axes."""
        return [axis.name for axis in self.axes]

    @property
    def spec(self) -> list:
        """Every spec this dataset's axes satisfy: what it structurally is.

        Read from ``stored_spec``, materialized at creation from the intrinsic
        axes -- not re-derived on read. The axes are immutable, so the column
        cannot disagree with them; this is the same write-time materialization as
        ``DataArray``'s absolute scale.

        Empty -- not SCALAR -- when the intrinsic system did not exist at creation:
        a dataset whose axes are unknown has no spatial extent to report, and
        claiming SCALAR would say it has none. A genuine no-SPACE-axis dataset
        stores ``['SCALAR', ...]``, so the two stay distinguishable.
        """
        return [enums.ArrayDatasetSpec(value) for value in self.stored_spec]

    @property
    def shape_list(self) -> list:
        """The dataset's shape: that of its level-0 array."""
        base = self.data_arrays.order_by("level").first()
        return base.shape if base and isinstance(base.shape, list) else []

    def phasor_histogram_at(self, axis: str, harmonic: int):
        """The persisted phasor distribution over an axis at a harmonic, across all this dataset's anchors."""
        return PhasorHistogram.objects.filter(anchor__dataset=self, axis=axis, harmonic=harmonic).first()

    def phasor_calibrations_at(self, axis: str, harmonic: int):
        """The instrument-response correction for an axis at a harmonic, across all this dataset's anchors."""
        return PhasorCalibration.objects.filter(anchor__dataset=self, axis=axis, harmonic=harmonic).first()


class DataArray(models.Model):
    """One level of a dataset's resolution pyramid: a zarr-backed array.

    A downsampled level's voxel-index space is an ARRAY
    :class:`~core.models.CoordinateSystem`, and the map from that space into the
    dataset's intrinsic space is a stored :class:`~core.models.Transformation`.
    Every level maps into the *same* intrinsic system -- a star, not a chain -- so
    no level's placement depends on another's. Level 0 owns no system and no edge:
    the INTRINSIC system *is* the level-0 pixel grid, by definition, and a second
    node for the same space joined by an all-ones SCALE edge would record nothing
    (see :attr:`space`).

    The old ``scale_factors`` column is gone. It stored *nominal* factors
    (1, 2, 4, 8, ...), which a real pyramid does not obey: a 36-voxel axis floors
    to 36, 18, 9, 4, 2, 1, whose true factors are 1, 2, 4, 9, 18, 36. The
    absolute scale is now derived from the actual shapes at write time, by
    :func:`core.logic.coords.pyramid_transform`, and stored on the edge.
    """

    store = models.ForeignKey(
        ZarrStore,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The store of the data array",
    )
    shape = models.JSONField(help_text="The shape of the data array")
    chunk_shape = models.JSONField(help_text="The chunk shape of the data array")

    dataset = models.ForeignKey(ArrayDataset, on_delete=models.CASCADE, related_name="data_arrays")
    level = models.IntegerField(help_text="The level of the data array in the resolution pyramid, 0 being the highest resolution")

    # A fact of the level, not of the dataset -- the same reason `shape` lives here: a
    # pyramid may well have been built one level at a time by different code. Null on level
    # 0, which was not downsampled from anything, and null on a level whose writer did not
    # say. Stated rather than derived: two arrays are all that survives a downsample, and
    # nothing in the numbers says whether they were averaged or picked.
    scale_method = TextChoicesField(
        choices_enum=enums.ScaleMethodChoices,
        null=True,
        blank=True,
        help_text="How this level's voxels were computed from the level above it. Null for level 0, and for a level whose writer did not say. Over a dataset whose values are object ids only NEAREST and MODE are accepted -- everything else returns numbers that were not in the input, and an invented id is an object that does not exist",
    )

    # Always set, including for level 0 -- which is where this shape pays off. Level 0 used
    # to own *no* system, with a null and a "means the dataset's own grid" convention to
    # explain it; under residence it simply lives in that grid, pointing at the same node the
    # dataset does. The special case is gone rather than ported.
    coordinate_system = models.ForeignKey(
        CoordinateSystem,
        on_delete=models.PROTECT,
        # Nullable in the database only because the `historical*` twin carries rows written
        # before this column existed, and a history row must be allowed to say "not
        # recorded". Every write path sets it, so a live row never has none.
        null=True,
        blank=True,
        related_name="data_arrays",
        help_text="The coordinate system this level's voxels are expressed in. Level 0 shares its dataset's; every downsampled level has its own, with a stored edge relating the two",
    )

    class Meta:
        """Meta options for the data array."""

        # Everything -- the dataset's shape, the lens edges, the pyramid
        # derivation -- keys off "the level-0 array". Two arrays claiming the
        # same level would make all of it silently ambiguous.
        constraints = [
            models.UniqueConstraint(fields=["dataset", "level"], name="one_data_array_per_level"),
        ]

    @property
    def space(self):
        """The coordinate system this level's voxels live in.

        Level 0 owns no system: the dataset's INTRINSIC system *is* the level-0 pixel
        grid, by definition, so this resolves to it. Higher levels own an ARRAY system
        and a stored edge into intrinsic.
        """
        return getattr(self, "coordinate_system", None) or (self.dataset.intrinsic_coordinate_system if self.level == 0 else None)

    @property
    def to_parent(self):
        """The stored edge from this level's voxel space into the dataset's intrinsic space.

        None for level 0: its space IS the intrinsic space, and an identity edge between one
        space and itself would be a stored fact carrying no information.

        **Both endpoints, and top-level only.** Filtering on ``input`` alone used to be enough,
        back when a level-0 array owned no system: the only edge out of a level's own space was
        its own. Under residence (level 0 shares the dataset's grid) every physical-space edge
        and every registration also leaves that space, so an input-only filter returned whichever
        of them sorted first under ``Meta.ordering`` -- a registration into a world, presented as
        the pyramid edge, exactly where this docstring promises ``None``. Naming the output pins
        it to the one edge `core.logic.graph.create_level_edge` wrote, and ``parent__isnull``
        keeps a SEQUENCE's child from standing in for its wrapper.
        """
        system = getattr(self, "coordinate_system", None)
        intrinsic = self.dataset.intrinsic_coordinate_system
        if system is None or intrinsic is None or system.pk == intrinsic.pk:
            return None
        return Transformation.objects.filter(input=system, output=intrinsic, parent__isnull=True).first()


# ==========================================
# 2. THE HUB & SPOKES (Metadata)
# ==========================================


class CoordinateAnchor(models.Model):
    """The Axis-Agnostic Hub."""

    id = models.BigAutoField(primary_key=True)
    dataset = models.ForeignKey(ArrayDataset, related_name="anchors", on_delete=models.CASCADE)
    coordinates = models.JSONField(
        default=dict,
        help_text="The coordinates this anchor is pinned to, keyed by axis name, e.g. {'c': 0, 't': 5}. Level-0 pixel indices (the dataset's INTRINSIC space). An omitted axis means global along it",
    )

    class Meta:
        indexes = [GinIndex(fields=["coordinates"], name="anchor_coords_gin")]


class OptikitState(models.Model):
    """1:1 Spoke (Hardware Truth)"""

    anchor = models.OneToOneField(CoordinateAnchor, related_name="microscope", on_delete=models.CASCADE)
    state = models.JSONField(default=dict)


class OmeMetadata(models.Model):
    """N:1 Spoke (Image Truth)"""

    anchor = models.OneToOneField(CoordinateAnchor, related_name="ome_metadata", on_delete=models.CASCADE)
    metadata = models.JSONField(default=dict)


class ValueHistogram(models.Model):
    """N:1 Spoke (Pixel Value Distribution)"""

    anchor = models.OneToOneField(CoordinateAnchor, related_name="value_histogram", on_delete=models.CASCADE)
    histogram = models.JSONField(default=list, help_text="The histogram of the pixel values (y values)")
    bins = models.JSONField(default=list, help_text="The bin indices of the histogram (x values)")
    min = models.FloatField(help_text="The minimum pixel value of the histogram", null=True, blank=True)
    max = models.FloatField(help_text="The maximum pixel value of the histogram", null=True, blank=True)
    p1 = models.FloatField(help_text="The first percentile of the pixel values", null=True, blank=True)
    p99 = models.FloatField(help_text="The 99th percentile of the pixel values", null=True, blank=True)


class ChannelLabel(models.Model):
    """N:1 Spoke (Channel Truth)"""

    anchor = models.OneToOneField(CoordinateAnchor, related_name="channel_label", on_delete=models.CASCADE)
    label = models.CharField(max_length=1000, help_text="The label of the channel", null=True, blank=True)


class LightPath(models.Model):
    """N:1 Spoke (Light Path Truth)"""

    anchor = models.OneToOneField(CoordinateAnchor, related_name="light_graph", on_delete=models.CASCADE)
    graph = models.JSONField(default=dict)


class PhasorHistogram(models.Model):
    """N:1 Spoke (Phasor Distribution).

    The phasor's answer to :class:`ValueHistogram`: a client seeds the contrast limits of an
    intensity channel from the value histogram without reading the array, and it seeds the
    value range of a phasor overlay from this without streaming a whole TCSPC or hyperspectral
    cube.

    A ForeignKey where every other spoke is a OneToOne, because the anchor's ``coordinates``
    dict can only pin *array* coordinates -- and neither the axis a phasor was taken over nor
    the harmonic it was taken at is one. Under a 1:1 spoke, computing the second harmonic
    would silently replace the first rather than sit beside it.
    """

    anchor = models.ForeignKey(CoordinateAnchor, related_name="phasor_histograms", on_delete=models.CASCADE)
    axis = models.CharField(max_length=32, help_text="The axis the phasor was taken over, e.g. 'tau'")
    harmonic = models.PositiveSmallIntegerField(default=1, help_text="The harmonic the phasor was taken at")
    bins = models.PositiveIntegerField(default=256, help_text="The resolution of the square (g, s) density grid")
    g_min = models.FloatField(default=0.0, help_text="The lower g bound of the density grid")
    g_max = models.FloatField(default=1.0, help_text="The upper g bound of the density grid")
    s_min = models.FloatField(default=0.0, help_text="The lower s bound of the density grid")
    s_max = models.FloatField(default=0.6, help_text="The upper s bound of the density grid")
    counts = models.JSONField(default=list, help_text="The flattened bins x bins density, row-major with s outermost")
    total = models.BigIntegerField(null=True, blank=True, help_text="The number of pixels that contributed, so counts can be normalized")
    calibrated = models.BooleanField(default=False, help_text="Whether the g/s were reference-corrected when computed. An uncalibrated density is still a valid distribution, it is just not traceable to an absolute lifetime")
    profile = models.JSONField(default=list, blank=True, help_text="The summed profile along the phasor axis (a decay for a MICROTIME axis, a spectrum for a SPECTRUM one), one value per bin. Calibration-free, so a client can sanity-check or recompute the transform")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["anchor", "axis", "harmonic"], name="unique_phasor_histogram")]


class PhasorCalibration(models.Model):
    """N:1 Spoke (Instrument Response Truth).

    The correction taking a raw phasor to a calibrated one. It lives on the dataset rather than
    on a render node because it is an *acquisition* fact: two layers over one dataset cannot
    coherently disagree about the instrument response. Being anchored makes it per detection
    channel, which is right -- the IRF differs per detector.

    Stored as a phase offset (radians) and a modulation factor (dimensionless), both
    dimension-free, so one model serves a lifetime reference and a spectral one alike. The
    reference *value* ("4.1 ns") is what someone used to derive those two numbers; it is
    recorded descriptively rather than as a quantity the server would have to interpret.

    Absent this spoke a phasor is simply uncalibrated. That is a legitimate state: the overlay
    still renders, its hue just is not traceable to an absolute lifetime.
    """

    anchor = models.ForeignKey(CoordinateAnchor, related_name="phasor_calibrations", on_delete=models.CASCADE)
    axis = models.CharField(max_length=32, help_text="The axis the correction applies to, e.g. 'tau'")
    harmonic = models.PositiveSmallIntegerField(default=1, help_text="The harmonic the correction applies at")
    phase_offset = models.FloatField(null=True, blank=True, help_text="The phase correction in radians, added to each pixel's phase")
    modulation_factor = models.FloatField(null=True, blank=True, help_text="The modulation correction, multiplied into each pixel's modulus")
    reference = models.CharField(max_length=255, null=True, blank=True, help_text="What the correction was measured against, e.g. 'Rhodamine 6G, 4.1 ns'")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["anchor", "axis", "harmonic"], name="unique_phasor_calibration")]


class Lens(models.Model):
    """A selection over a dataset. Nothing else.

    Its shape and dimensions are derived from the dataset and the slices -- they
    were columns, and two people computing them from the same slices are
    guaranteed to agree, so there was no reason for a second copy that could
    drift.

    A *sliced* lens has its own coordinate system, and the edge back to the dataset
    is a stored :class:`~core.models.Transformation`. Before that, slicing shifted
    voxel coordinates and nothing recorded the shift: an ROI drawn on a cropped
    lens had no defined path back to its dataset. An **unsliced** lens selects
    everything, so its space is the dataset's intrinsic space by definition -- it
    owns no system and no edge (see :attr:`space`), because a second node for the
    same space joined by an identity edge would record nothing. Lenses are
    immutable, so the decision is made once, at creation.

    The lens-to-parent edge is **derived from the slices, never authored** --
    recreating a lens from its slices reproduces its geometry exactly. In
    particular, per-channel corrections (chromatic drift) are not lens
    properties: they are acquisition facts, and a correction stored on a view
    would be a second copy free to disagree with the next view of the same
    channel. The supported interim pattern is one lens per channel plus a
    scene-level registration edge authored from the lens' system
    (``createTransformation`` accepts any input system, and the search in
    :mod:`core.logic.graph` takes the direct edge because it is both the
    best-known route and the shortest -- not because directness is itself
    preferred; there is no such rule). If channel-wise correction becomes a
    first-class need, it will be a dataset-owned ``aligned`` system with one
    channel-wise edge from intrinsic -- the physical-space pattern again, never
    per-view state.
    """

    dataset = models.ForeignKey(ArrayDataset, on_delete=models.CASCADE, related_name="lenses")
    slices = models.JSONField(help_text="The selection this lens makes over its dataset, as a list of per-dimension slices", default=list)

    # Always set. An unsliced lens used to own no system, with a null standing for "the
    # dataset's grid"; under residence it lives in that grid and points at the same node.
    coordinate_system = models.ForeignKey(
        CoordinateSystem,
        on_delete=models.PROTECT,
        # Nullable in the database only because the `historical*` twin carries rows written
        # before this column existed, and a history row must be allowed to say "not
        # recorded". Every write path sets it, so a live row never has none.
        null=True,
        blank=True,
        related_name="lenses",
        help_text="The coordinate system this lens' voxels are expressed in. An unsliced lens shares its dataset's; a sliced one has its own, with a stored edge carrying the shift",
    )

    provenance = ProvenanceField()

    @property
    def slices_list(self) -> list[base_models.SliceModel]:
        """Return the slices of the lens as a list."""
        return [base_models.SliceModel(**slice_dict) for slice_dict in self.slices] if isinstance(self.slices, list) else []

    @property
    def axis_names(self) -> list:
        """The lens' axis names. A selection never drops or reorders an axis."""
        return self.dataset.axis_names

    @property
    def axis_specs(self) -> list[coords_logic.AxisSpec]:
        """The lens' axes, coerced for :mod:`core.logic.coords`."""
        return self.dataset.axis_specs

    @property
    def shape_list(self) -> list:
        """The shape this lens' slices cut out of its dataset."""
        return coords_logic.lens_shape(self.dataset.shape_list, self.dataset.axis_names, self.slices_list)

    @property
    def space(self):
        """The coordinate system this lens' selection is expressed in.

        A lens with no slices selects everything, so its space is the dataset's
        intrinsic space *by definition* and it owns no system -- the same rule as a
        level-0 array. A sliced lens shifts voxel coordinates, which is a real fact,
        so it owns a system and the derived edge that records the shift.
        """
        return getattr(self, "coordinate_system", None) or self.dataset.intrinsic_coordinate_system

    @property
    def to_parent(self):
        """The stored edge from this lens' space back into its dataset's intrinsic space.

        None for an unsliced lens: its space IS the intrinsic space, and there is no shift to
        record.

        Both endpoints named, and top-level only, for the same reason as
        :attr:`DataArray.to_parent` -- an input-only filter returns any edge leaving the space,
        which for an unsliced lens (sharing the dataset's grid) is every registration the dataset
        has.
        """
        system = getattr(self, "coordinate_system", None)
        intrinsic = self.dataset.intrinsic_coordinate_system
        if system is None or intrinsic is None or system.pk == intrinsic.pk:
            return None
        return Transformation.objects.filter(input=system, output=intrinsic, parent__isnull=True).first()

    def get_size_of_axis(self, axis_name: str) -> int:
        """Get the size of an axis by its name."""
        axis_names, shape = self.axis_names, self.shape_list
        try:
            return shape[axis_names.index(axis_name)]
        except ValueError as error:
            raise ValueError(f"Axis {axis_name} not found in lens axes {axis_names}.") from error

    @property
    def active_anchors(self):
        """
        THE WORKSPACE QUERY:
        Finds all anchors that fall within the boundaries of this Lens.
        Respects the "Axis-Agnostic" rule: If an anchor is global ({})
        or partial ({"c": 0}), it is included as long as it doesn't contradict the Lens.
        """
        qs = CoordinateAnchor.objects.filter(dataset=self.dataset)

        # Loop through the axis constraints of the Lens
        for slc in self.slices_list:
            axis = slc.axis

            # Condition A: The anchor is global for this axis (key doesn't exist)
            axis_is_global = ~Q(coordinates__has_key=axis)

            if slc.start is not None and slc.stop is not None:
                axis_in_range = Q(**{f"coordinates__{axis}__gte": slc.start, f"coordinates__{axis}__lt": slc.stop})
            else:
                continue  # Failsafe for unhandled slice types

            # The anchor must either be global for this axis, OR fall inside the slice limits
            qs = qs.filter(axis_is_global | axis_in_range)

        # OPTIMIZATION: prefetch/select the spokes to prevent N+1 database death
        return qs.select_related("microscope").prefetch_related("ome_metadata", "value_histogram")



class Scene(models.Model):
    """A composition of layers over a shared world coordinate system.

    ``world`` is *which* space the scene composes over, and it is always set. It is
    deliberately not ownership: a scene never owns a space. A scene created bare gets
    an ordinary ownerless SHARED system created for convenience; a scene created over
    an existing system merely references it -- either way many scenes can compose over
    one space, the space outlives every one of them (``on_delete=RESTRICT`` refuses to
    delete a space out from under a scene), and no scene's deletion deletes a space:
    that is ``deleteCoordinateSystem``'s explicit job.

    The scene carries no units: they are per-axis, on the axes of its world
    system. It carries no affine either -- the map to a parent scene is an edge
    between the two scenes' world systems, like every other spatial fact.

    There is deliberately no membership set (RFC-6). Which registrations this
    composition uses is not a scene-level pool: a layer carries no placement
    reference at all, and its path to world is fixed by the graph alone -- see
    ``Layer``, which says the same thing from the other side. Two scenes over one
    world therefore *agree* about a dataset's position by construction, which is
    the point of the shared space. A scene is its world plus its layers, nothing
    more.

    This paragraph used to describe a ``Layer.registration`` column, which RFC-8
    removed along with ``affine_matrix`` and ``validity`` -- and which contradicted
    ``Layer``'s own docstring two hundred lines below. Where two scenes genuinely
    need to disagree, that is rival edges plus the widest-path search in
    ``core.logic.graph._bfs_tree``, not a per-view pointer.
    """

    name = models.CharField(max_length=255)
    world = models.ForeignKey(
        "CoordinateSystem",
        on_delete=models.RESTRICT,
        related_name="scenes",
        help_text=(
            "The space this scene composes its layers over: a shared world system created for "
            "convenience alongside the scene, or an adopted existing system -- a shared space, a "
            "dataset's intrinsic grid, a physical space, a collection's space. Never owned by the "
            "scene: many scenes can share it, it outlives each of them, and deleting a scene "
            "never deletes it. RESTRICT: while a scene is rooted in a space, neither the space "
            "nor its owning container can be deleted"
        ),
    )
    blending = TextChoicesField(
        choices_enum=enums.BlendingChoices,
        default=enums.BlendingChoices.ADDITIVE.value,
        help_text="The blending of the scene",
    )
    # Viewer preferences: how a client should *look* at this composition. Preferences, not
    # constraints -- nothing server-side reads them, and a viewer that ignores them is not
    # wrong. They sit here rather than in a layer's render graph because that graph says
    # what the pixels are; these say where the eye goes, which is a fact about the whole
    # composition and about no layer in particular.
    preferred_view = TextChoicesField(
        choices_enum=enums.PreferredViewChoices,
        default=enums.PreferredViewChoices.AUTO.value,
        help_text="How a viewer should open this scene: flat, volumetric, or its own choice. Defaults to AUTO -- a scene nobody has expressed a preference for should not claim one",
    )
    background_color = models.JSONField(
        null=True,
        blank=True,
        help_text="The viewer background, as RGBA. Null to let the viewer use its own",
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)

    provenance = ProvenanceField()


class Animation(models.Model):
    """A named camera tour of a scene: the poses a viewer pans through, in order.

    A view artifact, not a spatial fact. It hangs off the scene and cascades with it, and
    no placement walk crosses it: refining a registration moves the data, and it must
    never move the camera. That is the same footing :class:`SceneSnapshot` stands on, and
    deliberately not :class:`~core.models.Annotation`'s -- an annotation is owned by
    its collection and outlives every scene, because it is a claim about where
    something *is*.

    Its waypoints are written as a whole list, never one at a time, which is what makes
    ``AnimationWaypoint.order`` trustworthy -- see the mutation.
    """

    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="animations", help_text="The scene this tour flies through")
    name = models.CharField(max_length=255, help_text="The name of the tour, e.g. 'overview' or 'dive to the mitochondria'")
    description = models.CharField(max_length=1000, null=True, blank=True, help_text="What the tour shows")

    created_at = models.DateTimeField(auto_now_add=True, help_text="The time the tour was created")
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, help_text="The user that created the tour")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization the tour belongs to")
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this object was created through, if any",
    )
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_%(class)ss",
        help_text="The assigner of the creating task, denormalized for fast filtering",
    )
    provenance = ProvenanceField()


class AnimationWaypoint(models.Model):
    """One camera pose in a tour, and how the viewer travels to it.

    Carries no creator, organization or provenance of its own: a waypoint is written by
    its animation's mutation and cascades with it, exactly as an :class:`Axis` is written
    by its coordinate system's. The animation is what a delete guard and org-scoping act
    on; a waypoint is never independently owned.
    """

    animation = models.ForeignKey(Animation, on_delete=models.CASCADE, related_name="waypoints", help_text="The tour this pose belongs to")
    order = models.PositiveSmallIntegerField(help_text="The pose's index in the tour. Written by enumeration from the authored list, never supplied by a caller")
    name = models.CharField(max_length=255, blank=True, default="", help_text="What this stop shows, e.g. 'the nucleus'")
    camera = models.JSONField(help_text="Where the camera is, as a CameraState: a position keyed by the world's axis names, plus the flat and volumetric views of it")
    duration_ms = models.PositiveIntegerField(default=1000, help_text="How long the viewer takes to travel TO this pose, in milliseconds. Ignored for the first pose, which is where the tour starts")
    easing = TextChoicesField(
        choices_enum=enums.EasingChoices,
        default=enums.EasingChoices.EASE_IN_OUT.value,
        help_text="How the viewer eases the camera along that travel",
    )

    class Meta:
        """Meta options for the animation waypoint."""

        ordering = ["order"]
        constraints = [
            # Safe precisely because `order` is written by enumeration over the whole
            # authored list: two waypoints are never swapped in place, so this can never
            # be transiently violated and needs no deferral.
            models.UniqueConstraint(fields=["animation", "order"], name="unique_animation_waypoint_order"),
        ]

    def __str__(self) -> str:
        """The waypoint's position in its tour."""
        return f"{self.animation_id}[{self.order}]"


class SceneSnapshot(models.Model):
    """A pre-rendered picture of a composition: every layer of the scene, blended.

    A picture of the *scene*, and deliberately not of any one dataset in it -- there is
    no lens or dataset FK, because what a single dataset looks like on its own is not a
    question this model answers. It cascades with the scene: once the composition is
    gone, a picture of it depicts nothing that still exists.

    Not a spatial fact, and deliberately not on the coordinate graph: a snapshot
    records that a picture was taken, never a claim about where anything is. Nothing
    walks it, and refining a registration does not move it.

    A dataset can still be previewed from these, through the scene it nominates as its
    ``default_scene`` -- which is what ``ArrayDataset.latestSnapshot`` reads. That is a choice
    someone made, not a fact derived from the graph, so the picture may well show other data
    staged in the same scene.
    """

    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="snapshots", help_text="The composition this is a picture of")
    store = models.ForeignKey(MediaStore, on_delete=models.CASCADE, related_name="scene_snapshots", help_text="The media store holding the rendered image")
    name = models.CharField(max_length=1000, default="", help_text="The name of the snapshot")

    created_at = models.DateTimeField(auto_now_add=True, help_text="The time the snapshot was taken")
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, help_text="The user that took the snapshot")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization the snapshot belongs to")
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this object was created through, if any",
    )
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_%(class)ss",
        help_text="The assigner of the creating task, denormalized for fast filtering",
    )
    provenance = ProvenanceField()
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_scene_snapshots",
        blank=True,
        help_text="The users that have pinned the snapshot",
    )

    class Meta:
        indexes = [
            models.Index(fields=["scene", "-created_at"], name="scene_snapshot_latest_idx"),
        ]


class Layer(models.Model):
    """View state, and the unit that gets alpha-blended. No spatial fields.

    A single table discriminated by ``kind``: it carries the shared placement and
    compositing settings plus the source and render settings for every layer kind.
    Exactly one source FK is set per kind, enforced by the create mutations -- though
    a *kind* is not one-to-one with a source: ``POINT`` and ``TRACK`` share
    ``table_dataset``, and ``IMAGE``, ``INTENSITY``, ``RGB``, ``PHASOR`` and ``LABEL``
    all share ``lens``. What separates them is the second half of
    :class:`~core.enums.LayerKind`'s job: which render settings apply. In GraphQL this
    one model is exposed as a ``Layer`` interface with concrete types resolved by
    ``kind``.

    **Only ``IMAGE`` carries a render graph, and that is the point of the split.** A
    tree is what a layer that genuinely composites needs; the four kinds beside it have
    a fixed shape, so they carry their settings as columns. The graph form of an
    intensity layer was a blend node with a single child, and additively blending one
    thing is that thing -- a level of nesting that said nothing and that every reader
    had to walk anyway. RGB is the sharper case: as a graph, a photograph and three
    fluorescence markers coloured red/green/blue are the *same* three-child additive
    blend, and the second reading is much the commoner one, so the authored fact was
    destroyed at write time and unrecoverable on read. It is a kind now, and states
    itself.

    The columns those kinds carry are deliberately **exactly what their builder
    mutations take as input** -- no more. ``createIntensityLayer`` accepts no transfer
    curve, no inverted mapping and no per-channel opacity, so ``IntensityLayer`` has no
    field for them and they stay what they have always been: a reason to use ``IMAGE``.
    That rule is what keeps "which kind am I" answerable without judgment, and it is why
    this split loses nothing.

    A solid tint was on that list once and is not any more -- see ``color`` below. The
    line it moved across is *compositing*, not "anything the graph can also say": a
    second channel, an authored curve and a per-channel opacity all need a tree because
    they describe how two things combine, where a tint describes one ramp's hue and is
    the same shape ``colormap`` already is. The rule survives the move intact, because
    the question it answers is still "does this layer composite".

    ``clim_min``, ``clim_max`` and ``gamma`` were columns here once and were dropped
    when ``render_graph`` arrived. Their return is not a reversal of that decision: the
    graph is still the only honest description of a layer that *composites*. What
    changed is that it stopped being applied to layers that never did.

    **The rule this model exists to obey (RFC-8):** a spatial fact is a node or an
    edge, never a column here, and a layer's spatial questions are answered by
    *deriving over its path* -- stored nowhere. Check any proposed field against it:
    if two layers over one dataset could carry two copies of it and disagree, it
    belongs on the edge.

    Every removal on this model is that rule applied. The layer no longer carries an
    ``affine_matrix``: registration belongs to the dataset, not to a view of it, and
    it is a :class:`~core.models.Transformation` edge into the scene's world -- under
    RFC-6 unique per (data, world), so the layer carries no placement reference at
    all and its path to world is fixed by the graph alone. It no longer carries
    ``validity`` or ``status`` (RFC-8): how well a placement is known is
    a fact of the *edge*, and the layer derives the weakest one on its path. Nor does
    it carry ``x_dim`` and friends -- those follow from the axis types, and are
    derived by :func:`core.logic.coords.resolve_render_axes`.

    The same rule decides what *stays*. Which columns hold a point layer's
    coordinates is not here either: the table dataset declares them by role, and a
    per-layer copy could disagree with the dataset's own schema. ``size_column`` and
    friends pick among the remaining measure columns for display, which is honestly
    per-layer view state.

    Two scalar lengths below (``point_size``, ``line_width``) are in *scene units*,
    which RFC-8 defines as the world's spatial-axis unit -- meaningful for a layer
    exactly when its path to world preserves lengths up to one factor
    (``placementInvariance`` of SIMILARITY or better).
    """

    # --- shared placement / compositing ---
    scene = models.ForeignKey(Scene, related_name="layers", on_delete=models.CASCADE)
    kind = TextChoicesField(
        choices_enum=enums.LayerKindChoices,
        default=enums.LayerKindChoices.IMAGE.value,
        help_text="The kind of layer, discriminating its data source and render settings",
    )
    blending = TextChoicesField(
        choices_enum=enums.BlendingChoices,
        default=enums.BlendingChoices.ADDITIVE.value,
        help_text="The blending mode used to composite this layer over the layers below it",
    )
    opacity = models.FloatField(default=1.0, help_text="Layer alpha for alpha-over compositing (0..1)")
    visible = models.BooleanField(default=True, help_text="Whether the layer participates in compositing")
    order = models.IntegerField(default=0, help_text="Explicit z-index for deterministic back-to-front compositing")
    # What a layer is *called*, which until now it had no way to be. The channel name a
    # bootstrapped scene knows ("DAPI", from a `ChannelLabel` spoke) was written into the
    # render graph's root node `label`, because that was the only string on the row -- a
    # workaround `core.logic.scene` said so at the time. The flat kinds have no graph root,
    # so the workaround had nowhere left to stand, and the honest field is this one. On the
    # interface rather than per kind: a heterogeneous `Scene.layers` list wants one name
    # field, not a fragment per kind to read the same string.
    name = models.CharField(max_length=255, null=True, blank=True, help_text="A human-readable name for the layer, e.g. the channel it draws ('DAPI'). Null when nobody has named it")

    # --- source references (exactly one set, per kind) ---
    lens = models.ForeignKey(Lens, on_delete=models.CASCADE, related_name="layers", null=True, blank=True, help_text="(image/intensity/rgb/phasor/label/vector) The lens that defines the array data source and constraints")
    annotation_collection = models.ForeignKey(
        "AnnotationCollection",
        on_delete=models.CASCADE,
        related_name="layers",
        null=True,
        blank=True,
        help_text="(annotation) The annotation collection, owning its own coordinate system, whose drawn shapes this layer renders",
    )
    table_dataset = models.ForeignKey(
        "TableDataset",
        on_delete=models.CASCADE,
        related_name="layers",
        null=True,
        blank=True,
        help_text="(point/track) The table dataset whose declared coordinate columns provide the coordinates; its own coordinate system is the space, and its column roles are the mapping -- nothing is duplicated per layer",
    )
    mesh_collection = models.ForeignKey(
        "MeshCollection",
        on_delete=models.CASCADE,
        related_name="mesh_layers",
        null=True,
        blank=True,
        help_text="(mesh) The versioned mesh collection, owning its own coordinate system, that this layer renders",
    )
    network_collection = models.ForeignKey(
        "NetworkCollection",
        on_delete=models.CASCADE,
        related_name="network_layers",
        null=True,
        blank=True,
        help_text="(network) The versioned network collection, owning its own coordinate system, that this layer renders",
    )
    # --- image render settings ---
    render_graph = models.JSONField(null=True, blank=True, default=None, help_text="(image) The composable render recipe (channels + transfer functions + in-layer blend) that is the single source of truth for how the image layer is rendered.")

    # --- intensity / rgb render settings ---
    # Columns, not a graph, because these kinds have a fixed shape -- see the class docstring.
    # `intensity_axis` and the contrast limits are shared between INTENSITY and RGB the way
    # `lens` is shared across every array-sourced kind: one fact, one column, whoever asks it.
    intensity_axis = models.CharField(max_length=100, null=True, blank=True, help_text="(intensity/rgb) The lens axis carrying the channels, or null when the pixel value itself is the intensity (a single-valued volume)")
    intensity_index = models.IntegerField(null=True, blank=True, help_text="(intensity) The index along the intensity axis to render")
    clim_min = models.FloatField(null=True, blank=True, help_text="(intensity/rgb/vector) Lower contrast limit, in the data's own units -- not a normalized fraction. For intensity and RGB that means intensity units (RGB shares one pair across all three channels, because they are components of one picture rather than three signals); for a vector layer it means vector MAGNITUDE, in the component axis's unit -- the two readings share this column, so both are stated here rather than left to collide")
    clim_max = models.FloatField(null=True, blank=True, help_text="(intensity/rgb/vector) Upper contrast limit, in the data's own units -- not a normalized fraction. For a vector layer: vector magnitude, in the component axis's unit")
    gamma = models.FloatField(null=True, blank=True, help_text="(intensity) Gamma correction applied to the normalized intensities")
    # A solid tint, and the one member of the transfer function's vocabulary that crossed
    # over. It is here because it is not a curve: `stops` and `invert` reshape the mapping
    # from intensity to colour and need a tree to say so, where a tint only says which hue
    # the one ramp ends at -- the same shape `colormap` already is, spelled as a colour
    # rather than chosen from a list. A converter that knows a channel's emission
    # wavelength, or reads the acquisition software's own colour for it, has an RGBA quad
    # and no named map that means it; before this column that fact alone forced the whole
    # layer onto a render graph.
    #
    # `default=None` for `colormap`'s reason below, and it overrides rather than excludes:
    # both may be set, and the colour wins. That is `TransferFunction`'s rule, and the two
    # disagreeing about what a tint beside a map means would be worse than either answer.
    color = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text="(intensity/vector) A solid RGBA color instead of a colormap: four components, each 0..255. For intensity, a tint over the channel; for a vector layer, a flat glyph colour where a magnitude ramp is noise",
    )
    # Null means draw the plane. A projection is a setting on one channel rather than a kind
    # of its own: it collapses z, it does not composite anything, so a VOLUME layer is an
    # INTENSITY layer with this set. `BootstrapLayerKind.VOLUME` is the policy that sets it.
    projection_mode = TextChoicesField(
        choices_enum=enums.ProjectionModeChoices,
        null=True,
        blank=True,
        default=None,
        help_text="(intensity) How z is collapsed. Null means draw the plane; set means project over z",
    )
    red_index = models.IntegerField(null=True, blank=True, help_text="(rgb) The index along the intensity axis carrying the red component")
    green_index = models.IntegerField(null=True, blank=True, help_text="(rgb) The index along the intensity axis carrying the green component")
    blue_index = models.IntegerField(null=True, blank=True, help_text="(rgb) The index along the intensity axis carrying the blue component")

    # --- phasor render settings ---
    # JSON rather than columns, for the reason `label_render` is: a phasor's transfer maps
    # the *reduction's* output -- a (g, s) pair plus a photon count -- and carries a list of
    # cursors, so it is a nested document rather than a handful of scalars. The same models
    # `core.render.layer.models` already declares for the phasor render *node*, which is what
    # composites a phasor inside an IMAGE layer's graph.
    phasor_render = models.JSONField(null=True, blank=True, default=None, help_text="(phasor) Which axis is reduced to a phasor, at which harmonic, and how the resulting (g, s) becomes color. The single source of truth for how the phasor layer is rendered.")

    # --- label render settings ---
    # A second column rather than a second schema inside `render_graph`: that column's
    # invariant is "the root is a blend node", and a label map has no compositing tree to
    # put under one. Keeping them apart is what makes "a label source additively blended
    # with a fluorescence channel" unrepresentable rather than merely unbuilt.
    label_render = models.JSONField(null=True, blank=True, default=None, help_text="(label) How discrete object ids become color: the hashing seed, the transparent background id, contour-or-fill, the selection, and an optional `colorBy` naming a column of the table this mask's FIELD edge keys into. The single source of truth for how the label layer is rendered.")
    # Shared by INTENSITY with point and track, the way `lens` and `table_dataset` are shared:
    # one column for one fact, whichever kind is asking. `default=None` and not VIRIDIS,
    # because the kinds disagree about what a sensible default *is* -- a fluorescence channel
    # opens grey, a point cloud coloured by a measure opens viridis -- and a column default
    # can only be one of them. Every writer names its own: `createIntensityLayer` grey,
    # `createPointLayer` and `_materialize_table_layer` viridis. A column default here would
    # be a fourth answer, silently right for two kinds and wrong for the third.
    colormap = TextChoicesField(choices_enum=enums.ColorMapChoices, default=None, help_text="(intensity/point/track/vector) The applying color map. A vector layer applies it over glyph magnitude", null=True, blank=True)

    # --- point/track render choices. Which columns provide the COORDINATES (and the
    # track/point identity) is never stored here: the table dataset declares them by
    # role, and a second per-layer copy could disagree with the dataset's own schema.
    # These pick among the remaining measure columns for display, which is honestly
    # per-layer view state.
    size_column = models.CharField(max_length=100, null=True, blank=True, help_text="(point) The table column mapped to per-point size")
    color_column = models.CharField(max_length=100, null=True, blank=True, help_text="(point) The table column mapped to per-point color/intensity (used with colormap)")
    point_size = models.FloatField(null=True, blank=True, help_text="(point) The default point size, in scene units -- the world's spatial-axis unit, which is a well-defined length for a layer only when its `placementInvariance` is SIMILARITY or better (RFC-8)")
    color_by_column = models.CharField(max_length=100, null=True, blank=True, help_text="(track) The table column used to color tracks (used with colormap)")
    line_width = models.FloatField(null=True, blank=True, help_text="(track) The width of the track lines, in scene units -- the world's spatial-axis unit, which is a well-defined length for a layer only when its `placementInvariance` is SIMILARITY or better (RFC-8)")

    # --- mesh render settings ---
    material_color = models.JSONField(default=None, null=True, blank=True, help_text="(mesh) The material (surface) color of the mesh (RGBA)")
    wireframe = models.BooleanField(default=False, help_text="(mesh) Whether the mesh is rendered as a wireframe instead of a solid surface")
    shading = TextChoicesField(
        choices_enum=enums.MeshShadingChoices,
        default=enums.MeshShadingChoices.SMOOTH.value,
        help_text="(mesh) How the surface is lit. Vocabulary a mesh needs and an image has no use for -- a raster has no normals to shade with",
    )
    # A cap, never a choice of level: which level a viewer loads follows from the zoom, and
    # this only says how far it may go. Its own column rather than a field on the collection
    # because it is a per-layer budget -- two layers over one collection may honestly want
    # different ones -- and RFC-8 licenses that: it says what is drawn, never where the
    # geometry is.
    max_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="(mesh) The deepest octree level this layer may load, capping detail against the collection's declared `grid.levels`. Null lets the viewer decide",
    )

    # --- network render settings ---
    #
    # Two width sources rather than one, because the two kinds of data this serves measure
    # different things. A traced arbor carries a radius **per node** -- the calibre of the
    # dendrite, a measurement the tracer made -- and the segment between two nodes tapers
    # between their radii. A connectome carries a weight **per edge** and every segment is
    # uniform. Both are optional and fall back to the flat `line_width` the track kind
    # already has; `node_size_column` wins when both are given, since a per-node profile is
    # strictly the more specific statement.
    node_size_column = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="(network) The per-node column giving each node's radius, so a segment tapers between its endpoints. Read from the collection's attributes, in scene units",
    )
    edge_width_column = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="(network) The per-edge column giving each segment a uniform width. Ignored when `nodeSizeColumn` is set, which is the more specific statement",
    )
    # Direction is always in the data -- an edge is stored source-to-target -- so this says
    # only whether to *draw* it. A traced arbor is directed (soma outward) and usually drawn
    # without arrowheads anyway; a connectome is directed and usually drawn with them.
    directed = models.BooleanField(
        default=False,
        help_text="(network) Whether to draw the edge direction, e.g. as arrowheads. The data is always ordered source-to-target; this is a render setting, not a fact about the graph",
    )
    show_nodes = models.BooleanField(
        default=False,
        help_text="(network) Whether to draw a glyph at each node as well as the segments between them. Useful for a sparse graph, noise on a dense arbor",
    )

    # The same shape `label_render` carries under `colorBy`, and for the same reason: a
    # collection's objects carry ids, a FIELD edge keys them to a table, and picking one of
    # its columns to color by is per-layer view state (RFC-8). Its own column rather than a
    # `mesh_render` blob because the settings above are flat already, and folding them in
    # would be a migration that moves data to say nothing new.
    #
    # **A list.** A set of objects is routinely worth reading several ways at once -- volume as
    # a continuous colormap, cell type through a qualitative one -- and which one a viewer sees now
    # is a decision the person at the screen makes, not the author. So the author publishes an
    # ordered *picker* and the viewer chooses within it. Every entry is validated exactly as the
    # single colouring this replaced was: reachable by a FIELD edge, column declared, colouring
    # matched to the column's role.
    #
    # A label layer's `label_render` carries the same two pickers under the same names, built by
    # the same functions (`core.mutations.layer.build_color_bys` / `build_filter_bys`). They sit
    # in a JSON blob there rather than in columns here only because a label layer already had
    # one -- not because the two say anything different.
    mesh_color_bys = models.JSONField(default=list, blank=True, help_text="(mesh) The colourings this layer offers, in the order a picker should show them. Each colours objects by a column of a table this collection's FIELD edge keys into. Empty means the flat material color is the only rendering")
    # An index into that list rather than a copy of the chosen entry, or a flag on it: a
    # duplicate is free to disagree with the entry it duplicates, and a per-entry `active`
    # boolean makes "two active at once" representable when only one can be drawn.
    active_color_by = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="(mesh) Which entry of `meshColorBys` is currently drawn, as an index into it. Null renders the flat material color -- what having no colouring has always meant",
    )

    # The colour picker's sibling, over the same FIELD edge and checked by the same code: where
    # a colouring says what an object looks like, a filter says whether it is drawn at all.
    #
    # Two entries may name one column here, and deliberately -- "small cells" and "large cells"
    # are two rules over one measure, which is exactly what a picker is for. The colour picker
    # refuses a repeat because two entries with the same column, colormap and window are
    # one colouring wearing two names; there is no such thing as two identical *rules* that
    # differ in what they select.
    mesh_filter_bys = models.JSONField(default=list, blank=True, help_text="(mesh) The filters this layer offers, in the order a picker should show them. Each keeps or drops objects by a column of a table this collection's FIELD edge keys into. Empty means nothing is offered and every object draws")
    # A list of indices, not one: filters *compose*, and several being on at once is the normal
    # case rather than a contradiction -- which is the whole reason this is a list where
    # `active_color_by` is a single index. Applied conjunctively; an object draws when every
    # active rule keeps it.
    active_filter_bys = models.JSONField(default=list, blank=True, help_text="(mesh) Which entries of `meshFilterBys` are currently applied, as indices into it. Combined with AND -- an object is drawn when every active rule keeps it. Empty applies none of them, so everything draws")

    # The same two pickers again, for a point layer.
    #
    # Separate columns from the mesh pair rather than shared ones, for the reason the comment
    # above gives about `label_render`: one row is one layer of one kind, and a column named for
    # the kind that uses it cannot be read as the other's by accident. `active_color_by` and
    # `active_filter_bys` ARE shared, because an index into "this layer's picker" means the same
    # thing whichever picker it indexes.
    #
    # A point layer needs these for the reason a mesh layer does and more sharply: its objects
    # have positions and nothing else, so a colouring is the only thing that makes one point
    # differ from another. The flat `color_column` it shipped with predates the picker and cannot
    # name a sparse matrix at all -- which is most of what there is to colour a point cloud by.
    point_color_bys = models.JSONField(default=list, blank=True, help_text="(point) The colourings this layer offers, in the order a picker should show them. Each colours points by a column of a table this layer's ids key into, or by one slice of a sparse matrix they index. Empty means every point takes the flat colour")
    point_filter_bys = models.JSONField(default=list, blank=True, help_text="(point) The filters this layer offers, in the order a picker should show them. Each keeps or drops points by a column of a table this layer's ids key into. Empty means nothing is offered and every point draws")

    # The same two pickers a fourth time, for a network layer -- columns named for the kind,
    # `active_color_by`/`active_filter_bys` shared, exactly per the rule stated above the point
    # pair. What is new here is not the shape but a third entry kind: alongside COLUMN (a table
    # keyed by this collection's *object* ids) and SPARSE, a GRAPH entry reads a per-node value
    # the collection itself carries -- Strahler order, degree, depth, component, a stored
    # radius, a writer's own column -- validated against the collection's manifest rather than
    # against a table, and aimed at nodes or edges by its `target`.
    network_color_bys = models.JSONField(default=list, blank=True, help_text="(network) The colourings this layer offers, in the order a picker should show them. Each colours objects by a table column or sparse slice their ids reach, or nodes by a per-node attribute the collection itself carries. Empty means the flat material color is the only rendering")
    network_filter_bys = models.JSONField(default=list, blank=True, help_text="(network) The filters this layer offers, in the order a picker should show them. Each keeps or drops objects by a reachable table column or sparse slice, or nodes and segments by a per-node attribute the collection carries. Empty means nothing is offered and everything draws")

    # --- vector render settings ---
    #
    # The columns pass the RFC-8 test above: two layers over one flow field may honestly
    # disagree about all three -- a coarse overview beside a dense inset is the normal case.
    # Which axis holds the components is NOT here, deliberately: it is a fact of the array
    # (the DISPLACEMENT axis), derived by `resolve_render_axes` like `x_dim` and friends,
    # and a per-layer copy could disagree with the axes themselves. Named `glyph_*` rather
    # than `vector_*` because a future table-backed glyph kind shares this vocabulary the
    # way `colormap` is shared across intensity, point and track.
    glyph = TextChoicesField(
        choices_enum=enums.VectorGlyphChoices,
        default=enums.VectorGlyphChoices.ARROW.value,
        help_text="(vector) How one sampled vector is drawn: an arrow, a bare line, or a cone. Always triangle geometry, never a sprite -- a sprite is not an occluder in a volume pass",
    )
    glyph_stride = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="(vector) Sample every Nth voxel per spatial axis. Null lets the renderer pick a stride from its own glyph budget -- a dense field at full resolution is millions of glyphs, which is a picture of overdraw rather than of flow",
    )
    glyph_scale = models.FloatField(
        null=True,
        blank=True,
        help_text="(vector) How a magnitude becomes a drawn length: scene units per unit of the component axis. Null auto-normalizes to the sampled maximum. In scene units, so like `point_size` it is well defined for a layer only when its `placementInvariance` is SIMILARITY or better (RFC-8)",
    )

    provenance = ProvenanceField()
