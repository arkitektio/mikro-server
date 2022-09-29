from abc import abstractclassmethod
from datetime import datetime
from email.policy import default
import json
from grunnlag.storage import PrivateMediaStorage
from grunnlag.managers import RepresentationManager
from django.db import models
from django.contrib.contenttypes.fields import GenericRelation

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

# Create your models here.
from .enums import OmeroFileType, RepresentationVariety, RoiType
import logging

from django.contrib.auth import get_user_model
from django.db import models
from lok.models import LokUser
from taggit.managers import TaggableManager
from matrise.models import Matrise
from colorfield.fields import ColorField
from komment.models import Comment

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, o):
        print(o)
        if isinstance(o, datetime):
            return o.isoformat()

        return json.JSONEncoder.default(self, o)


def get_sentinel_user():
    return get_user_model().objects.get_or_create(username="deleted")[0]


class UserMeta(models.Model):
    user = models.OneToOneField(
        get_user_model(), blank=True, on_delete=models.CASCADE, related_name="meta"
    )
    color = ColorField(default="#FF0000")

    def __str__(self) -> str:
        return f"User Meta for {self.user}"


class Antibody(models.Model):

    name = models.CharField(max_length=100)
    creator = models.ForeignKey(get_user_model(), blank=True, on_delete=models.CASCADE)

    def __str__(self):
        return "{0}".format(self.name)


class OmeroFileField(models.FileField):
    pass


class Experiment(models.Model):
    meta = models.JSONField(null=True, blank=True)
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=1000, null=True, blank=True)
    description_long = models.TextField(null=True, blank=True)
    linked_paper = models.URLField(null=True, blank=True)
    image = models.ImageField(upload_to="experiment_banner", null=True, blank=True)

    comments = GenericRelation(Comment)
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, null=True, blank=True
    )
    pinned_by = models.ManyToManyField(
        get_user_model(), related_name="pinned_experiments"
    )
    tags = TaggableManager()


class ExperimentalGroup(models.Model):
    name = models.CharField(max_length=200, help_text="The experimental groups name")
    description = models.CharField(
        max_length=1000, help_text="A brief summary of applied techniques in this group"
    )
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        help_text="The experiment this Group belongs too",
    )
    iscontrol = models.BooleanField(
        help_text="Is this Experimental Group a ControlGroup?"
    )


class Animal(models.Model):
    name = models.CharField(max_length=100)
    age = models.CharField(max_length=400)
    type = models.CharField(max_length=500)
    creator = models.ForeignKey(get_user_model(), blank=True, on_delete=models.CASCADE)
    experiment = models.ForeignKey(
        Experiment, blank=True, on_delete=models.CASCADE, null=True
    )
    experimentalgroup = models.ForeignKey(
        ExperimentalGroup, blank=True, on_delete=models.CASCADE, null=True
    )


class OmeroFile(models.Model):
    type = models.CharField(
        max_length=400, choices=OmeroFileType.choices, default=OmeroFileType.UNKNWON
    )
    file = OmeroFileField(
        upload_to="files", null=True, storage=PrivateMediaStorage(), blank=True
    )
    name = models.CharField(max_length=400)
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, null=True, blank=True
    )
    tags = TaggableManager()


class Sample(models.Model):
    """Samples are storage containers for representations. A Sample is to be understood analogous to a Biological Sample. It existed in Time (the time of acquisiton and experimental procedure), was measured in space (x,y,z) and in different modalities (c). Sample therefore provide a datacontainer where each Representation of the data shares the same dimensions. Every transaction to our image data is still part of the original acuqistion, so also filtered images are refering back to the sample"""

    meta = models.JSONField(null=True, blank=True)
    name = models.CharField(max_length=1000)
    experiments = models.ManyToManyField(
        Experiment, blank=True, null=True, related_name="samples"
    )
    nodeid = models.CharField(max_length=400, null=True, blank=True)
    experimentalgroup = models.ForeignKey(
        ExperimentalGroup, on_delete=models.SET_NULL, blank=True, null=True
    )
    animal = models.ForeignKey(Animal, on_delete=models.SET_NULL, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, null=True, blank=True
    )
    pinned_by = models.ManyToManyField(get_user_model(), related_name="pinned_samples")
    tags = TaggableManager()

    def delete(self, *args, **kwargs):
        logger.info("Trying to remove Sample H5File")
        super(Sample, self).delete(*args, **kwargs)


class Representation(Matrise):
    """A Representation is 5-dimensional representation of a microscopic image @arkitekt/rep"""

    group = "representation"  #
    meta = models.JSONField(null=True, blank=True)
    origins = models.ManyToManyField(
        "self",
        blank=True,
        null=True,
        related_name="derived",
        related_query_name="derived",
        symmetrical=False,
    )
    file_origins = models.ManyToManyField(
        OmeroFile,
        blank=True,
        null=True,
        related_name="derived_representations",
        related_query_name="derived_representations",
        symmetrical=False,
    )
    roi_origins = models.ManyToManyField(
        "ROI",
        blank=True,
        null=True,
        related_name="derived_representations",
        related_query_name="derived_representations",
        symmetrical=False,
    )
    sample = models.ForeignKey(
        Sample,
        on_delete=models.CASCADE,
        related_name="representations",
        help_text="The Sample this representation belosngs to",
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=1000, null=True, blank=True)
    type = models.CharField(
        max_length=400,
        blank=True,
        null=True,
        help_text="The Representation can have varying types, consult your API",
    )
    variety = models.CharField(
        max_length=400,
        help_text="The Representation can have vasrying types, consult your API",
        choices=RepresentationVariety.choices,
        default=RepresentationVariety.UNKNOWN.value,
    )
    chain = models.CharField(max_length=9000, blank=True, null=True)
    nodeid = models.CharField(max_length=400, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.SET(get_sentinel_user), null=True, blank=True
    )
    comments = GenericRelation(Comment)
    pinned_by = models.ManyToManyField(
        get_user_model(), related_name="pinned_representations"
    )

    tags = TaggableManager()

    objects = RepresentationManager()

    class Meta:
        permissions = [("download_representation", "Can download Presentation")]

    def __str__(self):
        return f"Representation osf {self.name}"


class Instrument(models.Model):
    name = models.CharField(max_length=1000, unique=True)
    detectors = models.JSONField(null=True, blank=True, default=list)
    dichroics = models.JSONField(null=True, blank=True, default=list)
    filters = models.JSONField(null=True, blank=True, default=list)
    lot_number = models.CharField(max_length=1000, null=True, blank=True)
    manufacturer = models.CharField(max_length=1000, null=True, blank=True)
    model = models.CharField(max_length=1000, null=True, blank=True)
    serial_number = models.CharField(max_length=1000, null=True, blank=True)


class Omero(models.Model):
    representation = models.OneToOneField(
        Representation, on_delete=models.CASCADE, related_name="omero"
    )
    planes = models.JSONField(null=True, blank=True, default=list)
    channels = models.JSONField(null=True, blank=True, default=list)
    scale = models.JSONField(null=True, blank=True, default=list)
    physical_size = models.JSONField(null=True, blank=True, default=list)
    acquisition_date = models.DateTimeField(null=True, blank=True)
    objective_settings = models.JSONField(null=True, blank=True, default=dict)
    imaging_environment = models.JSONField(null=True, blank=True, default=dict)
    instrument = models.ForeignKey(
        Instrument, null=True, blank=True, on_delete=models.SET_NULL
    )


class Metric(models.Model):
    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="metrics",
        help_text="The Representatoin this Metric belongs to",
    )
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="metrics",
        help_text="The Representatoin this Metric belongs to",
    )
    sample = models.ForeignKey(
        Sample,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="metrics",
        help_text="The Representatoin this Metric belongs to",
    )
    key = models.CharField(max_length=1000, help_text="The Key")
    value = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, null=True, blank=True
    )


class Thumbnail(models.Model):
    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        related_name="thumbnails",
        help_text="The Sample this representation belongs to",
    )
    image = models.ImageField(
        upload_to="thumbnails", null=True, storage=PrivateMediaStorage()
    )
    major_color = models.CharField(max_length=100, null=True, blank=True)


class ROI(models.Model):
    nodeid = models.CharField(max_length=400, null=True, blank=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    vectors = models.JSONField(
        max_length=3000,
        help_text="A json dump of the ROI Vectors (specific for each type)",
        default=list,
    )
    type = models.CharField(
        max_length=400,
        help_text="The Representation can have varying types, consult your API",
        choices=RoiType.choices,
        default=RoiType.UNKNOWN.value,
    )
    color = models.CharField(max_length=100, blank=True, null=True)
    signature = models.CharField(max_length=300, null=True, blank=True)
    created_at = models.DateTimeField(auto_now=True)
    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="rois",
    )
    label = models.CharField(max_length=1000, null=True, blank=True)
    pinned_by = models.ManyToManyField(get_user_model(), related_name="pinned_rois")
    experimentalgroup = models.ForeignKey(
        ExperimentalGroup, on_delete=models.SET_NULL, blank=True, null=True
    )
    tags = TaggableManager()

    def __str__(self):
        return f"ROI creatsed by {self.creator.username} on {self.representation.name}"


class Label(models.Model):
    instance = models.BigIntegerField()
    name = models.CharField(max_length=600, null=True, blank=True)
    created_at = models.DateTimeField(auto_now=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="labels",
    )
    experimentalgroup = models.ForeignKey(
        ExperimentalGroup, on_delete=models.SET_NULL, blank=True, null=True
    )
    pinned_by = models.ManyToManyField(get_user_model(), related_name="pinned_labels")
    tags = TaggableManager()

    def __str__(self):
        return f"ROI created by {self.creator.username} on {self.representation.name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instance", "representation"],
                name="Only one unique label per images",
            )
        ]


class Feature(models.Model):
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True)
    label = models.ForeignKey(
        Label,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="features",
    )
    key = models.CharField(max_length=1000, help_text="The sKesyss")
    value = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "label"],
                name="Only one unique key per label",
            )
        ]


import grunnlag.signals as sig
