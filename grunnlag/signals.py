from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch.dispatcher import receiver
from guardian.shortcuts import assign_perm
from .models import Representation
from bergen.models import Node
import logging

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Representation)
def rep_post_save(sender, instance=None, created=None, **kwargs):
    """
    Assign Permission to the Representation
    """

    permissions = ["download_representation","view_representation"]
    if created:
        logger.info(f"Assigning Permissions {permissions} to Representation")
        for permission in permissions:
            assign_perm(permission, instance.sample.creator, instance)

    
    #from grunnlag.subscriptions import MyNewestRep
    #MyNewestRep.broadcast(instance, groups=[MyNewestRep.USERGROUP(instance.sample.creator)])
