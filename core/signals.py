import logging

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from core import channels, models

logger = logging.getLogger(__name__)


def _file_rooms(instance: models.File) -> list[str]:
    return [
        channels.org_files_room(instance.organization_id),
        channels.folder_files_room(instance.folder_id),
    ]


@receiver(post_save, sender=models.File)
def my_file_handler(sender, instance=None, created=None, **kwargs):
    if created:
        channels.file_channel.broadcast(channels.FileSignal(create=instance.id), _file_rooms(instance))
    else:
        channels.file_channel.broadcast(channels.FileSignal(update=instance.id), _file_rooms(instance))


@receiver(pre_delete, sender=models.File)
def my_file_delete_handler(sender, instance=None, **kwargs):
    channels.file_channel.broadcast(channels.FileSignal(delete=instance.id), _file_rooms(instance))
