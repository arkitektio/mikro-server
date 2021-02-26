from django.db.models import TextChoices

class RepresentationVariety(TextChoices):
    """ Variety expresses the Type of Representation we are dealing with
    """
    MASK = "MASK", "Mask (Value represent Labels)"
    VOXEL = "VOXEL", "Voxel (Value represent Intensity)"
    UNKNOWN = "UNKNOWN", "Unknown"

