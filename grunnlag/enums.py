from balder.enum import InputEnum
from django.db.models import TextChoices


class RepresentationVariety(TextChoices):
    """Variety expresses the Type of Representation we are dealing with"""

    MASK = "MASK", "Mask (Value represent Labels)"
    VOXEL = "VOXEL", "Voxel (Value represent Intensity)"
    UNKNOWN = "UNKNOWN", "Unknown"


class OmeroFileType(TextChoices):
    TIFF = "TIFF", "Tiff"
    JPEG = "JPEG", "Jpeg"
    MSR = "MSR", "MSR File"
    CZI = "CZI", "Zeiss Microscopy File"
    UNKNWON = "UNKNOWN", "Unwknon File Format"


RepresentationVarietyInput = InputEnum.from_choices(RepresentationVariety)
OmeroFileTypesInput = InputEnum.from_choices(OmeroFileType)
