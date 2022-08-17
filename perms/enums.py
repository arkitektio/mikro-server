from django.apps import apps
import logging
import graphene
from django.conf import settings

logger = logging.getLogger(__name__)

sharable_models = {}

for app in apps.get_app_configs():
    if app.label not in settings.SHARABLE_APPS:
        continue
    for model in app.get_models():
        sharable_models[
            f"{app.label}_{model.__name__}".replace(" ", "_").upper()
        ] = model


SharableModelsEnum = type(
    "SharableModels",
    (graphene.Enum,),
    {m: m for m in sharable_models.keys()},
)
