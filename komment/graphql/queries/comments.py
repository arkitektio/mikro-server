from balder.types.query import BalderQuery
from komment.enums import CommentableModelsEnum, commentable_models
import graphene
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from komment import types, models
from balder.registry import register_type
import graphene


class CommentsFor(BalderQuery):
    class Arguments:
        model = graphene.Argument(CommentableModelsEnum, required=True)
        id = graphene.ID(required=False)

    def resolve(self, info, model, id):

        model = commentable_models[model]

        f = models.Comment.objects.filter(
            content_type=ContentType.objects.get_for_model(model), object_id=id
        )

        return f.order_by("-created_at").all()

    class Meta:
        type = types.Comment
        list = True
        operation = "commentsfor"


class MyMentions(BalderQuery):
    def resolve(self, info):
        f = info.context.user.mentioned_in
        return f.order_by("-created_at").all()

    class Meta:
        type = types.Comment
        list = True
        operation = "mymentions"
