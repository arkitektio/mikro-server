from datetime import datetime
from typing import Optional
from pydantic import BaseModel
import graphene
from graphene.types.generic import GenericScalar


class PhysicalSizeModel(BaseModel):
    x: float = 1
    y: float = 1
    z: float = 1
    t: float = 1


class ChannelModel(BaseModel):
    name: str = "Test"
    emmission_wavelength: float = 0
    excitation_wavelength: float = 0
    acquisition_mode: str = "Standard"
    color: str = "rgb(244,255,232)"


class PlaneModel(BaseModel):
    z_index: int = 0
    y_index: int = 0
    x_index: int = 0
    c_index: int = 0
    t_index: int = 0
    exposure_time: float = 0
    delta_t: float = 0


class OmeroRepresentationModel(BaseModel):
    planes: Optional[PlaneModel]
    channels: Optional[ChannelModel]
    physicalSize: Optional[PhysicalSizeModel]
    acquisition_date: Optional[datetime]


class PhysicalSize(graphene.ObjectType):
    x = graphene.Float()
    y = graphene.Float()
    z = graphene.Float()
    t = graphene.Float()
    c = graphene.Float()


class PhysicalSizeInput(graphene.InputObjectType):
    x = graphene.Float()
    y = graphene.Float()
    z = graphene.Float()
    t = graphene.Float()
    c = graphene.Float()


class Channel(graphene.ObjectType):
    name = graphene.String()
    emmission_wavelength = graphene.Float()
    excitation_wavelength = graphene.Float()
    acquisition_mode = graphene.String()
    color = graphene.String()


class ChannelInput(graphene.InputObjectType):
    name = graphene.String()
    emmission_wavelength = graphene.Float()
    excitation_wavelength = graphene.Float()
    acquisition_mode = graphene.String()
    color = graphene.String()


class Medium(graphene.Enum):
    AIR = "Air"
    GLYCEROL = "Glycerol"
    OIL = "Oil"
    OTHER = "Other"
    WATER = "Water"


class Plane(graphene.ObjectType):
    z = graphene.Int()
    y = graphene.Int()
    x = graphene.Int()
    c = graphene.Int()
    t = graphene.Int()
    position_x = graphene.Float()
    position_y = graphene.Float()
    position_z = graphene.Float()
    exposure_time = graphene.Float()
    deltaT = graphene.Float()


class PlaneInput(graphene.InputObjectType):
    z = graphene.Int()
    y = graphene.Int()
    x = graphene.Int()
    c = graphene.Int()
    t = graphene.Int()
    position_x = graphene.Float()
    position_y = graphene.Float()
    position_z = graphene.Float()
    exposure_time = graphene.Float()
    deltaT = graphene.Float()


class ObjectiveSettingsInput(graphene.InputObjectType):
    correction_collar = graphene.Float()
    medium = graphene.Argument(Medium)
    numerical_aperture = graphene.Float()
    working_distance = graphene.Float()


class ObjectiveSettings(graphene.ObjectType):
    correction_collar = graphene.Float()
    medium = graphene.Field(Medium)
    numerical_aperture = graphene.Float()
    working_distance = graphene.Float()


class ImagingEnvironmentInput(graphene.InputObjectType):
    air_pessure = graphene.Float()
    co2_percent = graphene.Float()
    humidity = graphene.Float()
    temperature = graphene.Float()
    map = GenericScalar()


class ImagingEnvironment(graphene.ObjectType):
    air_pressure = graphene.Float()
    co2_percent = graphene.Float()
    humidity = graphene.Float()
    temperature = graphene.Float()
    map = GenericScalar()


class OmeroRepresentationInput(graphene.InputObjectType):
    planes = graphene.List(PlaneInput)
    channels = graphene.List(ChannelInput)
    physical_size = graphene.Argument(PhysicalSizeInput)
    scale = graphene.List(graphene.Float)
    acquisition_date = graphene.DateTime()
    objective_settings = graphene.Argument(ObjectiveSettingsInput)
    imaging_environment = graphene.Argument(ImagingEnvironmentInput)
    instrument = graphene.ID()
