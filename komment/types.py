from django.contrib.auth import get_user_model
from balder.types.object import BalderObject
import graphene
from grunnlag import models
from komment.enums import CommentableModelsEnum
from django.contrib.auth.models import Group as GroupModel
from balder.registry import register_type
from graphene.types.generic import GenericScalar


descendent_map = lambda: {
    "MentionDescendent": MentionDescendent,
    "ParagraphDescendent": ParagraphDescendent,
    "Leaf": Leaf,
}


@register_type
class Node(graphene.Interface):
    children = graphene.List(lambda: Descendent)
    untyped_children = GenericScalar()

    @classmethod
    def resolve_type(cls, instance, info):
        typemap = descendent_map()
        return typemap.get("typename", None)

    def resolve_untyped_children(root, info):
        return root.get("children", [])


@register_type
class Descendent(graphene.Interface):
    typename = graphene.String()

    @classmethod
    def resolve_type(cls, instance, info):
        typemap = descendent_map()
        return typemap.get(instance.get("typename"), Leaf)


@register_type
class Leaf(graphene.ObjectType):
    bold = graphene.Boolean(description="Is this a bold leaf?")
    italic = graphene.Boolean(description="Is this a italic leaf?")
    code = graphene.Boolean(description="Is this a code leaf?")
    text = graphene.String(description="The text of the leaf")

    class Meta:
        interfaces = (Descendent,)


@register_type
class MentionDescendent(graphene.ObjectType):
    user = graphene.String(description="The user that is mentioned", required=True)

    class Meta:
        interfaces = (Node, Descendent)


@register_type
class ParagraphDescendent(graphene.ObjectType):
    size = graphene.String(description="The size of the paragraph", required=False)

    class Meta:
        interfaces = (Node, Descendent)


class Comment(BalderObject):
    descendents = graphene.List(Descendent)
    children = graphene.List(
        lambda: Comment,
        limit=graphene.Int(description="How many children to return"),
        offset=graphene.Int(description="The offset for the children"),
    )
    content_type = graphene.Field(CommentableModelsEnum)

    def resolve_children(root, info, *args, offset=0, limit=20):
        return root.children.order_by("-created_at")[offset : offset + limit]

    def resolve_content_type(root, info):
        ct = root.content_type
        return f"{ct.app_label}_{ct.model}".replace(" ", "_").upper()

    class Meta:
        model = models.Comment
