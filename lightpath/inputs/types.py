import strawberry
from pydantic import BaseModel
from strawberry.experimental import pydantic
from typing import Optional, List


from kanne_server import scalars as kanne_scalars
from core.input_unions import parse_union_member, prose_errors, union_memberships
from lightpath.enums import ChannelKind, FilterKind, ObjectiveImmersion, PortRole, ElementKind, PulseKind
from lightpath.inputs import models  # your Pydantic input models


# ---- Small value types ----
@pydantic.input(models.Vec3InputModel, description="A 3D vector representing a point or offset in space.")
class Vec3Input:
    x: Optional[float] = strawberry.field(default=None, description="X coordinate of the vector.")
    y: Optional[float] = strawberry.field(default=None, description="Y coordinate of the vector.")
    z: Optional[float] = strawberry.field(default=None, description="Z coordinate of the vector.")


@pydantic.input(models.EulerInputModel, description="Euler angles representing rotation in 3D space.")
class EulerInput:
    rx: Optional[float] = strawberry.field(default=None, description="Rotation around the X axis, in degrees.")
    ry: Optional[float] = strawberry.field(default=None, description="Rotation around the Y axis, in degrees.")
    rz: Optional[float] = strawberry.field(default=None, description="Rotation around the Z axis, in degrees.")


@pydantic.input(models.Pose3DInputModel, description="A 3D pose consisting of position and orientation.")
class Pose3DInput:
    position: Optional[Vec3Input] = strawberry.field(default=None, description="3D position vector of the element.")
    orientation: Optional[EulerInput] = strawberry.field(default=None, description="3D orientation as Euler angles.")


@pydantic.input(models.SpectrumInputModel, description="Spectral window for wavelength-dependent components.")
class SpectrumInput:
    min: kanne_scalars.Length = strawberry.field(description="Minimum wavelength supported (e.g. '400 nm').")
    max: kanne_scalars.Length = strawberry.field(description="Maximum wavelength supported (e.g. '700 nm').")


@pydantic.input(models.BeamStateInputModel, description="State of the optical beam on a particular path segment.")
class BeamStateInput:
    wavelength: kanne_scalars.Length | None = strawberry.field(default=None, description="Nominal wavelength of the beam (e.g. '488 nm').")
    power: kanne_scalars.Power | None = strawberry.field(default=None, description="Optical power of the beam (e.g. '20 mW').")
    polarization: Optional[str] = strawberry.field(default=None, description="Polarization state (e.g., linear, circular).")
    mode_hint: Optional[str] = strawberry.field(default=None, description="Optional mode hint (e.g., TEM00).")


# ---- Port input ----
@pydantic.input(models.LightPortInputModel, description="Input definition for an optical port on an element.")
class LightPortInput:
    id:  strawberry.ID  = strawberry.field(default=None, description="Optional UUID of the port (provide for updates).")
    name: str = strawberry.field(description="Human-readable name for the port.")
    role: PortRole = strawberry.field(description="Directionality of the port: INPUT, OUTPUT, or BIDIRECTIONAL.")
    channel: ChannelKind = strawberry.field(default=ChannelKind.FREE_SPACE, description="Propagation channel type for the port.")
    spectrum: Optional[SpectrumInput] = strawberry.field(default=None, description="Spectral range supported by this port.")
    

# ---- Optical element input ----
#
# The wire type stays flat, because GraphQL has no input unions -- but `kind` now *selects*
# a member, and only that member's fields are read. Anything else is an error naming both
# the kind and the field, where it used to be carried along in the dump and then explode on
# a later read. The member inputs below are the same truth, published for codegen.


@prose_errors
@strawberry.input(
    description=(
        "One optical element, as a discriminated union: `kind` selects a member, and only that member's fields are read -- any other supplied field is rejected, never carried. "
        'The member inputs annotated `@unionElementOf(union: "OpticalElementInput")` say which fields each kind reads'
    ),
)
class OpticalElementInput:
    """One optical element, discriminated by `kind`.

    Deliberately not pydantic-backed: the wire type is flat, and ``to_pydantic`` is where
    that flatness is corrected into the strict member model.
    """

    label: str = strawberry.field(description="Human-readable label for the optical element.")
    kind: ElementKind = strawberry.field(description="Kind of optical element. It fixes which of the fields below are read; any field outside the chosen kind's member is rejected.")
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates).")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element.")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="List of optical ports belonging to the element.")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element.")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element.")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element.")

    nominal_wavelength: kanne_scalars.Length | None = strawberry.field(default=None, description="(LASER) Nominal output wavelength (e.g. '488 nm').")
    power: kanne_scalars.Power | None = strawberry.field(default=None, description="(LASER) Output power (e.g. '20 mW').")
    channel: Optional[ChannelKind] = strawberry.field(default=None, description="(LASER, LAMP, OTHER_SOURCE) Propagation channel of the output.")
    lamp_type: Optional[str] = strawberry.field(default=None, description="(LAMP, OTHER_SOURCE) Lamp type, e.g. 'LED', 'Halogen', 'Xenon', 'Mercury'.")
    laser_medium: Optional[str] = strawberry.field(default=None, description="(LASER) Laser medium, e.g. 'Ti:Sapphire', 'Nd:YAG'.")
    pulse_kind: Optional[PulseKind] = strawberry.field(default=None, description="(LASER) Pulse type, e.g. 'CW', 'ModeLocked'.")
    repetition_rate: kanne_scalars.Frequency | None = strawberry.field(default=None, description="(LASER) Repetition rate (e.g. '80 MHz').")
    has_pockels_cell: Optional[bool] = strawberry.field(default=None, description="(LASER) Has a Pockels cell.")
    has_q_switch: Optional[bool] = strawberry.field(default=None, description="(LASER) Has a Q-switch.")

    nepd_w_per_sqrt_hz: Optional[float] = strawberry.field(default=None, description="(DETECTOR) Noise-equivalent power density (W/√Hz).")
    amplifier_gain_db: float | None = strawberry.field(default=None, description="(DETECTOR) Amplifier gain (dB).")
    gain: float | None = strawberry.field(default=None, description="(DETECTOR, SHUTTER) Overall gain, unitless -- the drive gain for an AOTF or EOM used as a gate.")
    pixel_size: kanne_scalars.Length | None = strawberry.field(default=None, description="(CCD) Pixel size (e.g. '6.5 µm').")
    resolution: Optional[List[int]] = strawberry.field(default=None, description="(CCD) Sensor resolution (width, height) in pixels.")

    diameter: kanne_scalars.Length | None = strawberry.field(default=None, description="(PINHOLE, APERTURE) Diameter of the hole (e.g. '50 µm').")
    angle_deg: Optional[float] = strawberry.field(default=None, description="(MIRROR, POLARIZER, WAVEPLATE) The relevant angle, in degrees.")
    band_min: kanne_scalars.Length | None = strawberry.field(default=None, description="(MIRROR, BEAM_SPLITTER) Minimum wavelength of the coating band (e.g. '400 nm').")
    band_max: kanne_scalars.Length | None = strawberry.field(default=None, description="(MIRROR, BEAM_SPLITTER) Maximum wavelength of the coating band (e.g. '700 nm').")
    r_fraction: Optional[float] = strawberry.field(default=None, description="(BEAM_SPLITTER) Reflectance fraction (0-1).")
    t_fraction: Optional[float] = strawberry.field(default=None, description="(BEAM_SPLITTER) Transmittance fraction (0-1).")
    focal_length: kanne_scalars.Length | None = strawberry.field(default=None, description="(LENS) Focal length (e.g. '200 mm').")

    magnification: Optional[float] = strawberry.field(default=None, description="(OBJECTIVE) Magnification factor, e.g. 20 for 20x.")
    numerical_aperture: Optional[float] = strawberry.field(default=None, description="(OBJECTIVE) Numerical aperture.")
    brand: Optional[str] = strawberry.field(default=None, description="(OBJECTIVE) Brand of the objective.")
    working_distance: kanne_scalars.Length | None = strawberry.field(default=None, description="(OBJECTIVE) Working distance (e.g. '2 mm').")
    immersion_medium: ObjectiveImmersion | None = strawberry.field(default=None, description="(OBJECTIVE) Immersion medium, e.g. 'Oil', 'Water'.")
    iris: bool | None = strawberry.field(default=None, description="(OBJECTIVE) Has an iris (aperture stop).")

    filter_kind: FilterKind | None = strawberry.field(default=None, description="(FILTER) The kind of filter, e.g. 'Dichroic', 'BandPass'.")
    transmittance: Optional[float] = strawberry.field(default=None, description="(FILTER) Peak transmittance (0-1).")
    extinction_ratio: Optional[float] = strawberry.field(default=None, description="(POLARIZER) Ratio of transmitted to blocked polarization.")
    retardance: Optional[float] = strawberry.field(default=None, description="(WAVEPLATE) Retardance in waves, e.g. 0.5 for a half-wave plate.")
    design_wavelength: kanne_scalars.Length | None = strawberry.field(default=None, description="(WAVEPLATE) The wavelength the retardance is specified at (e.g. '532 nm').")
    is_open: Optional[bool] = strawberry.field(default=None, description="(SHUTTER) Whether the shutter was open at this coordinate.")
    shutter_type: Optional[str] = strawberry.field(default=None, description="(SHUTTER) How it switches, e.g. 'mechanical', 'AOTF', 'EOM'.")
    description: Optional[str] = strawberry.field(default=None, description="(SAMPLE, OTHER, FILTER) A free-form description of the element.")

    def to_pydantic(self) -> BaseModel:
        """Match the flat wire fields to the member model `kind` selects, strictly."""
        supplied = {name: getattr(self, name) for name in _ELEMENT_WIRE_FIELDS}
        data = {name: value for name, value in supplied.items() if value is not None}
        data["ports"] = [port.to_pydantic() for port in (self.ports or [])]
        return parse_union_member(models.ELEMENT_MEMBERS, data, noun="optical element")


#: The wire fields, read off the flat input once. `ports` is handled separately: it is a
#: list of nested inputs rather than a scalar, and an empty one is meaningful.
_ELEMENT_WIRE_FIELDS = [name for name in models.OpticalElementInputBase.model_fields if name != "ports"] + [
    "nominal_wavelength",
    "power",
    "channel",
    "lamp_type",
    "laser_medium",
    "pulse_kind",
    "repetition_rate",
    "has_pockels_cell",
    "has_q_switch",
    "nepd_w_per_sqrt_hz",
    "amplifier_gain_db",
    "gain",
    "pixel_size",
    "resolution",
    "diameter",
    "angle_deg",
    "band_min",
    "band_max",
    "r_fraction",
    "t_fraction",
    "focal_length",
    "magnification",
    "numerical_aperture",
    "brand",
    "working_distance",
    "immersion_medium",
    "iris",
    "filter_kind",
    "transmittance",
    "extinction_ratio",
    "retardance",
    "design_wavelength",
    "is_open",
    "shutter_type",
    "description",
]

# ---- The union members, published for codegen ----
#
# Referenced by no field, exactly like the transform union's members: they exist so a
# generated client can rebuild the tagged union from the `@unionElementOf` annotations.
#
# **Each member declares the parent's common fields as well as its own.** That is what makes
# a member a usable type rather than a fragment: GraphQL input types have no inheritance, so
# a client that generates `ShutterElementInput` from the annotation alone would otherwise be
# unable to say what the element is *called*, where it sits, or what ports it has. `kind` is
# declared too, and carries the member's own value as its default -- so the generated type
# is self-describing about which member it is, and the discriminator does not have to be
# threaded in by hand at every call site.

_BASE_DESCRIPTION = "Published for codegen; the wire type is the flat OpticalElementInput"


def _member(model: type, key: ElementKind, description: str):  # noqa: ANN202 - a decorator factory
    """Publish one member input of the OpticalElementInput union."""
    return pydantic.input(model, directives=union_memberships("OpticalElementInput", key=key.value), description=f"{description}. {_BASE_DESCRIPTION}")


@_member(models.LaserElementInputModel, ElementKind.LASER, "The fields a LASER element reads")
class LaserElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    nominal_wavelength: kanne_scalars.Length = strawberry.field(description="Nominal output wavelength (e.g. '488 nm')")
    power: kanne_scalars.Power | None = strawberry.field(default=None, description="Output power (e.g. '20 mW')")
    channel: Optional[ChannelKind] = strawberry.field(default=None, description="Propagation channel of the output")
    laser_medium: Optional[str] = strawberry.field(default=None, description="Laser medium, e.g. 'Ti:Sapphire'")
    pulse_kind: Optional[PulseKind] = strawberry.field(default=None, description="Pulse type, e.g. 'CW'")
    repetition_rate: kanne_scalars.Frequency | None = strawberry.field(default=None, description="Repetition rate (e.g. '80 MHz')")
    has_pockels_cell: Optional[bool] = strawberry.field(default=None, description="Has a Pockels cell")
    has_q_switch: Optional[bool] = strawberry.field(default=None, description="Has a Q-switch")


@_member(models.LampElementInputModel, ElementKind.LAMP, "The fields a LAMP element reads")
class LampElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    channel: Optional[ChannelKind] = strawberry.field(default=None, description="Propagation channel of the output")
    lamp_type: Optional[str] = strawberry.field(default=None, description="Lamp type, e.g. 'LED', 'Xenon'")


@_member(models.OtherSourceElementInputModel, ElementKind.OTHER_SOURCE, "The fields an OTHER_SOURCE element reads")
class OtherSourceElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    channel: Optional[ChannelKind] = strawberry.field(default=None, description="Propagation channel of the output")
    lamp_type: Optional[str] = strawberry.field(default=None, description="Source type, if it has a name")


@_member(models.DetectorElementInputModel, ElementKind.DETECTOR, "The fields a DETECTOR element reads")
class DetectorElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    nepd_w_per_sqrt_hz: Optional[float] = strawberry.field(default=None, description="Noise-equivalent power density (W/√Hz)")
    amplifier_gain_db: Optional[float] = strawberry.field(default=None, description="Amplifier gain (dB)")
    gain: Optional[float] = strawberry.field(default=None, description="Overall gain (unitless)")


@_member(models.CCDElementInputModel, ElementKind.CCD, "The fields a CCD element reads")
class CCDElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    pixel_size: kanne_scalars.Length | None = strawberry.field(default=None, description="Pixel size (e.g. '6.5 µm')")
    resolution: Optional[List[int]] = strawberry.field(default=None, description="Sensor resolution (width, height) in pixels")


@_member(models.MirrorElementInputModel, ElementKind.MIRROR, "The fields a MIRROR element reads")
class MirrorElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    angle_deg: Optional[float] = strawberry.field(default=None, description="Angle of incidence, in degrees")
    band_min: kanne_scalars.Length | None = strawberry.field(default=None, description="Minimum wavelength of the coating band")
    band_max: kanne_scalars.Length | None = strawberry.field(default=None, description="Maximum wavelength of the coating band")


@_member(models.BeamSplitterElementInputModel, ElementKind.BEAM_SPLITTER, "The fields a BEAM_SPLITTER element reads")
class BeamSplitterElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    r_fraction: Optional[float] = strawberry.field(default=None, description="Reflectance fraction (0-1)")
    t_fraction: Optional[float] = strawberry.field(default=None, description="Transmittance fraction (0-1)")
    band_min: kanne_scalars.Length | None = strawberry.field(default=None, description="Minimum wavelength of the coating band")
    band_max: kanne_scalars.Length | None = strawberry.field(default=None, description="Maximum wavelength of the coating band")


@_member(models.LensElementInputModel, ElementKind.LENS, "The fields a LENS element reads")
class LensElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    focal_length: kanne_scalars.Length | None = strawberry.field(default=None, description="Focal length (e.g. '200 mm')")


@_member(models.ObjectiveElementInputModel, ElementKind.OBJECTIVE, "The fields an OBJECTIVE element reads")
class ObjectiveElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    magnification: Optional[float] = strawberry.field(default=None, description="Magnification factor, e.g. 20 for 20x")
    numerical_aperture: Optional[float] = strawberry.field(default=None, description="Numerical aperture")
    brand: Optional[str] = strawberry.field(default=None, description="Brand of the objective")
    working_distance: kanne_scalars.Length | None = strawberry.field(default=None, description="Working distance (e.g. '2 mm')")
    immersion_medium: ObjectiveImmersion | None = strawberry.field(default=None, description="Immersion medium, e.g. 'Oil'")
    iris: Optional[bool] = strawberry.field(default=None, description="Has an iris (aperture stop)")


@_member(models.PinholeElementInputModel, ElementKind.PINHOLE, "The fields a PINHOLE element reads")
class PinholeElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    diameter: kanne_scalars.Length | None = strawberry.field(default=None, description="Diameter (e.g. '50 µm')")


@_member(models.ApertureElementInputModel, ElementKind.APERTURE, "The fields an APERTURE element reads")
class ApertureElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    diameter: kanne_scalars.Length | None = strawberry.field(default=None, description="Aperture diameter (e.g. '5 mm')")


@_member(models.FilterElementInputModel, ElementKind.FILTER, "The fields a FILTER element reads")
class FilterElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    description: Optional[str] = strawberry.field(default=None, description="A free-form description of the filter")
    filter_kind: FilterKind | None = strawberry.field(default=None, description="The kind of filter, e.g. 'Dichroic'")
    transmittance: Optional[float] = strawberry.field(default=None, description="Peak transmittance (0-1)")


@_member(models.PolarizerElementInputModel, ElementKind.POLARIZER, "The fields a POLARIZER element reads")
class PolarizerElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    angle_deg: Optional[float] = strawberry.field(default=None, description="Transmission axis angle, in degrees")
    extinction_ratio: Optional[float] = strawberry.field(default=None, description="Ratio of transmitted to blocked polarization")


@_member(models.WaveplateElementInputModel, ElementKind.WAVEPLATE, "The fields a WAVEPLATE element reads")
class WaveplateElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    angle_deg: Optional[float] = strawberry.field(default=None, description="Fast-axis angle, in degrees")
    retardance: Optional[float] = strawberry.field(default=None, description="Retardance in waves, e.g. 0.5 for a half-wave plate")
    design_wavelength: kanne_scalars.Length | None = strawberry.field(default=None, description="The wavelength the retardance is specified at")


@_member(models.ShutterElementInputModel, ElementKind.SHUTTER, "The fields a SHUTTER element reads")
class ShutterElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    is_open: Optional[bool] = strawberry.field(default=None, description="Whether the shutter was open at this coordinate")
    shutter_type: Optional[str] = strawberry.field(default=None, description="How it switches, e.g. 'mechanical', 'AOTF', 'EOM'")
    gain: Optional[float] = strawberry.field(default=None, description="Drive gain, for an AOTF or EOM (unitless)")


@_member(models.SampleElementInputModel, ElementKind.SAMPLE, "The fields a SAMPLE element reads")
class SampleElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    description: Optional[str] = strawberry.field(default=None, description="A free-form description of the sample")


@_member(models.OtherElementInputModel, ElementKind.OTHER, "The fields an OTHER element reads")
class OtherElementInput:
    id: strawberry.ID | None = strawberry.field(default=None, description="Optional UUID of the element (provide for updates)")
    label: str = strawberry.field(description="Human-readable label for the optical element")
    kind: ElementKind = strawberry.field(description="The discriminator: which member of OpticalElementInput this is")
    pose: Optional[Pose3DInput] = strawberry.field(default=None, description="Optional spatial pose of the element")
    ports: List[LightPortInput] | None = strawberry.field(default=None, description="The optical ports belonging to the element")
    manufacturer: Optional[str] = strawberry.field(default=None, description="Manufacturer of the optical element")
    model: Optional[str] = strawberry.field(default=None, description="Model name or number of the optical element")
    serial_number: Optional[str] = strawberry.field(default=None, description="Serial number of the optical element")
    description: Optional[str] = strawberry.field(default=None, description="A free-form description of the element")


#: The member inputs published to the SDL, for the schema's ``types=[...]``. Dropping one
#: erases it from the SDL silently -- they are referenced by no field.
element_union_types: list[type] = [
    LaserElementInput,
    LampElementInput,
    OtherSourceElementInput,
    DetectorElementInput,
    CCDElementInput,
    MirrorElementInput,
    BeamSplitterElementInput,
    LensElementInput,
    ObjectiveElementInput,
    PinholeElementInput,
    ApertureElementInput,
    FilterElementInput,
    PolarizerElementInput,
    WaveplateElementInput,
    ShutterElementInput,
    SampleElementInput,
    OtherElementInput,
]


# ---- Edge input ----
@pydantic.input(models.LightEdgeInputModel, description="Input for connecting two optical ports.")
class LightEdgeInput:
    id:str = strawberry.field(default=None, description="Optional UUID of the edge (provide for updates).")
    source_element_id: strawberry.ID = strawberry.field(description="UUID of the source element.")
    source_port_id:  strawberry.ID  = strawberry.field(description="UUID of the source port.")
    target_element_id:  strawberry.ID  = strawberry.field(description="UUID of the target element.")
    target_port_id:  strawberry.ID  = strawberry.field(description="UUID of the target port.")
    path_length: kanne_scalars.Length | None = strawberry.field(default=None, description="Geometric path length between ports (e.g. '100 mm').")
    medium: Optional[str] = strawberry.field(default="AIR", description="Propagation medium for the edge (default is AIR).")
    loss_db: Optional[float] = strawberry.field(default=0.0, description="Insertion loss along this edge, in decibels.")
    beam: Optional[BeamStateInput] = strawberry.field(default=None, description="Beam state annotation for this edge.")


# ---- Graph input ----
@pydantic.input(models.LightpathGraphInputModel, description="Bulk input for a full lightpath graph, including elements and edges.")
class LightpathGraphInput:
    elements: List[OpticalElementInput] = strawberry.field(description="List of all optical elements to include in the graph.")
    edges: List[LightEdgeInput] = strawberry.field(description="List of all edges connecting elements in the graph.")
