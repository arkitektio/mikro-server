from typing import Optional, List

import strawberry
from strawberry.experimental import pydantic

from kanne_server import scalars as kanne_scalars
from lightpath.objects import models
from lightpath.enums import ChannelKind, PortRole, ElementKind, ObjectiveCorrectionKind, ObjectiveImmersion, PulseKind

# ---- Geometry / beam annotations ----
@pydantic.type(models.Vec3Model, description="3D vector or point in space")
class Vec3:
    x: Optional[float] = strawberry.field(default=None, description="X coordinate")
    y: Optional[float] = strawberry.field(default=None, description="Y coordinate")
    z: Optional[float] = strawberry.field(default=None, description="Z coordinate")


@pydantic.type(models.EulerModel,description="Euler angles for 3D orientation")
class Euler:
    rx: Optional[float] = strawberry.field(default=None, description="Rotation around X axis in degrees")
    ry: Optional[float] = strawberry.field(default=None, description="Rotation around Y axis in degrees")
    rz: Optional[float] = strawberry.field(default=None, description="Rotation around Z axis in degrees")


@pydantic.type(models.Pose3DModel, description="Optional 3D pose; position and/or orientation can be omitted")
class Pose3D:
    position: Optional[Vec3] = strawberry.field(default=None, description="XYZ position (optional)")
    orientation: Optional[Euler] = strawberry.field(default=None, description="Euler orientation (optional)")


@pydantic.type(models.SpectrumModel, description="Spectral window")
class Spectrum:
    min: kanne_scalars.Length = strawberry.field(description="Minimum wavelength (e.g. '400 nm')")
    max: kanne_scalars.Length = strawberry.field(description="Maximum wavelength (e.g. '700 nm')")


@pydantic.type(models.BeamStateModel, description="Beam properties carried on a light edge")
class BeamState:
    wavelength: kanne_scalars.Length | None = strawberry.field(default=None, description="Wavelength (e.g. '488 nm')")
    power: kanne_scalars.Power | None = strawberry.field(default=None, description="Optical power (e.g. '20 mW')")
    polarization: str | None = strawberry.field(default=None, description="Polarization label")
    mode_hint: str | None = strawberry.field(default=None, description="Mode hint, e.g., TEM00")


# ---------- Port ----------
@pydantic.type(models.LightPortModel, description="Optical port on an element")
class LightPort:
    id: strawberry.ID = strawberry.field(description="Port UUID")
    name: str = strawberry.field(description="Port name")
    role: PortRole = strawberry.field(description="Port role")
    channel: ChannelKind = strawberry.field(description="Channel type")
    spectrum: Spectrum | None = strawberry.field(default=None, description="Supported spectrum")
    max_incoming_edges: int | None = strawberry.field(default=None, description="Max fan-in (for merges)")


# ---------- Optical Element Interface ----------
@strawberry.interface(description="Common interface for all optical elements")
class OpticalElement:
    id: strawberry.ID = strawberry.field(description="Element UUID")
    label: str = strawberry.field(description="Element label")
    kind: ElementKind = strawberry.field(description="Element kind")
    pose: Pose3D | None = strawberry.field(default=None, description="3D pose of the element")
    manufacturer: str | None = strawberry.field(default=None)
    model: str | None = strawberry.field(default=None)
    ports: list[LightPort] = strawberry.field(description="List of ports on the element")
    serial_number: str | None = strawberry.field(default=None)
    


# ---------- Element Subtypes (inline fields, no nested params) ----------
@pydantic.type(models.LaserElementModel, description="Light source")
class LaserElement(OpticalElement):
    id: strawberry.ID
    nominal_wavelength: kanne_scalars.Length | None = strawberry.field(description="Source wavelength (e.g. '488 nm')")
    power: kanne_scalars.Power | None = strawberry.field(default=None, description="Source power (e.g. '20 mW')")
    channel: ChannelKind | None  = strawberry.field(default=None, description="Output channel type")
    laser_medium: Optional[str] = strawberry.field(default=None, description="Laser medium (e.g., 'Ti:Sapphire', 'Nd:YAG')")
    pulse_kind: Optional[PulseKind] = strawberry.field(default=None, description="Pulse type (e.g., 'CW', 'PULSED')")
    repetition_rate: kanne_scalars.Frequency | None = strawberry.field(default=None, description="Repetition rate (e.g. '80 MHz')")
    has_pockels_cell: Optional[bool] = strawberry.field(default=None, description="Has Pockels cell")
    has_q_switch: Optional[bool] = strawberry.field(default=None, description="Has Q-switch")

@pydantic.type(models.LampElementModel, description="Light source")
class LampElement(OpticalElement):
    id: strawberry.ID
    lamp_type: str | None = strawberry.field(default=None, description="Lamp type (e.g., LED, Halogen, Xenon, Mercury)")
    channel: ChannelKind | None  = strawberry.field(default=None, description="Output channel type")


@pydantic.type(models.OtherSourceElementModel, description="Light source")
class OtherSourceElement(OpticalElement):
    id: strawberry.ID
    lamp_type: str | None = strawberry.field(default=None, description="Lamp type (e.g., LED, Halogen, Xenon, Mercury)")
    channel: ChannelKind | None  = strawberry.field(default=None, description="Output channel type")




@pydantic.type(models.DetectorElementModel, description="Detector")
class DetectorElement(OpticalElement):
    id: strawberry.ID
    nepd_w_per_sqrt_hz: float | None = strawberry.field(default=None, description="Noise-equivalent power density (W/√Hz)")
    amplifier_gain_db: float | None = strawberry.field(default=None, description="Amplifier gain (dB)")
    gain: float | None = strawberry.field(default=None, description="Overall gain (unitless)")


@pydantic.type(models.CCDElementModel, description="Detector")
class CCDElement(OpticalElement):
    id: strawberry.ID
    pixel_size: kanne_scalars.Length | None = strawberry.field(default=None, description="Pixel size (e.g. '6.5 µm')")
    resolution: Optional[List[int]] =  strawberry.field(default=None, description="Sensor resolution (width, height) in pixels")


@pydantic.type(models.MirrorElementModel, description="Mirror")
class MirrorElement(OpticalElement):
    id: strawberry.ID
    angle_deg: float | None = strawberry.field(default=None, description="Nominal incidence angle (deg)")
    band: Spectrum | None = strawberry.field(default=None, description="Coating band")


@pydantic.type(models.PinholeElementModel, description="Pinhole")
class PinholeElement(OpticalElement):
    id: strawberry.ID
    diameter: kanne_scalars.Length | None = strawberry.field(default=None, description="Diameter (e.g. '50 µm')")

@pydantic.type(models.BeamSplitterElementModel, description="Beam splitter")
class BeamSplitterElement(OpticalElement):
    id: strawberry.ID
    r_fraction: float = strawberry.field(description="Reflectance fraction (0–1)")
    t_fraction: float = strawberry.field(description="Transmittance fraction (0–1)")
    band: Spectrum | None = strawberry.field(default=None, description="Coating band")


@pydantic.type(models.LensElementModel, description="Thin lens")
class LensElement(OpticalElement):
    id: strawberry.ID
    focal_length: kanne_scalars.Length = strawberry.field(description="Focal length (e.g. '200 mm')")

@pydantic.type(models.SampleElementModel, description="The sample")
class SampleElement(OpticalElement):
    id: strawberry.ID
    label: str
    kind: ElementKind
    
@pydantic.type(models.OtherElementModel, description="The sample")
class OtherElement(OpticalElement):
    id: strawberry.ID
    label: str
    kind: ElementKind    
    
    
@pydantic.type(models.FilterElementModel, description="A filter")
class FilterElement(OpticalElement):
    id: strawberry.ID
    

@pydantic.type(models.ObjectiveElementModel, description="Microscope objective")
class ObjectiveElement(OpticalElement):
    id: strawberry.ID
    correction_kind: ObjectiveCorrectionKind | None = strawberry.field(default=None, description="Optical correction type")
    magnification: float | None = strawberry.field(default=None,description="Magnification (e.g., 20 for 20x)")
    numerical_aperture: float | None = strawberry.field(default=None,description="NA")
    iris: bool | None = strawberry.field(default=None, description="Has iris (aperture stop)")
    brand: str | None = strawberry.field(default=None, description="Brand of the objective")
    working_distance: kanne_scalars.Length | None = strawberry.field(default=None, description="Working distance (e.g. '2 mm')")
    immersion_medium: ObjectiveImmersion | None = strawberry.field(default=None, description="Immersion medium (e.g., 'OIL', 'WATER')")


@pydantic.type(models.ShutterElementModel, description="A blocker: open or closed. An AOTF or EOM used purely as a gate is one of these")
class ShutterElement(OpticalElement):
    id: strawberry.ID
    is_open: bool | None = strawberry.field(default=None, description="Whether the shutter was open at this coordinate")
    shutter_type: str | None = strawberry.field(default=None, description="How it switches, e.g. 'mechanical', 'AOTF', 'EOM'")
    gain: float | None = strawberry.field(default=None, description="Drive gain, for an AOTF or EOM (unitless)")


@pydantic.type(models.PolarizerElementModel, description="A polarization filter, at an angle")
class PolarizerElement(OpticalElement):
    id: strawberry.ID
    angle_deg: float | None = strawberry.field(default=None, description="Transmission axis angle, in degrees")
    extinction_ratio: float | None = strawberry.field(default=None, description="Ratio of transmitted to blocked polarization")


@pydantic.type(models.WaveplateElementModel, description="A retarder: a fraction of a wave, at an angle")
class WaveplateElement(OpticalElement):
    id: strawberry.ID
    angle_deg: float | None = strawberry.field(default=None, description="Fast-axis angle, in degrees")
    retardance: float | None = strawberry.field(default=None, description="Retardance in waves, e.g. 0.5 for a half-wave plate")
    design_wavelength: kanne_scalars.Length | None = strawberry.field(default=None, description="The wavelength the retardance is specified at (e.g. '532 nm')")


@pydantic.type(models.ApertureElementModel, description="A stop: a hole of some diameter. A pinhole with no confocal claim attached")
class ApertureElement(OpticalElement):
    id: strawberry.ID
    diameter: kanne_scalars.Length | None = strawberry.field(default=None, description="Aperture diameter (e.g. '5 mm')")

# ---------- Edge ----------
@pydantic.type(models.LightEdgeModel, description="Directed edge connecting two ports")
class LightEdge:
    id: strawberry.ID = strawberry.field(description="Edge UUID")
    source_element_id: strawberry.ID = strawberry.field(description="Source element UUID")
    source_port_id: strawberry.ID = strawberry.field(description="Source port UUID")
    target_element_id: strawberry.ID = strawberry.field(description="Target element UUID")
    target_port_id: strawberry.ID = strawberry.field(description="Target port UUID")
    path_length: kanne_scalars.Length | None = strawberry.field(default=None, description="Path length (e.g. '100 mm')")
    medium: str | None = strawberry.field(default="AIR", description="Propagation medium")
    loss_db: float | None = strawberry.field(default=0.0, description="Insertion loss (dB)")
    beam: BeamState | None = strawberry.field(default=None, description="Beam state annotation")


# ---------- Graph DTO ----------
@pydantic.type(models.LightpathGraphModel, description="Graph of optical elements and edges")
class LightpathGraph:
    elements: list[OpticalElement] = strawberry.field(description="All elements in the graph")
    edges: list[LightEdge] = strawberry.field(description="All edges in the graph")


