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
    """ Physical size of the image 
    
    Each dimensions of the image has a physical size. This is the size of the
    pixel in the image. The physical size is given in micrometers on a PIXEL
    basis. This means that the physical size of the image is the size of the    
    pixel in the image * the number of pixels in the image. For example, if 
    the image is 1000x1000 pixels and the physical size of the image is 3 (x params) x 3 (y params),
    micrometer, the physical size of the image is 3000x3000 micrometer. If the image

    The t dimension is given in ms, since the time is given in ms.
    The C dimension is given in nm, since the wavelength is given in nm.
    
    """



    x = graphene.Float(description="Physical size of *one* Pixel in the x dimension (in µm)")
    y = graphene.Float(description="Physical size of *one* Pixel in the t dimension (in µm)")
    z = graphene.Float(description="Physical size of *one* Pixel in the z dimension (in µm)")
    t = graphene.Float(description="Physical size of *one* Pixel in the t dimension (in ms)")
    c = graphene.Float(description="Physical size of *one* Pixel in the c dimension (in µm)")


class PhysicalSizeInput(graphene.InputObjectType):
    """ Physical size of the image 
    
    Each dimensions of the image has a physical size. This is the size of the
    pixel in the image. The physical size is given in micrometers on a PIXEL
    basis. This means that the physical size of the image is the size of the    
    pixel in the image * the number of pixels in the image. For example, if 
    the image is 1000x1000 pixels and the physical size of the image is 3 (x params) x 3 (y params),
    micrometer, the physical size of the image is 3000x3000 micrometer. If the image

    The t dimension is given in ms, since the time is given in ms.
    The C dimension is given in nm, since the wavelength is given in nm.
    
    """



    x = graphene.Float(description="Physical size of *one* Pixel in the x dimension (in µm)")
    y = graphene.Float(description="Physical size of *one* Pixel in the t dimension (in µm)")
    z = graphene.Float(description="Physical size of *one* Pixel in the z dimension (in µm)")
    t = graphene.Float(description="Physical size of *one* Pixel in the t dimension (in ms)")
    c = graphene.Float(description="Physical size of *one* Pixel in the c dimension (in nm)")


class Channel(graphene.ObjectType):
    """ A channel in an image

    Channels can be highly variable in their properties. This class is a
    representation of the most common properties of a channel.
    """

    name = graphene.String(description="The name of the channel")
    emmission_wavelength = graphene.Float(description="The emmission wavelength of the fluorophore in nm")
    excitation_wavelength = graphene.Float(description="The excitation wavelength of the fluorophore in nm")
    acquisition_mode = graphene.String(description="The acquisition mode of the channel")
    color = graphene.String(description="The default color for the channel (might be ommited by the rendered)")


class ChannelInput(graphene.InputObjectType):
    """ A channel in an image

    Channels can be highly variable in their properties. This class is a
    representation of the most common properties of a channel.
    """

    name = graphene.String(description="The name of the channel")
    emmission_wavelength = graphene.Float(description="The emmission wavelength of the fluorophore in nm")
    excitation_wavelength = graphene.Float(description="The excitation wavelength of the fluorophore in nm")
    acquisition_mode = graphene.String(description="The acquisition mode of the channel")
    color = graphene.String(description="The default color for the channel (might be ommited by the rendered)")


class Medium(graphene.Enum):
    """ The medium of the imaging environment 
    
    Important for the objective settings"""
    AIR = "Air"
    GLYCEROL = "Glycerol"
    OIL = "Oil"
    OTHER = "Other"
    WATER = "Water"


class Plane(graphene.ObjectType):
    """ A plane in an image 

    Plane follows the convention of the OME model, where the first index is the
    Z axis, the second is the Y axis, the third is the X axis, the fourth is the
    C axis, and the fifth is the T axis.

    It attached the image at the indicated index to the image and gives information
    about the plane (e.g. exposure time, delta t to the origin, etc.)


    """


    z = graphene.Int(description="Z index of the plane")
    y = graphene.Int(description="Y index of the plane")
    x = graphene.Int(description="X index of the plane")
    c = graphene.Int(description="C index of the plane")
    t = graphene.Int(description="Z index of the plane")
    position_x = graphene.Float(description="The planes X position on the stage of the microscope")
    position_y = graphene.Float(description="The planes Y position on the stage of the microscope")
    position_z = graphene.Float(description="The planes Z position on the stage of the microscope")
    exposure_time = graphene.Float(description="The exposure time of the plane (e.g. Laser exposure)")
    deltaT = graphene.Float(description="The Delta T to the origin of the image acqusition")


class PlaneInput(graphene.InputObjectType):
    """" A plane in an image 

    Plane follows the convention of the OME model, where the first index is the
    Z axis, the second is the Y axis, the third is the X axis, the fourth is the
    C axis, and the fifth is the T axis.

    It attached the image at the indicated index to the image and gives information
    about the plane (e.g. exposure time, delta t to the origin, etc.)


    """



    z = graphene.Int(description="Z index of the plane")
    y = graphene.Int(description="Y index of the plane")
    x = graphene.Int(description="X index of the plane")
    c = graphene.Int(description="C index of the plane")
    t = graphene.Int(description="Z index of the plane")
    position_x = graphene.Float(description="The planes X position on the stage of the microscope")
    position_y = graphene.Float(description="The planes Y position on the stage of the microscope")
    position_z = graphene.Float(description="The planes Z position on the stage of the microscope")
    exposure_time = graphene.Float(description="The exposure time of the plane (e.g. Laser exposure)")
    deltaT = graphene.Float(description="The Delta T to the origin of the image acqusition")


class ObjectiveSettingsInput(graphene.InputObjectType):
    """ Settings of the objective used to acquire the image 
    
    Follows the OME model for objective settings
    
    """


    correction_collar = graphene.Float(description="The correction collar of the objective")
    medium = graphene.Argument(Medium, description="The medium of the objective")
    numerical_aperture = graphene.Float(description="The numerical aperture of the objective")
    working_distance = graphene.Float(description="The working distance of the objective")


class ObjectiveSettings(graphene.ObjectType):
    """ Settings of the objective used to acquire the image 
    
    Follows the OME model for objective settings
    
    """
    correction_collar = graphene.Float(description="The correction collar of the objective")
    medium = graphene.Field(Medium, description="The medium of the objective")
    numerical_aperture = graphene.Float(description="The numerical aperture of the objective")
    working_distance = graphene.Float(description="The working distance of the objective")


class ImagingEnvironmentInput(graphene.InputObjectType):
    """ The imaging environment during the acquisition 
    
    Follows the OME model for imaging environment
    
    """
    air_pessure = graphene.Float(description="The air pressure during the acquisition")
    co2_percent = graphene.Float(description="The CO2 percentage in the environment")
    humidity = graphene.Float(description="The humidity of the imaging environment")
    temperature = graphene.Float(description="The temperature of the imaging environment")
    map = GenericScalar(description="A map of the imaging environment. Key value based")


class ImagingEnvironment(graphene.ObjectType):
    """ The imaging environment during the acquisition 
    
    Follows the OME model for imaging environment
    
    """
    air_pessure = graphene.Float(description="The air pressure during the acquisition")
    co2_percent = graphene.Float(description="The CO2 percentage in the environment")
    humidity = graphene.Float(description="The humidity of the imaging environment")
    temperature = graphene.Float(description="The temperature of the imaging environment")
    map = GenericScalar(description="A map of the imaging environment. Key value based")


class OmeroRepresentationInput(graphene.InputObjectType):
    """ The Omero Meta Data of an Image

    Follows closely the omexml model. With a few alterations:
    - The data model of the datasets and experimenters is
    part of the mikro datamodel and are not accessed here.
    - Some parameters are ommited as they are not really used

    """






    planes = graphene.List(PlaneInput)
    channels = graphene.List(ChannelInput)
    physical_size = graphene.Argument(PhysicalSizeInput)
    scale = graphene.List(graphene.Float)
    acquisition_date = graphene.DateTime()
    objective_settings = graphene.Argument(ObjectiveSettingsInput)
    imaging_environment = graphene.Argument(ImagingEnvironmentInput)
    instrument = graphene.ID()
