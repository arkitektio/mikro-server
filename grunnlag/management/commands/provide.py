from bergen.schema import NodeType, Template
from bergen.types.model import ArnheimModel
from django.core.management import BaseCommand
from bergen.messages.postman.provide import *
from bergen.clients.provider import ProviderBergen
import asyncio
import logging 
from bergen.messages import *
from bergen.provider.base import BaseProvider
from grunnlag.models import Experiment, Representation, Sample, Thumbnail
from bergen import use
from grunnlag.utils import array_to_image
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile


logger = logging.getLogger(__name__)





async def main():
    # Perform connection
    async with ProviderBergen(
        force_new_token=True,
        auto_reconnect=True# if we want to specifically only use pods on this innstance we would use that it in the selector
        ) as client:

        @client.provider.template(use(package="Elements",interface="generate_thumbnail"), bypass_shrink=True, bypass_expand=True)
        def generate_thumbnail(rep):
            rep = Representation.objects.get(id=rep)

            image = array_to_image(rep.array)
            
            buffer = BytesIO()
            image.save(fp=buffer, format='JPEG')
            file = ContentFile(buffer.getvalue())
            rep.thumbnail.save("thumbnail.jpg", InMemoryUploadedFile(file,
                None, "image.jpg", "image/jpeg", file.tell, None
            ) )

            return [rep.id]
        




        await client.provide_async()


class Command(BaseCommand):
    help = "Starts the provider"
    leave_locale_alone = True

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)


    def handle(self, *args, **options):

        # Get the backend to use
        
        loop = asyncio.get_event_loop()
        loop.create_task(main())

        # we enter a never-ending loop that waits for data
        # and runs callbacks whenever necessary.
        print(" [x] Awaiting Providing requests")
        loop.run_forever()