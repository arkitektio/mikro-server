from matrise.models import Matrise
from django.db import models
from grunnlag.models import Representation, Sample


# Create your models here.


class MultiScaleRepresentation(Matrise):
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, help_text="The Sample this Image bleongs to" )
    created_from = models.ForeignKey(Representation, on_delete=models.CASCADE,help_text="The Representation that was used to create a multiscale")
