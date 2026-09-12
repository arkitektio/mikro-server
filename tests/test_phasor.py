"""Tests for the phasor: the pure derivation logic, the metadata spokes, and the read surface.

The load-bearing test here is :func:`test_a_single_query_is_sufficient_to_compute_a_phasor`.
Every other test checks that a field *resolves*; that one checks the exposed set is
*sufficient* -- that each term of

    g = sum_k I_k cos(n w t_k) / sum_k I_k
    s = sum_k I_k sin(n w t_k) / sum_k I_k

and then of the color, is reachable from one query. A field that resolves but leaves the
client unable to compute a phasor would pass every other test in this file and still fail
the only requirement that matters.
"""

import pytest

from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import coords as coords_logic
from core.logic import phasor as phasor_logic
from core.types import array_dataset as array_types
from lightpath.objects import models as lightpath_models
from mikro_server.schema import schema
from tests import seed


# A FLIM cube: two detection channels, 16 arrival-time bins, 32x32 pixels.
_FLIM_AXES = [
    seed.axis("c", enums.AxisType.CHANNEL),
    seed.axis("tau", enums.AxisType.MICROTIME),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]
_FLIM_SHAPE = [2, 16, 32, 32]

# The same axes, calibrated: tau in nanoseconds (a [time] unit, as a MICROTIME axis
# requires), the spatial axes in micrometres.
_FLIM_CALIBRATED_AXES = [
    seed.physical_axis("c", enums.AxisType.CHANNEL, "dimensionless"),
    seed.physical_axis("tau", enums.AxisType.MICROTIME, "nanosecond"),
    seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
    seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
]
# One tau bin is 0.78125 ns wide, so the 16 bins span 12.5 ns -- the period of an 80 MHz laser.
_TAU_BIN_WIDTH = 0.78125
_FLIM_SCALE = [1.0, _TAU_BIN_WIDTH, 0.325, 0.325]


# ---------------------------------------------------------------------------
# The pure logic: no database
# ---------------------------------------------------------------------------


def test_phasor_axis_finds_the_continuous_axis():
    axes = [coords_logic.AxisSpec(name=name, type=type_) for name, type_ in (("c", "CHANNEL"), ("tau", "MICROTIME"), ("y", "SPACE"), ("x", "SPACE"))]
    assert phasor_logic.phasor_axis(axes).name == "tau"


def test_phasor_axis_is_none_without_one():
    axes = [coords_logic.AxisSpec(name=name, type=type_) for name, type_ in (("c", "CHANNEL"), ("y", "SPACE"), ("x", "SPACE"))]
    assert phasor_logic.phasor_axis(axes) is None


def test_axis_scale_reads_the_diagonal_of_a_scale_edge():
    edges = [("SCALE", {"scale": _FLIM_SCALE})]
    assert phasor_logic.axis_scale(edges, 1, 4) == _TAU_BIN_WIDTH


def test_axis_scale_of_a_sequence_uses_its_children():
    """A SEQUENCE keeps its parameters on its children, not in its own params.

    Composing it from its own (empty) params yields the identity -- a bin width of exactly
    1.0, which reads as an uncalibrated axis rather than as a bug. This is the case that
    silently lies, so it is the one worth pinning.
    """
    children = [("SCALE", {"scale": _FLIM_SCALE}), ("TRANSLATION", {"translation": [0.0, 0.0, 10.0, 10.0]})]

    unflattened = phasor_logic.axis_scale([("SEQUENCE", {})], 1, 4)
    assert unflattened is None or unflattened == 1.0  # what the naive read gives

    flattened = phasor_logic.flatten_edge("SEQUENCE", {}, children)
    assert phasor_logic.axis_scale(flattened, 1, 4) == _TAU_BIN_WIDTH


def test_axis_scale_is_none_for_a_non_affine_edge():
    assert phasor_logic.axis_scale([("FIELD", {})], 1, 4) is None


def test_laser_frequency_reads_the_dual_struct():
    graph = {"elements": [{"kind": "DETECTOR"}, {"kind": "LASER", "repetition_rate": {"canonical": 80_000_000_000_000_000, "given": "80 MHz", "unit": "nanohertz"}}]}
    assert phasor_logic.laser_frequency(graph) == 80_000_000_000_000_000


def test_laser_frequency_is_none_without_a_pulsed_source():
    assert phasor_logic.laser_frequency({"elements": [{"kind": "DETECTOR"}]}) is None
    assert phasor_logic.laser_frequency({}) is None


# ---------------------------------------------------------------------------
# Seeding a fully-described FLIM dataset
# ---------------------------------------------------------------------------


def _laser_graph() -> dict:
    """A lightpath whose source is a mode-locked 80 MHz laser: the clock a FLIM phasor runs on."""
    laser = lightpath_models.LaserElementModel(
        label="Ti:Sapphire",
        nominal_wavelength="800 nm",
        pulse_kind="ModeLocked",
        repetition_rate="80 MHz",
    )
    return lightpath_models.LightpathGraphModel(elements=[laser], edges=[]).model_dump(mode="json")


def _attach_lightpath(dataset: models.ArrayDataset) -> None:
    anchor = models.CoordinateAnchor.objects.create(dataset=dataset, coordinates={"c": 0})
    models.LightPath.objects.create(anchor=anchor, graph=_laser_graph())


async def _seed_flim(ctx: HttpContext, *, calibrated: bool = True, lightpath: bool = True) -> models.Lens:
    dataset = await seed.create_array_dataset(ctx, "FLIM", axes=_FLIM_AXES, shapes=[_FLIM_SHAPE])
    if calibrated:
        await seed.create_physical_space(ctx, dataset, axes=_FLIM_CALIBRATED_AXES, scale=_FLIM_SCALE)
    if lightpath:
        await sync_to_async(_attach_lightpath)(dataset)
    return await seed.create_lens(ctx, dataset)


# ---------------------------------------------------------------------------
# The read surface
# ---------------------------------------------------------------------------


_PHASOR_CONTEXT = """
    query Lens($id: ID!) {
        lens(id: $id) {
            phasor {
                axis
                axisType
                bins
                harmonic
                binWidth
                window
                laserFrequency
                calibration { phaseOffset modulationFactor reference }
                phasorHistogram { bins counts total calibrated }
            }
        }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_context_derives_the_instrument(db, authenticated_context: HttpContext):
    """Bin width from the calibration, laser rate from the lightpath. Neither is copied onto a node."""
    lens = await _seed_flim(authenticated_context)

    result = await schema.execute(_PHASOR_CONTEXT, context_value=authenticated_context, variable_values={"id": str(lens.id)})
    assert not result.errors, result.errors

    context = result.data["lens"]["phasor"]
    assert context["axis"] == "tau"
    assert context["axisType"] == "MICROTIME"
    assert context["bins"] == 16
    assert context["harmonic"] == 1

    # 0.78125 ns per bin, 16 bins -> a 12.5 ns window, the period of the 80 MHz laser above.
    # The two agree because they describe the same instrument; nothing forces them to.
    assert "0.78125 nanosecond" in context["binWidth"]
    assert "12.5 nanosecond" in context["window"]
    assert context["laserFrequency"] is not None

    # No calibration and no distribution have been attached, and that is a legitimate state:
    # the overlay still renders, its hue is just not traceable to an absolute lifetime.
    assert context["calibration"] is None
    assert context["phasorHistogram"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_context_without_a_calibration(db, authenticated_context: HttpContext):
    """An uncalibrated dataset still has a phasor context -- it just has no physical bin width."""
    lens = await _seed_flim(authenticated_context, calibrated=False, lightpath=False)

    result = await schema.execute(_PHASOR_CONTEXT, context_value=authenticated_context, variable_values={"id": str(lens.id)})
    assert not result.errors, result.errors

    context = result.data["lens"]["phasor"]
    assert context["bins"] == 16
    assert context["binWidth"] is None
    assert context["window"] is None
    assert context["laserFrequency"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_context_is_null_without_a_phasor_axis(db, authenticated_context: HttpContext):
    """A plain c/y/x stack has no axis a phasor is defined over, and says so rather than guessing."""
    dataset = await seed.create_array_dataset(authenticated_context, "Plain")
    lens = await seed.create_lens(authenticated_context, dataset)

    result = await schema.execute(_PHASOR_CONTEXT, context_value=authenticated_context, variable_values={"id": str(lens.id)})
    assert not result.errors, result.errors
    assert result.data["lens"]["phasor"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_context_of_a_sliced_lens_reports_the_lens_bins(db, authenticated_context: HttpContext):
    """A lens that crops tau narrows the window the transform runs over, and the context says so
    rather than claiming a period the data does not cover."""
    dataset = await seed.create_array_dataset(authenticated_context, "FLIM", axes=_FLIM_AXES, shapes=[_FLIM_SHAPE])
    await seed.create_physical_space(authenticated_context, dataset, axes=_FLIM_CALIBRATED_AXES, scale=_FLIM_SCALE)
    lens = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "tau", "start": 0, "stop": 8}])

    result = await schema.execute(_PHASOR_CONTEXT, context_value=authenticated_context, variable_values={"id": str(lens.id)})
    assert not result.errors, result.errors

    context = result.data["lens"]["phasor"]
    assert context["bins"] == 8
    assert "0.78125 nanosecond" in context["binWidth"]  # the bin width is unchanged: it is the dataset's
    assert "6.25 nanosecond" in context["window"]  # but the window is halved, because the data is


# ---------------------------------------------------------------------------
# The metadata spokes
# ---------------------------------------------------------------------------


_CREATE_HISTOGRAM = """
    mutation Create($input: CreatePhasorHistogramInput!) {
        createPhasorHistogram(input: $input) { id axis harmonic bins total calibrated }
    }
"""


def _counts(bins: int) -> list[float]:
    return [0.0] * (bins * bins)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_phasor_histogram_is_keyed_by_harmonic(db, authenticated_context: HttpContext):
    """Two harmonics coexist; recomputing one replaces it.

    The anchor's coordinates can only pin *array* coordinates, and the harmonic is not one --
    which is why these spokes are ForeignKeys where every other spoke is a OneToOne. Under a
    1:1 spoke, computing the second harmonic would silently replace the first.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "FLIM", axes=_FLIM_AXES, shapes=[_FLIM_SHAPE])

    async def create(harmonic: int, total: int):
        return await schema.execute(
            _CREATE_HISTOGRAM,
            context_value=authenticated_context,
            variable_values={"input": {"dataset": str(dataset.id), "axis": "tau", "harmonic": harmonic, "bins": 4, "counts": _counts(4), "total": total}},
        )

    first = await create(1, 100)
    assert not first.errors, first.errors
    second = await create(2, 200)
    assert not second.errors, second.errors

    assert await models.PhasorHistogram.objects.filter(anchor__dataset=dataset).acount() == 2

    # Recomputing at an existing harmonic overwrites rather than duplicating.
    again = await create(1, 999)
    assert not again.errors, again.errors
    assert await models.PhasorHistogram.objects.filter(anchor__dataset=dataset).acount() == 2

    reloaded = await models.PhasorHistogram.objects.aget(anchor__dataset=dataset, axis="tau", harmonic=1)
    assert reloaded.total == 999


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_histogram_rejects_a_non_phasor_axis(db, authenticated_context: HttpContext):
    """A density stored against the z axis is not a phasor, and nothing downstream could tell."""
    dataset = await seed.create_array_dataset(authenticated_context, "FLIM", axes=_FLIM_AXES, shapes=[_FLIM_SHAPE])
    result = await schema.execute(
        _CREATE_HISTOGRAM,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": str(dataset.id), "axis": "x", "bins": 4, "counts": _counts(4)}},
    )
    assert result.errors, "expected a phasor histogram over a spatial axis to be rejected"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_histogram_rejects_a_missized_grid(db, authenticated_context: HttpContext):
    """counts is the *flattened* bins x bins grid; a client that sends a 1D histogram gets told."""
    dataset = await seed.create_array_dataset(authenticated_context, "FLIM", axes=_FLIM_AXES, shapes=[_FLIM_SHAPE])
    result = await schema.execute(
        _CREATE_HISTOGRAM,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": str(dataset.id), "axis": "tau", "bins": 4, "counts": [0.0] * 4}},
    )
    assert result.errors, "expected counts of the wrong length to be rejected"


# ---------------------------------------------------------------------------
# The sufficiency gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_single_query_is_sufficient_to_compute_a_phasor(db, authenticated_context: HttpContext):
    """One query yields every term of the transform, and of the color it becomes.

    Not "the fields resolve" -- that the *set* is sufficient. A client holding this response
    and the array can compute (g, s) for every pixel and color it, with no second round trip:

        I_k        the profile      <- node.phasorAxis + intensityAxis/intensityIndex
        N          the bin count    <- context.bins
        t_k        the bin centres  <- context.binWidth
        w          the frequency    <- context.window / context.laserFrequency
        n          the harmonic     <- node.harmonic
        correction the calibration  <- context.calibration
        hue        the color        <- node.transfer
        brightness the photon count <- node.transfer.intensity
        a range    without the cube <- context.phasorHistogram
        the scene  the overlay      <- layer.blending / opacity / order
    """
    lens = await _seed_flim(authenticated_context)
    scene = await seed.create_scene(authenticated_context)
    await seed.register_into_scene(authenticated_context, scene, lens.dataset)
    dataset_id = await sync_to_async(lambda: str(lens.dataset.id))()

    histogram = await schema.execute(
        _CREATE_HISTOGRAM,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": dataset_id, "axis": "tau", "bins": 4, "counts": _counts(4), "total": 1024}},
    )
    assert not histogram.errors, histogram.errors

    calibration = await schema.execute(
        "mutation Create($input: CreatePhasorCalibrationInput!) { createPhasorCalibration(input: $input) { id phaseOffset } }",
        context_value=authenticated_context,
        variable_values={"input": {"dataset": dataset_id, "axis": "tau", "phaseOffset": 0.21, "modulationFactor": 0.94, "reference": "Rhodamine 6G, 4.1 ns"}},
    )
    assert not calibration.errors, calibration.errors

    created = await schema.execute(
        "mutation Create($input: CreatePhasorLayerInput!) { createPhasorLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "intensityAxis": "c",
                "transfer": {
                    "mode": "PHASE",
                    "min": "0.5 ns",
                    "max": "4 ns",
                    "colormap": "RAINBOW",
                    "cursors": [{"kind": "CIRCLE", "g": 0.4, "s": 0.35, "radius": 0.05, "color": [255, 0, 0, 255]}],
                    # The second place a `TransferFunctionInput` is reachable from: the photon
                    # count gets an ordinary transfer, curve and all, even though the phasor
                    # itself does not have one.
                    "intensity": {"stops": [{"position": 10.0, "value": 0.0}, {"position": 900.0, "value": 1.0}]},
                },
            }
        },
    )
    assert not created.errors, created.errors
    layer_id = created.data["createPhasorLayer"]["id"]

    # The one query. Everything below comes from this single response.
    query = """
        query Layer($id: ID!) {
            layer(id: $id) {
                __typename
                blending
                opacity
                order
                ... on PhasorLayer {
                    phasorRender {
                        phasorAxis
                        intensityAxis
                        intensityIndex
                        harmonic
                        transfer {
                            mode min max colormap weightByIntensity
                            intensity { climMin climMax gamma stops { position value } }
                            cursors { kind g s radius color }
                        }
                    }
                    lens {
                        phasor {
                            axis axisType bins harmonic binWidth window laserFrequency
                            calibration { phaseOffset modulationFactor reference }
                            phasorHistogram { bins counts total calibrated }
                        }
                    }
                }
            }
        }
    """
    result = await schema.execute(query, context_value=authenticated_context, variable_values={"id": str(layer_id)})
    assert not result.errors, result.errors

    layer = result.data["layer"]
    node = layer["phasorRender"]
    context = layer["lens"]["phasor"]

    # I_k -- which profile to transform, and which detection channel it comes from.
    assert node["phasorAxis"] == "tau"
    assert node["intensityAxis"] == "c"
    assert node["intensityIndex"] == 0

    # N, t_k, w -- the bin count, the bin width, and the period the transform runs over.
    assert context["bins"] == 16
    assert context["binWidth"] is not None
    assert context["window"] is not None
    assert context["laserFrequency"] is not None

    # n -- the harmonic, and it is the *same* harmonic the context resolved its spokes at.
    assert node["harmonic"] == 1
    assert context["harmonic"] == node["harmonic"]

    # The instrument-response correction taking the raw phasor to a calibrated one.
    assert context["calibration"]["phaseOffset"] == 0.21
    assert context["calibration"]["modulationFactor"] == 0.94

    # (g, s) -> hue, photon count -> brightness, and the phasor-space color rules.
    assert node["transfer"]["mode"] == "PHASE"
    assert node["transfer"]["colormap"] == "RAINBOW"
    assert "nanosecond" in node["transfer"]["min"]
    assert node["transfer"]["weightByIntensity"] is True
    assert node["transfer"]["intensity"] is not None
    assert node["transfer"]["intensity"]["stops"] == [{"position": 10.0, "value": 0.0}, {"position": 900.0, "value": 1.0}]
    assert node["transfer"]["cursors"][0]["color"] == [255, 0, 0, 255]

    # A sane default range, without reading the cube.
    assert context["phasorHistogram"]["total"] == 1024
    assert len(context["phasorHistogram"]["counts"]) == 16

    # And where the overlay sits in the x/y/z scene: a layer like any other, alpha-composited.
    # Its own kind, because a phasor's transfer maps a (g, s) pair plus a photon count rather
    # than a sampled scalar -- a phasor composited *with* an ordinary channel is the case that
    # still needs a render graph, and that is an IMAGE layer carrying a PhasorNode.
    assert layer["__typename"] == "PhasorLayer"
    assert layer["blending"] == "NORMAL"
    assert layer["opacity"] == 1.0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ingest_may_carry_the_phasor_spokes(db, authenticated_context: HttpContext):
    """A converter that already computed a phasor can attach it at ingest, on the anchor.

    The standalone mutations are the usual path -- computing a density means reading the cube --
    but the spokes are also fields of CoordinateAnchorInput, and that branch of create_array_dataset
    is otherwise driven by nothing.
    """
    from unittest.mock import patch

    from datalayer.models import ZarrStore

    store = await ZarrStore.objects.acreate(
        organization=authenticated_context.request.organization,
        key="flim-zarr",
        bucket="zarr",
        shape=_FLIM_SHAPE,
        chunks=_FLIM_SHAPE,
        version="3",
        dtype="uint8",
        populated=True,
    )

    mutation = """
        mutation Create($input: CreateArrayDatasetInput!) {
            createArrayDataset(input: $input) { id }
        }
    """
    variables = {
        "input": {
            "data": str(store.id),
            "name": "FLIM",
            "scales": [],
            "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "tau", "type": "MICROTIME"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
            "anchors": [
                {
                    "axisAnchors": [{"axis": "c", "value": 0}],
                    "phasorHistogram": {"axis": "tau", "bins": 4, "counts": _counts(4), "total": 512, "calibrated": True},
                    "phasorCalibration": {"axis": "tau", "phaseOffset": 0.3, "modulationFactor": 0.9},
                }
            ],
        }
    }
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors

    dataset_id = result.data["createArrayDataset"]["id"]
    histogram = await models.PhasorHistogram.objects.aget(anchor__dataset_id=dataset_id)
    assert histogram.axis == "tau"
    assert histogram.total == 512
    assert histogram.calibrated is True

    calibration = await models.PhasorCalibration.objects.aget(anchor__dataset_id=dataset_id)
    assert calibration.phase_offset == 0.3

    # Both spokes hang off the *same* anchor -- they describe one detection channel, not two.
    assert histogram.anchor_id == calibration.anchor_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_bin_width_is_not_read_off_whatever_axis_sits_at_the_same_position(authenticated_context: HttpContext):
    """The physical space is asked for *this* axis by name, not for its axis at some index.

    `_resolve_bin_width` used to take one integer -- the phasor axis' position in the lens'
    axis list -- and use it twice: to index the composed matrix's diagonal, and as `Axis.order`
    on the *physical* system. Nothing checked the two systems' axis at that position was the
    same axis. Here the physical space calls its microtime axis `m` and the intrinsic calls it
    `tau`, so position 1 holds a different axis on each side: the old code returned a real
    number in that other axis' unit and `window` (`binWidth x bins`) inherited it.

    `assert_unit_matches_type` cannot catch this -- a SPECTRUM and a SPACE axis both demand
    `[length]` -- so a wrong unit here is indistinguishable from a right one downstream.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "FLIM renamed", axes=_FLIM_AXES, shapes=[_FLIM_SHAPE])

    def attach_mismatched_physical() -> None:
        creation = seed._creation(ctx)
        intrinsic = dataset.coordinate_system
        physical = models.CoordinateSystem.objects.create(name="physical (renamed)", creator=creation.user, organization=creation.organization)
        for order, (name, kind, unit) in enumerate(
            [("c", enums.AxisTypeChoices.CHANNEL, "dimensionless"), ("m", enums.AxisTypeChoices.MICROTIME, "nanosecond"),
             ("y", enums.AxisTypeChoices.SPACE, "micrometer"), ("x", enums.AxisTypeChoices.SPACE, "micrometer")]
        ):
            models.Axis.objects.create(coordinate_system=physical, order=order, name=name, type=kind.value, unit=unit)
        # A SEQUENCE with a SCALE child -- the shape `create_physical_space` writes, and the
        # only one `axis_scale` can read (`compose` refuses a BY_DIMENSION outright, so that
        # shape would make this test pass for the wrong reason). Written through the ORM
        # rather than the mutation because the neighbouring fix now refuses a per-axis edge
        # between differently-named systems at the boundary; this is the row as it would
        # already exist in a database.
        wrapper = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SEQUENCE.value,
            input=intrinsic, output=physical, params={},
            creator=creation.user, organization=creation.organization,
        )
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value, parent=wrapper, order=0,
            params={"scale": _FLIM_SCALE}, creator=creation.user, organization=creation.organization,
        )

    await sync_to_async(attach_mismatched_physical)()
    lens = await seed.create_lens(ctx, dataset)

    def context():
        return array_types.resolve_phasor_context(models.Lens.objects.get(pk=lens.pk), axis_name=None, harmonic=1)

    resolved = await sync_to_async(context)()
    assert resolved is not None

    def neighbours():
        from core.logic import graph as g
        return [n.name for n in g.physical_neighbours(models.ArrayDataset.objects.get(pk=dataset.pk).coordinate_system)]
    found = await sync_to_async(neighbours)()
    assert found, f"the physical space must be reachable or this test proves nothing; got {found}"

    assert resolved.bin_width is None, (
        f"the physical space has no axis named 'tau', so it says nothing about its bin width -- "
        f"got {resolved.bin_width!r}, which is the unit of whatever sits at the same index"
    )
