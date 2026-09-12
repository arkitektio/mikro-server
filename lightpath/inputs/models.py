from __future__ import annotations
from typing import Annotated, Optional, List, Literal
from pydantic import BaseModel, ConfigDict, Field

from kanne_server import quantities
from lightpath.enums import ChannelKind, FilterKind, PortRole, ElementKind, ObjectiveImmersion, PulseKind
from lightpath.objects import models as object_models


class Vec3InputModel(BaseModel):
    """3D vector for input."""

    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


class EulerInputModel(BaseModel):
    """Euler angles for input."""

    rx: Optional[float] = None
    ry: Optional[float] = None
    rz: Optional[float] = None


class Pose3DInputModel(BaseModel):
    """Pose with optional position and orientation."""

    position: Optional[Vec3InputModel] = None
    orientation: Optional[EulerInputModel] = None


class SpectrumInputModel(BaseModel):
    """Spectral window."""

    min: quantities.Length
    max: quantities.Length


class BeamStateInputModel(BaseModel):
    """Beam properties for input edges."""

    wavelength: Optional[quantities.Length] = None
    power: Optional[quantities.Power] = None
    polarization: Optional[str] = None
    mode_hint: Optional[str] = None


class LightPortInputModel(BaseModel):
    """Port definition for input."""

    id: Optional[str] = None  # optional: may be generated server-side
    name: str
    role: PortRole
    channel: ChannelKind = ChannelKind.FREE_SPACE
    spectrum: Optional[SpectrumInputModel] = None
    max_incoming_edges: Optional[int] = None


# --------------------------------------------------------------------------------------
# The optical element input union.
#
# An element arrives as the flat, discriminator-carrying `OpticalElementInput`: `kind` plus
# the union of every kind's fields. The per-kind member models below are the strict truth
# about which fields each kind reads -- they forbid the rest -- and their input mirrors are
# published under `@unionElementOf` so a generated client can rebuild the tagged union.
#
# This shape replaces one fat model that read `kind` and validated nothing against it. That
# model was not merely lax, it was *disconnected*: ingest dumped it straight to JSON without
# ever building the storage union, so an element the storage side could not express was
# accepted, written, and only failed when something later read the column back. Each member
# below carries `to_element()`, and ingest calls it -- so the check happens where the author
# is, and the stored JSON is by construction a shape the read side can rebuild.


class OpticalElementInputBase(BaseModel):
    """The fields every element carries, whatever its kind."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = None
    label: str
    kind: ElementKind
    pose: Optional[Pose3DInputModel] = None
    ports: List[LightPortInputModel] = Field(default_factory=list)
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None

    def _base_kwargs(self) -> dict:
        """The base fields, in the shape the storage models take."""
        data = {
            "label": self.label,
            "kind": self.kind,
            "pose": self.pose.model_dump() if self.pose else None,
            "ports": [port.model_dump() for port in self.ports],
            "manufacturer": self.manufacturer,
            "model": self.model,
            "serial_number": self.serial_number,
        }
        if self.id is not None:
            data["id"] = self.id
        return data

    def to_element(self) -> object:
        """Build the storage model for this element. Overridden by every member."""
        raise NotImplementedError


def _band(band_min, band_max):  # noqa: ANN001, ANN202 - two optional quantities in, one optional dict out
    """The stored `band` spectrum, from the two flat wire fields that describe it.

    The wire says `bandMin`/`bandMax` and the storage model says one `band` -- the one place
    a member is not a field-for-field mirror, kept because both spellings are already in use.
    """
    if band_min is None and band_max is None:
        return None
    return {"min": band_min, "max": band_max}


class LaserElementInputModel(OpticalElementInputBase):
    """A laser: a wavelength, a power, and how it pulses."""

    kind: Literal[ElementKind.LASER] = ElementKind.LASER
    nominal_wavelength: quantities.Length
    power: Optional[quantities.Power] = None
    channel: Optional[ChannelKind] = None
    laser_medium: Optional[str] = None
    pulse_kind: Optional[PulseKind] = None
    repetition_rate: Optional[quantities.Frequency] = None
    has_pockels_cell: Optional[bool] = None
    has_q_switch: Optional[bool] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.LaserElementModel(
            **self._base_kwargs(),
            nominal_wavelength=self.nominal_wavelength,
            power=self.power,
            channel=self.channel or ChannelKind.FREE_SPACE,
            laser_medium=self.laser_medium,
            pulse_kind=self.pulse_kind,
            repetition_rate=self.repetition_rate,
            has_pockels_cell=self.has_pockels_cell,
            has_q_switch=self.has_q_switch,
        )


class LampElementInputModel(OpticalElementInputBase):
    """A lamp: an incoherent source."""

    kind: Literal[ElementKind.LAMP] = ElementKind.LAMP
    channel: Optional[ChannelKind] = None
    lamp_type: Optional[str] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.LampElementModel(**self._base_kwargs(), channel=self.channel or ChannelKind.FREE_SPACE, lamp_type=self.lamp_type)


class OtherSourceElementInputModel(OpticalElementInputBase):
    """A source that is neither a laser nor a lamp."""

    kind: Literal[ElementKind.OTHER_SOURCE] = ElementKind.OTHER_SOURCE
    channel: Optional[ChannelKind] = None
    lamp_type: Optional[str] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.OtherSourceElementModel(**self._base_kwargs(), channel=self.channel or ChannelKind.FREE_SPACE, lamp_type=self.lamp_type)


class DetectorElementInputModel(OpticalElementInputBase):
    """A point detector: a PMT, an APD."""

    kind: Literal[ElementKind.DETECTOR] = ElementKind.DETECTOR
    nepd_w_per_sqrt_hz: Optional[float] = None
    amplifier_gain_db: Optional[float] = None
    gain: Optional[float] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.DetectorElementModel(
            **self._base_kwargs(),
            nepd_w_per_sqrt_hz=self.nepd_w_per_sqrt_hz,
            amplifier_gain_db=self.amplifier_gain_db,
            gain=self.gain,
        )


class CCDElementInputModel(OpticalElementInputBase):
    """An array detector: a CCD, a CMOS."""

    kind: Literal[ElementKind.CCD] = ElementKind.CCD
    pixel_size: Optional[quantities.Length] = None
    resolution: Optional[List[int]] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        resolution = tuple(self.resolution) if self.resolution else None
        return object_models.CCDElementModel(**self._base_kwargs(), pixel_size=self.pixel_size, resolution=resolution)


class MirrorElementInputModel(OpticalElementInputBase):
    """A mirror, at an angle, over a coating band."""

    kind: Literal[ElementKind.MIRROR] = ElementKind.MIRROR
    angle_deg: Optional[float] = None
    band_min: Optional[quantities.Length] = None
    band_max: Optional[quantities.Length] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.MirrorElementModel(**self._base_kwargs(), angle_deg=self.angle_deg, band=_band(self.band_min, self.band_max))


class BeamSplitterElementInputModel(OpticalElementInputBase):
    """A splitter: what it reflects, what it transmits."""

    kind: Literal[ElementKind.BEAM_SPLITTER] = ElementKind.BEAM_SPLITTER
    r_fraction: Optional[float] = None
    t_fraction: Optional[float] = None
    band_min: Optional[quantities.Length] = None
    band_max: Optional[quantities.Length] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.BeamSplitterElementModel(
            **self._base_kwargs(),
            r_fraction=self.r_fraction if self.r_fraction is not None else 0.5,
            t_fraction=self.t_fraction if self.t_fraction is not None else 0.5,
            band=_band(self.band_min, self.band_max),
        )


class LensElementInputModel(OpticalElementInputBase):
    """A thin lens: a focal length."""

    kind: Literal[ElementKind.LENS] = ElementKind.LENS
    focal_length: Optional[quantities.Length] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.LensElementModel(**self._base_kwargs(), focal_length=self.focal_length)


class ObjectiveElementInputModel(OpticalElementInputBase):
    """A microscope objective."""

    kind: Literal[ElementKind.OBJECTIVE] = ElementKind.OBJECTIVE
    magnification: Optional[float] = None
    numerical_aperture: Optional[float] = None
    brand: Optional[str] = None
    working_distance: Optional[quantities.Length] = None
    immersion_medium: Optional[ObjectiveImmersion] = None
    iris: Optional[bool] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.ObjectiveElementModel(
            **self._base_kwargs(),
            magnification=self.magnification,
            numerical_aperture=self.numerical_aperture,
            brand=self.brand,
            working_distance=self.working_distance,
            immersion_medium=self.immersion_medium,
            iris=self.iris,
        )


class PinholeElementInputModel(OpticalElementInputBase):
    """A confocal pinhole: a diameter."""

    kind: Literal[ElementKind.PINHOLE] = ElementKind.PINHOLE
    diameter: Optional[quantities.Length] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.PinholeElementModel(**self._base_kwargs(), diameter=self.diameter)


class ApertureElementInputModel(OpticalElementInputBase):
    """A stop: a hole of some diameter, with no confocal claim attached."""

    kind: Literal[ElementKind.APERTURE] = ElementKind.APERTURE
    diameter: Optional[quantities.Length] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.ApertureElementModel(**self._base_kwargs(), diameter=self.diameter)


class FilterElementInputModel(OpticalElementInputBase):
    """A spectral filter."""

    kind: Literal[ElementKind.FILTER] = ElementKind.FILTER
    description: Optional[str] = None
    filter_kind: Optional[FilterKind] = None
    transmittance: Optional[float] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.FilterElementModel(
            **self._base_kwargs(),
            description=self.description,
            filter_kind=self.filter_kind,
            transmittance=self.transmittance,
        )


class PolarizerElementInputModel(OpticalElementInputBase):
    """A polarization filter, at an angle."""

    kind: Literal[ElementKind.POLARIZER] = ElementKind.POLARIZER
    angle_deg: Optional[float] = None
    extinction_ratio: Optional[float] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.PolarizerElementModel(**self._base_kwargs(), angle_deg=self.angle_deg, extinction_ratio=self.extinction_ratio)


class WaveplateElementInputModel(OpticalElementInputBase):
    """A retarder: a fraction of a wave, at an angle."""

    kind: Literal[ElementKind.WAVEPLATE] = ElementKind.WAVEPLATE
    angle_deg: Optional[float] = None
    retardance: Optional[float] = None
    design_wavelength: Optional[quantities.Length] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.WaveplateElementModel(
            **self._base_kwargs(),
            angle_deg=self.angle_deg,
            retardance=self.retardance,
            design_wavelength=self.design_wavelength,
        )


class ShutterElementInputModel(OpticalElementInputBase):
    """A blocker: open or closed. An AOTF or EOM used purely as a gate is one of these."""

    kind: Literal[ElementKind.SHUTTER] = ElementKind.SHUTTER
    is_open: Optional[bool] = None
    shutter_type: Optional[str] = None
    gain: Optional[float] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.ShutterElementModel(**self._base_kwargs(), is_open=self.is_open, shutter_type=self.shutter_type, gain=self.gain)


class SampleElementInputModel(OpticalElementInputBase):
    """The sample itself, as a station on the path."""

    kind: Literal[ElementKind.SAMPLE] = ElementKind.SAMPLE
    description: Optional[str] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.SampleElementModel(**self._base_kwargs(), description=self.description)


class OtherElementInputModel(OpticalElementInputBase):
    """Anything the vocabulary above does not name."""

    kind: Literal[ElementKind.OTHER] = ElementKind.OTHER
    description: Optional[str] = None

    def to_element(self) -> object:
        """Build the storage model for this element."""
        return object_models.OtherElementModel(**self._base_kwargs(), description=self.description)


#: Every element kind, keyed by discriminator value. Total over `ElementKind` by the same
#: test that keeps the storage table total, so the two sides cannot drift apart.
ELEMENT_MEMBERS: dict[str, type[OpticalElementInputBase]] = {
    ElementKind.LASER.value: LaserElementInputModel,
    ElementKind.LAMP.value: LampElementInputModel,
    ElementKind.OTHER_SOURCE.value: OtherSourceElementInputModel,
    ElementKind.DETECTOR.value: DetectorElementInputModel,
    ElementKind.CCD.value: CCDElementInputModel,
    ElementKind.MIRROR.value: MirrorElementInputModel,
    ElementKind.BEAM_SPLITTER.value: BeamSplitterElementInputModel,
    ElementKind.LENS.value: LensElementInputModel,
    ElementKind.OBJECTIVE.value: ObjectiveElementInputModel,
    ElementKind.PINHOLE.value: PinholeElementInputModel,
    ElementKind.APERTURE.value: ApertureElementInputModel,
    ElementKind.FILTER.value: FilterElementInputModel,
    ElementKind.POLARIZER.value: PolarizerElementInputModel,
    ElementKind.WAVEPLATE.value: WaveplateElementInputModel,
    ElementKind.SHUTTER.value: ShutterElementInputModel,
    ElementKind.SAMPLE.value: SampleElementInputModel,
    ElementKind.OTHER.value: OtherElementInputModel,
}

#: The union the pydantic side carries, so a resolver never sees the flat wire shape.
OpticalElementSpec = Annotated[
    LaserElementInputModel
    | LampElementInputModel
    | OtherSourceElementInputModel
    | DetectorElementInputModel
    | CCDElementInputModel
    | MirrorElementInputModel
    | BeamSplitterElementInputModel
    | LensElementInputModel
    | ObjectiveElementInputModel
    | PinholeElementInputModel
    | ApertureElementInputModel
    | FilterElementInputModel
    | PolarizerElementInputModel
    | WaveplateElementInputModel
    | ShutterElementInputModel
    | SampleElementInputModel
    | OtherElementInputModel,
    Field(discriminator="kind"),
]


class LightEdgeInputModel(BaseModel):
    """Input model for connecting two ports."""

    id: Optional[str] = None
    source_element_id: str
    source_port_id: str
    target_element_id: str
    target_port_id: str
    path_length: Optional[quantities.Length] = None
    medium: Optional[str] = Field(default="AIR")
    loss_db: Optional[float] = Field(default=0.0)
    beam: Optional[BeamStateInputModel] = None


class LightpathGraphInputModel(BaseModel):
    """Bulk graph input model for elements and edges."""

    elements: List[OpticalElementSpec]
    edges: List[LightEdgeInputModel]

    def to_graph(self) -> object_models.LightpathGraphModel:
        """The storage graph this input describes.

        **This is the ingest gate, and the whole point of the union above.** Both write
        paths used to store ``model_dump()`` of *this* model -- a shape the read side never
        validated against -- so an element the storage union could not build was accepted,
        written, and blew up later in `LightpathGraph.graph`, on a query, with a raw
        pydantic union-tag dump. Building the storage model here means the column can only
        ever hold something the read side can rebuild, and a bad element is refused at the
        mutation, naming the kind and the field.
        """
        return object_models.LightpathGraphModel(
            elements=[element.to_element() for element in self.elements],
            edges=[object_models.LightEdgeModel(**edge.model_dump(exclude_none=True)) for edge in self.edges],
        )
