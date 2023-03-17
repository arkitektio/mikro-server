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
from lok.models import LokClient
logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, o):
        print(o)
        if isinstance(o, datetime):
            return o.isoformat()

        return json.JSONEncoder.default(self, o)


def get_sentinel_user():
    return get_user_model().objects.get_or_create(username="deleted")[0]


class CreatedThroughMixin(models.Model):
    created_by = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_created_by")
    created_through = models.ForeignKey(
        LokClient, on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_created_through"
    )

    class Meta:
        abstract = True



class CommentableMixin(models.Model):
    comments = GenericRelation(Comment, help_text="Comments on the experiment")

    class Meta:
        abstract = True


class UserMeta(models.Model):
    user = models.OneToOneField(
        get_user_model(), blank=True, on_delete=models.CASCADE, related_name="meta"
    )
    color = ColorField(default="#FF0000")

    def __str__(self) -> str:
        return f"User Meta for {self.user}"


class Antibody(CreatedThroughMixin, CommentableMixin, models.Model):

    name = models.CharField(max_length=100)
    creator = models.ForeignKey(get_user_model(), blank=True, on_delete=models.CASCADE)

    def __str__(self):
        return "{0}".format(self.name)



class Objective(CreatedThroughMixin, CommentableMixin,  models.Model):
    serial_number = models.CharField(max_length=1000, unique=True)
    name = models.CharField(max_length=1000, unique=True)
    magnification = models.FloatField(blank=True, null=True)
    na = models.FloatField(blank=True, null=True)
    immersion = models.CharField(max_length=1000, blank=True, null=True)



class Instrument(CreatedThroughMixin,  CommentableMixin, models.Model):
    name = models.CharField(max_length=1000, unique=True)
    detectors = models.JSONField(null=True, blank=True, default=list)
    dichroics = models.JSONField(null=True, blank=True, default=list)
    filters = models.JSONField(null=True, blank=True, default=list)
    objectives = models.ManyToManyField(Objective, blank=True, related_name="instruments")
    lot_number = models.CharField(max_length=1000, null=True, blank=True)
    manufacturer = models.CharField(max_length=1000, null=True, blank=True)
    model = models.CharField(max_length=1000, null=True, blank=True)
    serial_number = models.CharField(max_length=1000, null=True, blank=True)


class OmeroFileField(models.FileField):
    pass


class ModelDataField(models.FileField):
    pass





class Dataset(CreatedThroughMixin,  CommentableMixin, models.Model):
    """
    A dataset is a collection of data files and metadata files.
    It mimics the concept of a folder in a file system and is the top level
    object in the data model.

    """
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the experiment was created"
    )
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    name = models.CharField(max_length=200, help_text="The name of the experiment")
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_datasets",
        blank=True,
        help_text="The users that have pinned the experiment",
    )
    tags = TaggableManager(help_text="Tags for the experiment")


class InDatasetMixin(models.Model):
    datasets = models.ManyToManyField(Dataset, related_name="%(class)ss",null=True, blank=True)

    class Meta:
        abstract = True


class Experiment(CreatedThroughMixin,  CommentableMixin, InDatasetMixin, models.Model):
    """
    An experiment is a collection of samples and their representations.
    It mimics the concept of an experiment in the lab and is the top level
    object in the data model.

    You can use the experiment to group samples and representations likewise
    to how you would group files into folders in a file system.
    """

    name = models.CharField(max_length=200, help_text="The name of the experiment")
    description = models.CharField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="A short description of the experiment",
    )
    description_long = models.TextField(
        null=True, blank=True, help_text="A long description of the experiment"
    )
    linked_paper = models.URLField(
        null=True, blank=True, help_text="A link to a paper describing the experiment"
    )
    image = models.ImageField(
        upload_to="experiment_banner",
        null=True,
        blank=True,
        help_text="An image to be used as a banner for the experiment",
    )

    comments = GenericRelation(Comment, help_text="Comments on the experiment")
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the experiment was created"
    )
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created the experiment",
    )
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_experiments",
        blank=True,
        help_text="The users that have pinned the experiment",
    )
    tags = TaggableManager(help_text="Tags for the experiment")



class Context(CreatedThroughMixin, CommentableMixin, InDatasetMixin, models.Model):
    name = models.CharField(max_length=1000, help_text="The name of the context")
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the context was created"
    )
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="contexts", null=True, blank=True)
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created the context",
    )
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_contexts",
        blank=True,
        help_text="The users that have pinned the context",
    )
    tags = TaggableManager(help_text="Tags for the context")

    def __str__(self) -> str:
        return self.name
    

class Relation(models.Model):
    name = models.CharField(max_length=1000, help_text="The name of the relation", unique=True)
    description = models.CharField(max_length=1000, help_text="The description of the relation", null=True, blank=True)



class DataLink(models.Model):
    x_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="x_content_type")
    x_id = models.PositiveIntegerField()
    y_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="y_content_type")
    y_id = models.PositiveIntegerField()
    x = GenericForeignKey(ct_field="x_content_type", fk_field="x_id")
    y = GenericForeignKey(ct_field="y_content_type", fk_field="y_id")
    relation = models.ForeignKey(Relation, on_delete=models.CASCADE, help_text="The relation between the two objects")
    left_type = models.CharField(max_length=1000, help_text="The type of the left object")
    right_type = models.CharField(max_length=1000, help_text="The type of the right object")
    context = models.ForeignKey(Context, on_delete=models.CASCADE, related_name="links", null=True, blank=True)
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the sample was created"
    )
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created the sample",
    )

    class Meta:
        permissions = [("can_link", "Can link objects")]
        constraints = [
            models.UniqueConstraint(
                fields=("x_content_type", "x_id", "y_content_type", "y_id", "relation", "context"),
                name="Only one relationship of the same kind between two objects in the same context",
            )
        ]

class ExperimentalGroup(CreatedThroughMixin, CommentableMixin, models.Model):
    """A group of samples that are part of the same experimental group"""

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


class Animal(CreatedThroughMixin, CommentableMixin, models.Model):
    """An animal is a living organism that is part of an experiment"""

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

    comments = GenericRelation(Comment, help_text="Comments on the experiment")


class OmeroFile(CreatedThroughMixin, CommentableMixin, InDatasetMixin, models.Model):
    """An OmeroFile is a file that contains omero-meta data. It is the raw file that was generated
    by the microscope.

    Mikro uses the omero-meta datas to create representations of the file. See Representation for more information."""

    type = models.CharField(
        max_length=400,
        choices=OmeroFileType.choices,
        default=OmeroFileType.UNKNWON,
        help_text="The type of the file",
    )
    experiments = models.ManyToManyField(
        Experiment,
        help_text="The experiment this file belongs to",
        null=True,
        blank=True,
        related_name="omero_files",
    )
    file = OmeroFileField(
        upload_to="files",
        null=True,
        storage=PrivateMediaStorage(),
        blank=True,
        help_text="The file",
    )
    name = models.CharField(max_length=400, help_text="The name of the file")
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the file was created"
    )
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created/uploaded the file",
    )
    tags = TaggableManager(help_text="Tags for the file")

    comments = GenericRelation(Comment, help_text="Comments on the experiment")


class Model(CreatedThroughMixin, CommentableMixin, InDatasetMixin, models.Model):
    """A

    Mikro uses the omero-meta data to create representations of the file. See Representation for more information."""

    kind = models.CharField(
        max_length=400,
        help_text="The kind of the model (e.g. Pytorch, Tensorflow, etc.)",
    )
    experiments = models.ManyToManyField(
        Experiment,
        help_text="The experiment this model belongs to",
        null=True,
        blank=True,
        related_name="models",
    )
    data = ModelDataField(
        upload_to="models",
        null=True,
        storage=PrivateMediaStorage(),
        blank=True,
        help_text="The model",
    )
    name = models.CharField(max_length=400, help_text="The name of the model")
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the file was created"
    )
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created/uploaded the file",
    )
    contexts = models.ManyToManyField(
        Context,
        help_text="The contexts this model is valid for",
        null=True,
        blank=True,
        related_name="models",
    )



class Sample(CreatedThroughMixin, CommentableMixin, InDatasetMixin, models.Model):
    """Samples are storage containers for representations. A Sample is to be understood analogous to a Biological Sample. It existed in Time (the time of acquisiton and experimental procedure), was measured in space (x,y,z) and in different modalities (c). Sample therefore provide a datacontainer where each Representation of the data shares the same dimensions. Every transaction to our image data is still part of the original acuqistion, so also filtered images are refering back to the sample"""

    meta = models.JSONField(null=True, blank=True)
    name = models.CharField(max_length=1000, help_text="The name of the sample")
    experiments = models.ManyToManyField(
        Experiment,
        blank=True,
        null=True,
        related_name="samples",
        help_text="The experiments this sample belongs to",
    )
    experimentalgroup = models.ForeignKey(
        ExperimentalGroup,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="The experimental group this sample belongs to",
    )
    animal = models.ForeignKey(
        Animal,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="The animal this sample belongs to",
    )

    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the sample was created"
    )
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created the sample",
    )
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_samples",
        blank=True,
        help_text="The users that have pinned the sample",
    )
    tags = TaggableManager()

    def delete(self, *args, **kwargs):
        logger.info("Trying to remove Sample H5File")
        super(Sample, self).delete(*args, **kwargs)

# TODO: Rename Stage
class Stage(CreatedThroughMixin, CommentableMixin, InDatasetMixin, models.Model):
    """An Stage is a set of positions that share a common space on a microscope and can
    be use to translate.
    
    
    """
    name = models.CharField(max_length=1000, help_text="The name of the stage")
    kind = models.CharField(max_length=1000)
    instrument = models.ForeignKey(Instrument, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="The time the acquistion was created"
    )
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created the stage",
    )
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_acquistions",
        blank=True,
        help_text="The users that have pinned the stage",
    )
    tags = TaggableManager()


class Position(CreatedThroughMixin, CommentableMixin, models.Model):
    """The relative position of a sample on a microscope stage"""

    #Should be stage
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="positions")
    name = models.CharField(max_length=1000, help_text="The name of the possition")
    x = models.FloatField(null=True, blank=True)
    y = models.FloatField(null=True, blank=True)
    z = models.FloatField(null=True, blank=True)
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_positions",
        blank=True,
        help_text="The users that have pinned the position",
    )
    tags = TaggableManager()

    class Meta:
        permissions = [("download_representation", "Can download Presentation")]
        constraints = [
            models.UniqueConstraint(
                fields=["stage", "x","y","z"],
                name="Only one unique posistion per stage",
            )
        ]




class Representation(CreatedThroughMixin,  InDatasetMixin, CommentableMixin,Matrise):
    """A Representation is 5-dimensional representation of an image

    Mikro stores each image as sa 5-dimensional representation. The dimensions are:
    - t: time
    - c: channel
    - z: z-stack
    - x: x-dimension
    - y: y-dimension

    This ensures a unified api for all images, regardless of their original dimensions. Another main
    determining factor for a representation is its variety:
    A representation can be a raw image representating voxels (VOXEL)
    or a segmentation mask representing instances of a class. (MASK)
    It can also representate a human perception of the image (RGB) or a human perception of the mask (RGBMASK)

    # Meta

    Meta information is stored in the omero field which gives access to the omero-meta data. Refer to the omero documentation for more information.


    #Origins and Derivations

    Images can be filtered, which means that a new representation is created from the other (original) representations. This new representation is then linked to the original representations. This way, we can always trace back to the original representation.
    Both are encapsulaed in the origins and derived fields.

    Representations belong to *one* sample. Every transaction to our image data is still part of the original acuqistion, so also filtered images are refering back to the sample
    Each iamge has also a name, which is used to identify the image. The name is unique within a sample.
    File and Rois that are used to create images are saved in the file origins and roi origins repectively.


    """

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
    experiments = models.ManyToManyField(
        Experiment,
        blank=True,
        null=True,
        related_name="experiments",
        help_text="The experiments this image belongs to",
    )
    description = models.CharField(max_length=1000, null=True, blank=True)
    variety = models.CharField(
        max_length=400,
        help_text="The Representation can have vasrying types, consult your API",
        choices=RepresentationVariety.choices,
        default=RepresentationVariety.UNKNOWN.value,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.SET(get_sentinel_user), null=True, blank=True
    )
    comments = GenericRelation(Comment, help_text="Comments on the representation")
    x = GenericRelation(DataLink, help_text="Comments on the representation", content_type_field="x_content_type", object_id_field="x_id")
    y = GenericRelation(DataLink, help_text="Comments on the representation", content_type_field="y_content_type", object_id_field="y_id")
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_representations",
        help_text="The users that have pinned the representation",
    )

    tags = TaggableManager()

    objects = RepresentationManager()

    class Meta:
        permissions = [("download_representation", "Can download Presentation")]

    def __str__(self):
        return f"Representation: {self.name}"




# TODO: Rename Context
class Omero(CreatedThroughMixin,  CommentableMixin,models.Model):


    """Omero is a through model that stores the real world context of an image

    This means that it stores the position (corresponding to the relative displacement to
    a stage (Both are models)), objective and other meta data of the image.

    """
    representation = models.OneToOneField(
        Representation, on_delete=models.CASCADE, related_name="omero"
    )

    position = models.ForeignKey(Position, on_delete=models.SET_NULL, null=True, related_name="omeros")
    objective = models.ForeignKey(Objective, on_delete=models.SET_NULL, null=True, related_name="omeros")
    affine_transformation = models.JSONField(null=True, blank=True, default=list)
    planes = models.JSONField(null=True, blank=True, default=list)
    channels = models.JSONField(null=True, blank=True, default=list)
    scale = models.JSONField(null=True, blank=True, default=list)
    physical_size = models.JSONField(null=True, blank=True, default=list)
    acquisition_date = models.DateTimeField(null=True, blank=True)
    objective_settings = models.JSONField(null=True, blank=True, default=dict)
    imaging_environment = models.JSONField(null=True, blank=True, default=dict)
    instrument = models.ForeignKey(
        Instrument, null=True, blank=True, on_delete=models.SET_NULL, related_name="omeros"
    )


class Metric(CreatedThroughMixin, CommentableMixin, models.Model):
    """A Metric is a single (scalar) value that is associated with a representation, sample or experiment.

    It can be used to store any kind of value that is associated with a sample or experiment, representation.
    For example, the number of cells in a sample or the average intensity of a channel in a representation.


    """

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
        help_text="The Sample this Metric belongs to",
    )
    key = models.CharField(max_length=1000, help_text="The Key")
    value = models.JSONField(null=True, blank=True, help_text="The value")

    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, null=True, blank=True
    )

    class Meta:
        permissions = [("download_representation", "Can download Presentation")]
        constraints = [
            models.UniqueConstraint(
                fields=["key", "sample"],
                name="Only one unique key per sample",
            )
        ]




class Thumbnail(CreatedThroughMixin,  CommentableMixin,models.Model):
    """A Thumbnail is a render of a representation that is used to display the representation in the UI.

    Thumbnails can also store the major color of the representation. This is used to color the representation in the UI.
    """

    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        related_name="thumbnails",
        help_text="The Sample this representation belongs to",
    )
    blurhash = models.CharField(max_length=1000, null=True, blank=True)
    image = models.ImageField(
        upload_to="thumbnails", null=True, storage=PrivateMediaStorage()
    )
    major_color = models.CharField(max_length=100, null=True, blank=True)


class ROI(CreatedThroughMixin,  CommentableMixin,models.Model):
    """A ROI is a region of interest in a representation.

    This region is to be regarded as a view on the representation. Depending
    on the implementatoin (type) of the ROI, the view can be constructed
    differently. For example, a rectangular ROI can be constructed by cropping
    the representation according to its 2 vectors. while a polygonal ROI can be constructed by masking the
    representation with the polygon.

    The ROI can also store a name and a description. This is used to display the ROI in the UI.

    """

    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        help_text="The user that created the ROI",
    )
    vectors = models.JSONField(
        max_length=3000,
        help_text="A list of the ROI Vectors (specific for each type)",
        default=list,
    )
    type = models.CharField(
        max_length=400,
        help_text="The Roi can have varying types, consult your API",
        choices=RoiType.choices,
        default=RoiType.UNKNOWN.value,
    )
    color = models.CharField(
        max_length=100, blank=True, null=True, help_text="The color of the ROI (for UI)"
    )
    created_at = models.DateTimeField(
        auto_now=True, help_text="The time the ROI was created"
    )
    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="rois",
        help_text="The Representation this ROI was original used to create (drawn on)",
    )
    x = GenericRelation(DataLink, help_text="Comments on the representation", content_type_field="x_content_type", object_id_field="x_id")
    y = GenericRelation(DataLink, help_text="Comments on the representation", content_type_field="y_content_type", object_id_field="y_id")
    
    label = models.CharField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="The label of the ROI (for UI)",
    )
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_rois",
        blank=True,
        help_text="The users that pinned this ROI",
    )
    experimentalgroup = models.ForeignKey(
        ExperimentalGroup,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="The ExperimentalGroup this ROI belongs to",
    )
    tags = TaggableManager()

    def __str__(self):
        return f"ROI creatsed by {self.creator.username} on {self.representation.name}"


class Label(CreatedThroughMixin, CommentableMixin, models.Model):
    """A Label is a trough model for image and features.

    Its map an instance value of a representation
    (e.g. a pixel value of a segmentation mask) to a set of corresponding features of the segmented
    class instance.

    There can only be one label per representation and class instance. You can then attach
    features to the label.


    """

    instance = models.BigIntegerField(
        help_text="The instance value of the representation (pixel value). Must be a value of the image array"
    )
    name = models.CharField(
        max_length=600, null=True, blank=True, help_text="The name of the instance"
    )
    created_at = models.DateTimeField(
        auto_now=True, help_text="The time the Label was created"
    )
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        help_text="The user that created the Label",
    )
    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="labels",
        help_text="The Representation this Label instance belongs to",
    )
    experimentalgroup = models.ForeignKey(
        ExperimentalGroup,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="The Experimental group this Label belongs to",
    )
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_labels",
        help_text="The users that pinned this Label",
    )
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


class Feature(CreatedThroughMixin,  CommentableMixin,models.Model):
    """A Feature is a numerical key value pair that is attached to a Label.

    You can model it for example as a key value pair of a class instance of a segmentation mask.
    Representation -> Label0 -> Feature0
                             -> Feature1
                   -> Label1 -> Feature0

    Features can be used to store any numerical value that is attached to a class instance.
    THere can only ever be one key per label. If you want to store multiple values for a key, you can
    store them as a list in the value field.

    Feature are analogous to metrics on a representation, but for a specific class instance (Label)

    """

    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The user that created the Feature",
    )
    label = models.ForeignKey(
        Label,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="features",
        help_text="The Label this Feature belongs to",
    )
    key = models.CharField(max_length=1000, help_text="The key of the feature")
    value = models.JSONField(
        null=True, blank=True, help_text="The value of the feature"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "label"],
                name="Only one unique key per label",
            )
        ]


import grunnlag.signals as sig
