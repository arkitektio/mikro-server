from django.apps import apps
import logging
import graphene
from django.conf import settings

logger = logging.getLogger(__name__)

commentable_models = {}

for app in apps.get_app_configs():
    if app.label not in settings.COMMENTABLE_APPS:
        continue
    for model in app.get_models():
        commentable_models[
            f"{app.label}_{model.__name__}".replace(" ", "_").upper()
        ] = model


CommentableModelsEnum = type(
    "CommentableModels",
    (graphene.Enum,),
    {m: m for m in commentable_models.keys()},
)
