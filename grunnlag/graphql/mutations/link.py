from typing import Dict, Tuple
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from lok import bounced
from grunnlag.linke import LinkableModels, linkable_models
import logging
import datetime
from django.contrib.contenttypes.models import ContentType


class Link(BalderMutation):
    """Create an Comment 
    
    This mutation creates a comment. It takes a commentable_id and a commentable_type.
    If this is the first comment on the commentable, it will create a new comment thread.
    If there is already a comment thread, it will add the comment to the thread (by setting
    it's parent to the last parent comment in the thread).

    CreateComment takes a list of Descendents, which are the comment tree. The Descendents
    are a recursive structure, where each Descendent can have a list of Descendents as children.
    The Descendents are either a Leaf, which is a text node, or a MentionDescendent, which is a
    reference to another user on the platform.

    Please convert your comment tree to a list of Descendents before sending it to the server.
    TODO: Add a converter from a comment tree to a list of Descendents.

    
    (only signed in users)"""

    class Arguments:
        left_type = graphene.Argument(LinkableModels, required=True, description="The type model you want to link from")
        left_id = graphene.ID(
            required=True, description="The id of the model you want to link from"
        )
        right_type = graphene.Argument(LinkableModels, required=True, description="The type model you want to link to")
        right_id = graphene.ID(
            required=True, description="The id of the model you want to link to"
        )
        context = graphene.ID(required=False, description="The experiment this link is part of (optional), gives context to the link")
        relation = graphene.ID(required=True, description="The type of relation")


    def mutate(root, info, left_type, left_id, right_type,  right_id, relation, context=None, **kwargs ):
        creator = info.context.user
        x_class = linkable_models[left_type]
        y_class = linkable_models[right_type]
        # Just chekcing if the user actually has access to the models
        x_instance = x_class.objects.get(id=left_id)
        y_instance = y_class.objects.get(id=right_id)


        link, _ = models.DataLink.objects.update_or_create(
            x_content_type=ContentType.objects.get_for_model(x_class),
            y_content_type=ContentType.objects.get_for_model(y_class),
            x_id=x_instance.id,
            y_id=y_instance.id,
            relation_id=relation,
            creator=creator if creator else None,
            context_id=context,
            left_type=left_type,
            right_type=right_type,
        )


        return link

    class Meta:
        type = types.DataLink



class DeleteLinkResult(graphene.ObjectType):
    id = graphene.String()


class DeleteLink(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.DataLink.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteLinkResult
