from django.conf import settings
from django.db.models.signals import post_save, post_delete
from django.dispatch.dispatcher import receiver

from bord.models import Table
from .models import ROI, Experiment, OmeroFile, Representation, Sample, Thumbnail
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Representation)
def rep_post_save(sender, instance=None, created=None, **kwargs):
    """
    Assign Permission to the Representation
    """

    permissions = ["download_representation", "view_representation"]
    if created:
        logger.info(f"Assigning Permissions {permissions} to Representation")

    from grunnlag.graphql.subscriptions import MyRepresentations

    if instance.creator:
        MyRepresentations.broadcast(
            {"action": "created", "data": instance.id}
            if created
            else {"action": "updated", "data": instance.id},
            [MyRepresentations.USERGROUP(instance.creator)],
        )


@receiver(post_save, sender=Thumbnail)
def rep_post_save(sender, instance=None, created=None, **kwargs):
    """
    Assign Permission to the Representation
    """

    from grunnlag.graphql.subscriptions import MyRepresentations

    if instance.representation:
        if instance.representation.creator:
            MyRepresentations.broadcast(
                {"action": "updated", "data": instance.representation.id},
                [MyRepresentations.USERGROUP(instance.representation.creator)],
            )


@receiver(post_save, sender=Experiment)
def exp_post_save(sender, instance=None, created=None, **kwargs):
    from grunnlag.graphql.subscriptions import MyExperiments

    if instance.creator:
        MyExperiments.broadcast(
            {"action": "created", "data": instance.id}
            if created
            else {"action": "updated", "data": instance.id},
            [MyExperiments.USERGROUP(instance.creator)],
        )


@receiver(post_save, sender=Sample)
def samp_post_save(sender, instance=None, created=None, **kwargs):
    from grunnlag.graphql.subscriptions import MySamples

    if instance.creator:
        MySamples.broadcast(
            {"action": "created", "data": instance.id}
            if created
            else {"action": "updated", "data": instance.id},
            [MySamples.USERGROUP(instance.creator)],
        )


@receiver(post_delete, sender=Sample)
def samp_post_del(sender, instance=None, **kwargs):
    from grunnlag.graphql.subscriptions import MySamples

    if instance.creator:
        MySamples.broadcast(
            {"action": "deleted", "data": instance.id},
            [MySamples.USERGROUP(instance.creator)],
        )


@receiver(post_delete, sender=Experiment)
def exp_post_del(sender, instance=None, **kwargs):
    from grunnlag.graphql.subscriptions import MyExperiments

    if instance.creator:
        MyExperiments.broadcast(
            {"action": "deleted", "data": instance.id},
            [MyExperiments.USERGROUP(instance.creator)],
        )


@receiver(post_save, sender=Table)
def table_post_save(sender, instance=None, created=None, **kwargs):
    from grunnlag.graphql.subscriptions import MyTables

    if instance.creator:
        MyTables.broadcast(
            {"action": "created", "data": instance.id}
            if created
            else {"action": "updated", "data": instance.id},
            [MyTables.USERGROUP(instance.creator)],
        )


@receiver(post_save, sender=ROI)
def rep_post_save(sender, instance=None, created=None, **kwargs):
    from grunnlag.graphql.subscriptions import Rois

    if instance.representation:
        Rois.broadcast(
            {"action": "created", "data": instance}
            if created
            else {"action": "updated", "data": instance},
            [Rois.ROI_FOR_REP(instance.representation)],
        )


@receiver(post_delete, sender=ROI)
def roi_post_del(sender, instance=None, **kwargs):
    from grunnlag.graphql.subscriptions import Rois

    try:
        if instance.representation:
            Rois.broadcast(
                {"action": "deleted", "data": instance.id},
                [Rois.ROI_FOR_REP(instance.representation)],
            )
    except Exception as e:
        # should raise if representaiton was deleted as well
        logger.error(e)


@receiver(post_delete, sender=Table)
def table_post_del(sender, instance=None, **kwargs):
    from grunnlag.graphql.subscriptions import MyTables

    if instance.creator:
        MyTables.broadcast(
            {"action": "deleted", "data": instance.id},
            [MyTables.USERGROUP(instance.creator)],
        )


@receiver(post_delete, sender=Representation)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding `Representation` object is deleted.
    """
    if instance.store:
        try:
            instance.store.delete()
        except Exception as e:
            logger.exception(e)

    from grunnlag.graphql.subscriptions import MyRepresentations

    if instance.creator:
        MyRepresentations.broadcast(
            {"action": "deleted", "data": instance.id},
            [MyRepresentations.USERGROUP(instance.creator)],
        )


@receiver(post_delete, sender=OmeroFile)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding `Representation` object is deleted.
    """
    if instance.file:
        instance.file.delete()
