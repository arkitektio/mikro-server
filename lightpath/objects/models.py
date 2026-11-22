from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Discriminator, Field

from kanne_server import quantities
from lightpath.enums import (
    ChannelKind,
    ElementKind,
    FilterKind,
    ObjectiveCorrectionKind,
    ObjectiveImmersion,
    PortRole,
    PulseKind,
)


# ---- Geometry / beam annotations ----
class Vec3Model(BaseModel):
    x: float | None = None
    y: float | None = None
    z: float | None = None


class EulerModel(BaseModel):
    rx: float | None = None
    ry: float | None = None
    rz: float | None = None


class Pose3DModel(BaseModel):
    position: Vec3Model | None = None
    orientation: EulerModel | None = None


class SpectrumModel(BaseModel):
    min: quantities.Length
    max: quantities.Length


class BeamStateModel(BaseModel):
    wavelength: quantities.Length | None = None
    power: quantities.Power | None = None
    polarization: str | None = None
    mode_hint: str | None = None  # e.g. TEM00


# ---- Ports ----
class LightPortModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4))
    name: str
    role: PortRole
    channel: ChannelKind = ChannelKind.FREE_SPACE
    spectrum: SpectrumModel | None = None
    max_incoming_edges: int | None = None  # allow merges


# ---- Optical element base + subtypes (inline fields, no nested params) ----
class OpticalElementBaseModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=lambda: str(uuid4))
    label: str
    kind: ElementKind
    pose: Pose3DModel | None = None
    ports: list[LightPortModel] = Field(default_factory=list)
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None


class LampElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.LAMP] = ElementKind.LAMP
    channel: ChannelKind | None = ChannelKind.FREE_SPACE
    lamp_type: str | None = None  # e.g., LED, Halogen, Xenon, Mercury, etc.


class OtherSourceElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.OTHER_SOURCE] = ElementKind.OTHER_SOURCE
    channel: ChannelKind | None = ChannelKind.FREE_SPACE
    lamp_type: str | None = None  # e.g., LED, Halogen, Xenon, Mercury, etc.


class LaserElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.LASER] = ElementKind.LASER
    nominal_wavelength: quantities.Length
    power: quantities.Power | None = None
    channel: ChannelKind | None = ChannelKind.FREE_SPACE
    laser_medium: str | None = None
    pulse_kind: PulseKind | None = None
    repetition_rate: quantities.Frequency | None = None
    has_pockels_cell: bool | None = None
    has_q_switch: bool | None = None


class DetectorElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.DETECTOR] = ElementKind.DETECTOR
    nepd_w_per_sqrt_hz: float | None = None
    # Both were declared on `DetectorElement` (the read type) and on the ingest input, and
    # on neither the storage model nor the union -- so a client could send them, they were
    # stored in the raw dump, and the read side rebuilt an element without them.
    amplifier_gain_db: float | None = None
    gain: float | None = None


class PinholeElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.PINHOLE] = ElementKind.PINHOLE
    diameter: quantities.Length | None = None


class MirrorElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.MIRROR] = ElementKind.MIRROR
    angle_deg: float | None = None
    band: SpectrumModel | None = None


class BeamSplitterElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.BEAM_SPLITTER] = ElementKind.BEAM_SPLITTER
    r_fraction: float = 0.5
    t_fraction: float = 0.5
    band: SpectrumModel | None = None


class LensElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.LENS] = ElementKind.LENS
    focal_length: quantities.Length | None


class CCDElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.CCD] = ElementKind.CCD
    pixel_size: quantities.Length | None = None
    resolution: tuple[int, int] | None = None  # (width, height)


class SampleElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.SAMPLE] = ElementKind.SAMPLE
    description: str | None = None


class FilterElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.FILTER] = ElementKind.FILTER
    description: str | None = None
    filter_kind: FilterKind | None = None
    transmittance: float | None = None  # 0-1


class OtherElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.OTHER] = ElementKind.OTHER
    description: str | None = None


class ObjectiveElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.OBJECTIVE] = ElementKind.OBJECTIVE
    magnification: float | None = None
    numerical_aperture: float | None = None
    working_distance: quantities.Length | None = None
    immersion_medium: ObjectiveImmersion | None = None
    correction_kind: ObjectiveCorrectionKind | None = None
    # As with the detector's gain: declared on the read type and the input, stored nowhere.
    iris: bool | None = None
    brand: str | None = None


class ShutterElementModel(OpticalElementBaseModel):
    """A blocker: open or closed, and how fast it switches.

    An AOTF or an EOM used purely as a gate is one of these -- which is what the payload
    that exposed this whole gap was: `ElementKind.SHUTTER` was in the enum, so the SDL
    advertised it and a client sent it, and there was no model to build it with.
    """

    kind: Literal[ElementKind.SHUTTER] = ElementKind.SHUTTER
    is_open: bool | None = None
    shutter_type: str | None = None  # e.g., mechanical, AOTF, EOM
    gain: float | None = None


class PolarizerElementModel(OpticalElementBaseModel):
    """A polarization filter, at an angle."""

    kind: Literal[ElementKind.POLARIZER] = ElementKind.POLARIZER
    angle_deg: float | None = None
    extinction_ratio: float | None = None


class WaveplateElementModel(OpticalElementBaseModel):
    """A retarder: a fraction of a wave, at an angle."""

    kind: Literal[ElementKind.WAVEPLATE] = ElementKind.WAVEPLATE
    angle_deg: float | None = None
    retardance: float | None = None  # in waves, e.g. 0.5 for a half-wave plate
    design_wavelength: quantities.Length | None = None


class ApertureElementModel(OpticalElementBaseModel):
    """A stop: a hole of some diameter. A pinhole with no confocal claim attached."""

    kind: Literal[ElementKind.APERTURE] = ElementKind.APERTURE
    diameter: quantities.Length | None = None


#: Every element kind, and the model that stores it. **This table is the union**, rather
#: than a list written beside one: a fourth parallel list of element kinds is exactly how
#: `SHUTTER` came to be advertised in the SDL with nothing able to build it, failing at
#: *read* time on data ingest had already accepted. `test_lightpath_elements` asserts the
#: table covers `ElementKind` exhaustively, so adding an enum member without a model fails
#: the suite rather than a client's query.
ELEMENT_MODEL_BY_KIND: dict[ElementKind, type[OpticalElementBaseModel]] = {
    ElementKind.OTHER_SOURCE: OtherSourceElementModel,
    ElementKind.LASER: LaserElementModel,
    ElementKind.DETECTOR: DetectorElementModel,
    ElementKind.LAMP: LampElementModel,
    ElementKind.MIRROR: MirrorElementModel,
    ElementKind.PINHOLE: PinholeElementModel,
    ElementKind.BEAM_SPLITTER: BeamSplitterElementModel,
    ElementKind.LENS: LensElementModel,
    ElementKind.SAMPLE: SampleElementModel,
    ElementKind.OTHER: OtherElementModel,
    ElementKind.OBJECTIVE: ObjectiveElementModel,
    ElementKind.FILTER: FilterElementModel,
    ElementKind.CCD: CCDElementModel,
    ElementKind.SHUTTER: ShutterElementModel,
    ElementKind.POLARIZER: PolarizerElementModel,
    ElementKind.WAVEPLATE: WaveplateElementModel,
    ElementKind.APERTURE: ApertureElementModel,
}

OpticalElementUnion = Annotated[
    Union[tuple(ELEMENT_MODEL_BY_KIND.values())],  # type: ignore[valid-type]  # noqa: UP007 -- a union of a computed tuple has no X | Y spelling
    Discriminator("kind"),
]


# ---- Graph edges + graph ----
class LightEdgeModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4))
    source_element_id: str = Field(default_factory=lambda: str(uuid4))
    source_port_id: str = Field(default_factory=lambda: str(uuid4))
    target_element_id: str = Field(default_factory=lambda: str(uuid4))
    target_port_id: str = Field(default_factory=lambda: str(uuid4))
    path_length: quantities.Length | None = None
    medium: str | None = "AIR"
    loss_db: float | None = 0.0
    beam: BeamStateModel | None = None


class LightpathGraphModel(BaseModel):
    elements: list[OpticalElementUnion] = Field(default_factory=list)
    edges: list[LightEdgeModel] = Field(default_factory=list)
