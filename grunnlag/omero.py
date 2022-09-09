from datetime import datetime
from typing import Optional
from pydantic import BaseModel
import graphene


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
    acquistion_date: Optional[datetime]


class PhysicalSize(graphene.ObjectType):
    x = graphene.Int()
    y = graphene.Int()
    z = graphene.Int()
    t = graphene.Int()
    c = graphene.Int()


class PhysicalSizeInput(graphene.InputObjectType):
    x = graphene.Int()
    y = graphene.Int()
    z = graphene.Int()
    t = graphene.Int()
    c = graphene.Int()


class Channel(graphene.ObjectType):
    name = graphene.String()
    emmissionWavelength = graphene.Float()
    excitationWavelength = graphene.Float()
    acquisitionMode = graphene.String()
    color = graphene.String()


class ChannelInput(graphene.InputObjectType):
    name = graphene.String()
    emmissionWavelength = graphene.Float()
    excitationWavelength = graphene.Float()
    acquisitionMode = graphene.String()
    color = graphene.String()


class Plane(graphene.ObjectType):
    zIndex = graphene.Int()
    yIndex = graphene.Int()
    xIndex = graphene.Int()
    cIndex = graphene.Int()
    tIndex = graphene.Int()
    exposureTime = graphene.Float()
    deltaT = graphene.Float()


class PlaneInput(graphene.InputObjectType):
    zIndex = graphene.Int()
    yIndex = graphene.Int()
    xIndex = graphene.Int()
    cIndex = graphene.Int()
    tIndex = graphene.Int()
    exposureTime = graphene.Float()
    deltaT = graphene.Float()


class OmeroRepresentationInput(graphene.InputObjectType):
    planes = graphene.List(PlaneInput)
    channels = graphene.List(ChannelInput)
    physicalSize = graphene.Argument(PhysicalSizeInput)
    scale = graphene.List(graphene.Float)
    acquisition_date = graphene.DateTime()
