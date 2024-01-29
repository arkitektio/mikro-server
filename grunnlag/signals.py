from typing import Union
from django.conf import settings
from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch.dispatcher import receiver

from bord.models import Table
from .models import ROI, Experiment, OmeroFile, Representation, Sample, Thumbnail, Stage
import logging

logger = logging.getLogger(__name__)


def publish_rep_changes(
    instance: Representation,
    action: str,
):
    from grunnlag.graphql.subscriptions import MyRepresentations

    publish_groups = []
    has_origin = False
    if instance.origins:
        logger.info("Publishing to Origin Groups")
        for origin in instance.origins.all():
            has_origin = True
            logger.info("Publishing to Origin Group: {}".format(origin.id))
            publish_groups.append(MyRepresentations.ORIGIN_GROUP(origin))

    if instance.creator:
        publish_groups.append(MyRepresentations.USERGROUP(instance.creator))

    if not has_origin:
        logger.info("NOOOOO CHILDREN")
        publish_groups.append(MyRepresentations.USER_NO_CHILDRENGROUP(instance.creator))

    MyRepresentations.broadcast(
        {"action": action, "data": instance.id},
        publish_groups,
    )


@receiver(post_save, sender=Representation)
def rep_post_save(sender, instance=None, created=None, **kwargs):
    """
    Assign Permission to the Representation
    """

    permissions = ["download_representation", "view_representation"]#

    print("REPRESENTATION SAVED")
    if created:
        logger.info(f"Assigning Permissions {permissions} to Representation")

    publish_rep_changes(instance, "created" if created else "updated")


@receiver(post_save, sender=Thumbnail)
def thumb_post_save(sender, instance=None, created=None, **kwargs):
    """
    Assign Permission to the Representation
    """

    from grunnlag.graphql.subscriptions import MyRepresentations

    if instance.representation:
        publish_rep_changes(instance.representation, "updated")


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


@receiver(post_save, sender=Stage)
def stage_post_save(sender, instance=None, created=None, **kwargs):
    pass


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


@receiver(post_delete, sender=Representation)
def rep_pre_delete(sender, instance=None, **kwargs):
    publish_rep_changes(instance, "deleted")


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
def roi_post_save(sender, instance=None, created=None, **kwargs):
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


@receiver(pre_delete, sender=Representation)
def auto_delete_store_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding `Representation` object is deleted.
    """

    if instance.store:
        logger.error("DELETING DATA")
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


@receiver(pre_delete, sender=OmeroFile)
def auto_delete_file_on_delete_d(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding `Representation` object is deleted.
    """
    if instance.file:
        print("Deleting file")
        instance.file.delete()
