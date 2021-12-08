from django.conf import settings
from django.db.models.signals import post_save, post_delete
from django.dispatch.dispatcher import receiver
from .models import Table
import logging

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Table)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding `Representation` object is deleted.
    """
    if instance.store:
        instance.store.delete()
