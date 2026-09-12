from __future__ import annotations
from typing import Annotated, Optional, List, Union, Literal, Tuple
from pydantic import BaseModel, Field, ConfigDict, Discriminator
from uuid import uuid4
from kanne_server import quantities
from lightpath.enums import PulseKind, ChannelKind, PortRole, ElementKind, FilterKind, ObjectiveImmersion, ObjectiveCorrectionKind


# ---- Geometry / beam annotations ----
class Vec3Model(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


class EulerModel(BaseModel):
    rx: Optional[float] = None
    ry: Optional[float] = None
    rz: Optional[float] = None


class Pose3DModel(BaseModel):
    position: Optional[Vec3Model] = None
    orientation: Optional[EulerModel] = None


class SpectrumModel(BaseModel):
    min: quantities.Length
    max: quantities.Length


class BeamStateModel(BaseModel):
    wavelength: Optional[quantities.Length] = None
    power: Optional[quantities.Power] = None
    polarization: Optional[str] = None
    mode_hint: Optional[str] = None  # e.g. TEM00


# ---- Ports ----
class LightPortModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4))
    name: str
    role: PortRole
    channel: ChannelKind = ChannelKind.FREE_SPACE
    spectrum: Optional[SpectrumModel] = None
    max_incoming_edges: Optional[int] = None  # allow merges

    @property
    def is_input(self) -> bool:
        return self.role == PortRole.INPUT

    @property
    def is_output(self) -> bool:
        return self.role == PortRole.OUTPUT


# ---- Optical element base + subtypes (inline fields, no nested params) ----
class OpticalElementBaseModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=lambda: str(uuid4))
    label: str
    kind: ElementKind
    pose: Optional[Pose3DModel] = None
    ports: List[LightPortModel] = Field(default_factory=list)
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None


class LampElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.LAMP] = ElementKind.LAMP
    channel: ChannelKind | None = ChannelKind.FREE_SPACE
    lamp_type: Optional[str] = None  # e.g., LED, Halogen, Xenon, Mercury, etc.


class OtherSourceElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.OTHER_SOURCE] = ElementKind.OTHER_SOURCE
    channel: ChannelKind | None = ChannelKind.FREE_SPACE
    lamp_type: Optional[str] = None  # e.g., LED, Halogen, Xenon, Mercury, etc.


class LaserElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.LASER] = ElementKind.LASER
    nominal_wavelength: quantities.Length
    power: Optional[quantities.Power] = None
    channel: ChannelKind | None = ChannelKind.FREE_SPACE
    laser_medium: Optional[str] = None
    pulse_kind: Optional[PulseKind] = None
    repetition_rate: Optional[quantities.Frequency] = None
    has_pockels_cell: Optional[bool] = None
    has_q_switch: Optional[bool] = None


class DetectorElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.DETECTOR] = ElementKind.DETECTOR
    nepd_w_per_sqrt_hz: Optional[float] = None
    # Both were declared on `DetectorElement` (the read type) and on the ingest input, and
    # on neither the storage model nor the union -- so a client could send them, they were
    # stored in the raw dump, and the read side rebuilt an element without them.
    amplifier_gain_db: Optional[float] = None
    gain: Optional[float] = None


class PinholeElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.PINHOLE] = ElementKind.PINHOLE
    diameter: Optional[quantities.Length] = None


class MirrorElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.MIRROR] = ElementKind.MIRROR
    angle_deg: Optional[float] = None
    band: Optional[SpectrumModel] = None


class BeamSplitterElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.BEAM_SPLITTER] = ElementKind.BEAM_SPLITTER
    r_fraction: float = 0.5
    t_fraction: float = 0.5
    band: Optional[SpectrumModel] = None


class LensElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.LENS] = ElementKind.LENS
    focal_length: quantities.Length | None


class CCDElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.CCD] = ElementKind.CCD
    pixel_size: Optional[quantities.Length] = None
    resolution: Optional[Tuple[int, int]] = None  # (width, height)


class SampleElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.SAMPLE] = ElementKind.SAMPLE
    description: Optional[str] = None


class FilterElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.FILTER] = ElementKind.FILTER
    description: Optional[str] = None
    filter_kind: Optional[FilterKind] = None
    transmittance: Optional[float] = None  # 0-1


class OtherElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.OTHER] = ElementKind.OTHER
    description: Optional[str] = None


class ObjectiveElementModel(OpticalElementBaseModel):
    kind: Literal[ElementKind.OBJECTIVE] = ElementKind.OBJECTIVE
    magnification: float | None = None
    numerical_aperture: float | None = None
    working_distance: Optional[quantities.Length] = None
    immersion_medium: Optional[ObjectiveImmersion] = None
    correction_kind: Optional[ObjectiveCorrectionKind] = None
    # As with the detector's gain: declared on the read type and the input, stored nowhere.
    iris: Optional[bool] = None
    brand: Optional[str] = None


class ShutterElementModel(OpticalElementBaseModel):
    """A blocker: open or closed, and how fast it switches.

    An AOTF or an EOM used purely as a gate is one of these -- which is what the payload
    that exposed this whole gap was: `ElementKind.SHUTTER` was in the enum, so the SDL
    advertised it and a client sent it, and there was no model to build it with.
    """

    kind: Literal[ElementKind.SHUTTER] = ElementKind.SHUTTER
    is_open: Optional[bool] = None
    shutter_type: Optional[str] = None  # e.g., mechanical, AOTF, EOM
    gain: Optional[float] = None


class PolarizerElementModel(OpticalElementBaseModel):
    """A polarization filter, at an angle."""

    kind: Literal[ElementKind.POLARIZER] = ElementKind.POLARIZER
    angle_deg: Optional[float] = None
    extinction_ratio: Optional[float] = None


class WaveplateElementModel(OpticalElementBaseModel):
    """A retarder: a fraction of a wave, at an angle."""

    kind: Literal[ElementKind.WAVEPLATE] = ElementKind.WAVEPLATE
    angle_deg: Optional[float] = None
    retardance: Optional[float] = None  # in waves, e.g. 0.5 for a half-wave plate
    design_wavelength: Optional[quantities.Length] = None


class ApertureElementModel(OpticalElementBaseModel):
    """A stop: a hole of some diameter. A pinhole with no confocal claim attached."""

    kind: Literal[ElementKind.APERTURE] = ElementKind.APERTURE
    diameter: Optional[quantities.Length] = None


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
    Union[tuple(ELEMENT_MODEL_BY_KIND.values())],  # type: ignore[valid-type]
    Discriminator("kind"),
]


# ---- Graph edges + graph ----
class LightEdgeModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4))
    source_element_id: str = Field(default_factory=lambda: str(uuid4))
    source_port_id: str = Field(default_factory=lambda: str(uuid4))
    target_element_id: str = Field(default_factory=lambda: str(uuid4))
    target_port_id: str = Field(default_factory=lambda: str(uuid4))
    path_length: Optional[quantities.Length] = None
    medium: Optional[str] = "AIR"
    loss_db: Optional[float] = 0.0
    beam: Optional[BeamStateModel] = None


class LightpathGraphModel(BaseModel):
    elements: List[OpticalElementUnion] = Field(default_factory=list)
    edges: List[LightEdgeModel] = Field(default_factory=list)
