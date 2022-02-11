from doctest import ELLIPSIS
from balder.enum import InputEnum
from django.db.models import TextChoices


class RepresentationVariety(TextChoices):
    """Variety expresses the Type of Representation we are dealing with"""

    MASK = "MASK", "Mask (Value represent Labels)"
    VOXEL = "VOXEL", "Voxel (Value represent Intensity)"
    RGB = "RGB", "RGB (First three channel represent RGB)"
    UNKNOWN = "UNKNOWN", "Unknown"


class OmeroFileType(TextChoices):
    TIFF = "TIFF", "Tiff"
    JPEG = "JPEG", "Jpeg"
    MSR = "MSR", "MSR File"
    CZI = "CZI", "Zeiss Microscopy File"
    UNKNWON = "UNKNOWN", "Unwknon File Format"


class RoiType(TextChoices):
    ELLIPSIS = "ellipse", "Ellipse"
    POLYGON = "polygon", "POLYGON"
    LINE = "line", "Line"
    RECTANGLE = "rectangle", "Rectangle"
    PATH = "path", "Path"
    UNKNOWN = "unknown", "Unknown"


RepresentationVarietyInput = InputEnum.from_choices(RepresentationVariety)
OmeroFileTypesInput = InputEnum.from_choices(OmeroFileType)
RoiTypeInput = InputEnum.from_choices(RoiType)
