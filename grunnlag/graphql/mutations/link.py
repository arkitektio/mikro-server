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
        x_type = graphene.Argument(LinkableModels, required=True, description="The type model you want to link from")
        x_id = graphene.ID(
            required=True, description="The id of the model you want to link from"
        )
        y_type = graphene.Argument(LinkableModels, required=True, description="The type model you want to link to")
        y_id = graphene.ID(
            required=True, description="The id of the model you want to link to"
        )
        context = graphene.ID(required=False, description="The experiment this link is part of (optional), gives context to the link")
        relation = graphene.String(required=True, description="The type of relation")


    def mutate(root, info, x_type, x_id, y_type,  y_id, relation, context=None, **kwargs ):
        creator = info.context.user
        x_class = linkable_models[x_type]
        y_class = linkable_models[y_type]
        # Just chekcing if the user actually has access to the models
        x_instance = x_class.objects.get(id=x_id)
        y_instance = y_class.objects.get(id=y_id)


        link, _ = models.DataLink.objects.update_or_create(
            x_content_type=ContentType.objects.get_for_model(x_class),
            y_content_type=ContentType.objects.get_for_model(y_class),
            x_id=x_instance.id,
            y_id=y_instance.id,
            relation=relation,
            creator=creator if creator else None,
            context_id=context,
            left_type=x_type,
            right_type=y_type,
        )


        return link

    class Meta:
        type = types.DataLink

