from typing import Dict, Tuple
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from komment import models, types
from lok import bounced
from komment.enums import CommentableModelsEnum, commentable_models
import logging


class DescendendInput(graphene.InputObjectType):
    children = graphene.List(lambda: DescendendInput, required=False)
    typename = graphene.String(description="The type of the descendent", required=False)
    user = graphene.String(description="The user that is mentioned", required=False)
    bold = graphene.Boolean(description="Is this a bold leaf?", required=False)
    italic = graphene.Boolean(description="Is this a italic leaf?", required=False)
    code = graphene.Boolean(description="Is this a code leaf?", required=False)
    text = graphene.String(description="The text of the leaf", required=False)


def recurse_parse_decendents(
    variables: Dict,
) -> Tuple[Dict, Dict]:
    """Parse Variables

    Recursively traverse variables, applying the apply function to the value if the predicate
    returns True.

    Args:
        variables (Dict): The dictionary to parse.
        predicate (Callable[[str, Any], bool]):The path this is in
        apply (Callable[[Any], Any]): _description_

    Returns:
        Dict: _description_
    """

    mentions = []

    def recurse_extract(obj, path: str = None):
        """
        recursively traverse obj, doing a deepcopy, but
        replacing any file-like objects with nulls and
        shunting the originals off to the side.
        """

        if isinstance(obj, list):
            nulled_obj = []
            for key, value in enumerate(obj):
                value = recurse_extract(
                    value,
                    f"{path}.{key}" if path else key,
                )
                nulled_obj.append(value)
            return nulled_obj
        elif isinstance(obj, dict):
            nulled_obj = {}
            for key, value in obj.items():
                if key == "typename" and value == "MentionDescendent":
                    mentions.append(obj)
                value = recurse_extract(value, f"{path}.{key}" if path else key)
                nulled_obj[key] = value
            return nulled_obj
        else:
            return obj

    dicted_variables = recurse_extract(variables)

    return dicted_variables, mentions


class CreateComment(BalderMutation):
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
        type = graphene.Argument(CommentableModelsEnum, required=True, description="The type model you want to comment on")
        object = graphene.ID(
            required=True, description="The Representationss this sROI belongs to"
        )
        descendents = graphene.List(DescendendInput, required=True, description="The comment tree")
        parent = graphene.ID(description="The parent comment", required=False)

    @bounced()
    def mutate(root, info, type, object, descendents, parent=None):
        creator = info.context.user
        model_class = commentable_models[type]
        UserModel = get_user_model()
        instance = model_class.objects.get(id=object)

        dicted_variables, mentions = recurse_parse_decendents(descendents)

        users = [UserModel.objects.get(email=m["user"]) for m in mentions]

        exp = models.Comment.objects.create(
            content_object=instance,
            user=creator,
            text="",
            descendents=descendents,
            parent_id=parent,
        )
        exp.mentions.set(users)
        exp.save()

        return exp

    class Meta:
        type = types.Comment
