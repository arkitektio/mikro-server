from abc import abstractclassmethod
from grunnlag.storage import PrivateMediaStorage
from grunnlag.managers import RepresentationManager
from django.db import models

# Create your models here.
from .enums import RepresentationVariety
import logging

from django.contrib.auth import get_user_model
from django.db import models

from taggit.managers import TaggableManager
from matrise.models import  Matrise

logger = logging.getLogger(__name__)

def get_sentinel_user():
    return get_user_model().objects.get_or_create(username='deleted')[0]


class Antibody(models.Model):

    name = models.CharField(max_length=100)
    creator = models.ForeignKey(get_user_model(), blank=True, on_delete=models.CASCADE)

    def __str__(self):
        return "{0}".format(self.name)







class Experiment(models.Model):
    meta = models.JSONField(null=True, blank=True)
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=1000, null=True, blank=True)
    description_long = models.TextField(null=True,blank=True)
    linked_paper = models.URLField(null=True,blank=True)
    image = models.ImageField(upload_to='experiment_banner',null=True,blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True)
    tags = TaggableManager()

    def __str__(self):
        return "Experiment {0} by {1}".format(self.name,self.creator.username)


class ExperimentalGroup(models.Model):
    name = models.CharField(max_length=200, help_text="The experimental groups name")
    description = models.CharField(max_length=1000,  help_text="A brief summary of applied techniques in this group")
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, help_text="The experiment this Group belongs too")
    iscontrol = models.BooleanField(help_text="Is this Experimental Group a ControlGroup?")

    def __str__(self):
        return "ExperimentalGroup {0} on Experiment {1}".format(self.name,self.experiment.name)



class Animal(models.Model):
    name = models.CharField(max_length=100)
    age = models.CharField(max_length=400)
    type = models.CharField(max_length=500)
    creator = models.ForeignKey(get_user_model(), blank=True, on_delete=models.CASCADE)
    experiment = models.ForeignKey(Experiment, blank=True, on_delete=models.CASCADE, null=True)
    experimentalgroup = models.ForeignKey(ExperimentalGroup, blank=True, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return "{0}".format(self.name)



class Sample(models.Model):
    """ Samples are storage containers for representations. A Sample is to be understood analogous to a Biological Sample. It existed in Time (the time of acquisiton and experimental procedure),
    was measured in space (x,y,z) and in different modalities (c). Sample therefore provide a datacontainer where each Representation of
    the data shares the same dimensions. Every transaction to our image data is still part of the original acuqistion, so also filtered images are refering back to the sample
    """
    meta = models.JSONField(null=True, blank=True)
    name = models.CharField(max_length=1000)
    experiments = models.ManyToManyField(Experiment, blank=True, null=True, related_name="samples")
    nodeid = models.CharField(max_length=400, null=True, blank=True)
    experimentalgroup = models.ForeignKey(ExperimentalGroup, on_delete=models.SET_NULL, blank=True, null=True)
    animal = models.ForeignKey(Animal, on_delete=models.SET_NULL, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True)
    tags = TaggableManager()



    def __str__(self):
        return "{0} by User: {1}".format(self.name,self.creator.username)


    def delete(self, *args, **kwargs):
        logger.info("Trying to remove Sample H5File")
        super(Sample, self).delete(*args, **kwargs)


    



class Representation(Matrise):
    ''' A Representation is 5-dimensional representation of a microscopic image @arkitekt/rep'''
    group = "representation"
    meta = models.JSONField(null=True, blank=True)
    origin = models.ForeignKey('self', on_delete=models.SET_NULL, blank=True, null= True, related_name="derived", related_query_name="derived")
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='representations', help_text="The Sample this representation belongs to", null=True, blank=True)
    type = models.CharField(max_length=400, blank=True, null=True, help_text="The Representation can have varying types, consult your API")
    variety = models.CharField(max_length=400, help_text="The Representation can have varying types, consult your API", choices=RepresentationVariety.choices, default=RepresentationVariety.UNKNOWN.value)
    chain = models.CharField(max_length=9000, blank=True, null=True)
    nodeid = models.CharField(max_length=400, null=True, blank=True)
    thumbnail = models.ImageField(upload_to="thumbnails",null=True, storage=PrivateMediaStorage(), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.SET(get_sentinel_user), null=True, blank=True)
    tags = TaggableManager()

    objects = RepresentationManager()

    class Meta:
        permissions = [
            ('download_representation', 'Can download Presentation')
        ]

    def __str__(self):
        return f'Representation of {self.name}'



class RepresentationMetric(models.Model):
    rep = models.ForeignKey(Representation,on_delete=models.CASCADE, related_name='metrics', help_text="The Representatoin this Metric belongs to")
    key = models.CharField(max_length=1000, help_text="The Key")
    value = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True)



class Thumbnail(models.Model):
    representation = models.ForeignKey(Representation, on_delete=models.CASCADE, related_name='thumbnails', help_text="The Sample this representation belongs to")
    image = models.ImageField(upload_to="thumbnails",null=True)



class ROI(models.Model):
    nodeid = models.CharField(max_length=400, null=True, blank=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    vectors = models.CharField(max_length=3000, help_text= "A json dump of the ROI Vectors (specific for each type)")
    color = models.CharField(max_length=100, blank=True, null=True)
    signature = models.CharField(max_length=300,null=True, blank=True)
    created_at = models.DateTimeField(auto_now=True)
    representation = models.ForeignKey(Representation, on_delete=models.CASCADE,blank=True, null=True, related_name="rois")
    experimentalgroup = models.ForeignKey(ExperimentalGroup, on_delete=models.SET_NULL, blank=True, null=True)


    def __str__(self):
        return f"ROI created by {self.creator.username} on {self.representation.name}"




import grunnlag.signals as sig