from posix import listdir
from django.core.management import BaseCommand
import asyncio
import logging 
from grunnlag.models import Experiment, Representation, Sample, Thumbnail
from grunnlag.enums import RepresentationVariety
from grunnlag.utils import array_to_image
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile
from django.conf import settings
from os.path import join, isfile
from os import listdir
import tifffile
import xarray as xr

logger = logging.getLogger(__name__)




class Command(BaseCommand):
    help = "Starts the provider"
    leave_locale_alone = True

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)


    def handle(self, *args, **options):
        exp, created = Experiment.objects.update_or_create(name="Demo Experiment", defaults={"description":"A brief examplary experiment containing two samples representing image stacks of brainorganoids"})

        if created:
            demodir = join(settings.BASE_DIR,"demo")

            onlyfiles = [f for f in listdir(demodir) if isfile(join(demodir, f)) ]

            for i, file_name in enumerate(onlyfiles):
                file_path = join(demodir, file_name) 
                sample = Sample.objects.create(name=f"Demo Sample {i}")
                sample.experiments.add(exp)
                sample.save()
                image = tifffile.imread(file_path)
                image = image.reshape((1,1) + image.shape)
                array = xr.DataArray(image, dims=list("ctzyx"))
                rep = Representation.objects.from_xarray(array, sample=sample, name="initial", variety=RepresentationVariety.VOXEL)

       

        # and runs callbacks whenever necessary.