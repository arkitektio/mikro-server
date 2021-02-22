from django.db.models import TextChoices

class RepresentationVariety(TextChoices):
    MASK = "MASK", "Mask"
    VOXEL = "VOXEL", "Voxel"
    UNKNOWN = "UNKNOWN", "Unknown"


class RepresentationVarietyInput(TextChoices): # We need to have an input type otherwise graphql whines 
    MASK = "MASK", "Mask"
    VOXEL = "VOXEL", "Voxel"
    UNKNOWN = "UNKNOWN", "Unknown"