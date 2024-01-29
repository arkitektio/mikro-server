from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch.dispatcher import receiver

from .models import Comment
import logging


@receiver(post_save, sender=Comment)
def comment_post_save(sender, instance=None, created=None, **kwargs):
    from komment.graphql.subscriptions import MyMentionsSubscription

    if instance.mentions and not created:
        # Mentions are added on save (as it is a m2m field)
        for mention in instance.mentions.all():
            MyMentionsSubscription.broadcast(
                {"action": "created", "data": instance.id}
                if created
                else {"action": "updated", "data": instance.id},
                [MyMentionsSubscription.USERGROUP(mention)],
            )
