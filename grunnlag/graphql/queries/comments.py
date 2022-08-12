from balder.types.query import BalderQuery
from grunnlag.graphql.utils import AvailableModelsEnum, ct_types
import graphene
from django.contrib.auth.models import Group, Permission
from grunnlag import types, models
from balder.registry import register_type
import graphene


class CommentsFor(BalderQuery):
    class Arguments:
        model = graphene.Argument(AvailableModelsEnum, required=True)
        id = graphene.ID(required=False)

    def resolve(self, info, model, id):

        ct = ct_types[model]
        model = ct.model_class()

        f = models.Comment.objects.filter(content_type=ct, object_id=id)

        return f.order_by("-created_at").all()

    class Meta:
        type = types.Comment
        list = True
        operation = "commentsFor"


class MyMentions(BalderQuery):
    def resolve(self, info):
        f = info.context.user.mentioned_in
        return f.order_by("-created_at").all()

    class Meta:
        type = types.Comment
        list = True
        operation = "mymentions"
