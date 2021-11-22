from django.core.management import BaseCommand
import asyncio
import logging 
from grunnlag.models import Experiment, Representation, Sample, Thumbnail
from grunnlag.utils import array_to_image
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile


logger = logging.getLogger(__name__)




class Command(BaseCommand):
    help = "Starts the provider"
    leave_locale_alone = True

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)


    def handle(self, *args, **options):

        # Get the backend to use
        main()

        # we enter a never-ending loop that waits for data
        # and runs callbacks whenever necessary.