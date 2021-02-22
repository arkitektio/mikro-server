from .extenders import ArnheimError
import django.db.models.options as options
from django.db import models


from django.core.files.uploadedfile import InMemoryUploadedFile
options.DEFAULT_NAMES = options.DEFAULT_NAMES + ('max','slicefunction',"rescale")
import logging

logger = logging.getLogger(__name__)

class ChannelsField(models.JSONField):
    pass

class PlanesField(models.JSONField):
    pass

class AutoGenerateImageFromArrayMixin(models.Model):
    image = models.ImageField(null=True, blank=True)
   

    class Meta:
        abstract=True



    def save(self, *args, **kwargs):            
        super().save(*args, **kwargs)



class WithChannel(models.Model):
    channels = ChannelsField(null=True, blank=True)
   

    class Meta:
        abstract=True



class WithPlanes(models.Model):
    planes = PlanesField(null=True, blank=True)
   

    class Meta:
        abstract=True






