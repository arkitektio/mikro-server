from django.apps import apps
import logging
import graphene
from django.conf import settings

logger = logging.getLogger(__name__)

linkable_models = {}
reverse_linkable_models = {}

for app in apps.get_app_configs():
    for model in app.get_models():
        linkable_models[
            f"{app.label}_{model.__name__}".replace(" ", "_").upper()
        ] = model
        reverse_linkable_models[model] = f"{app.label}_{model.__name__}".replace(" ", "_").upper()


LinkableModels = type(
    "LinkableModels",
    (graphene.Enum,),
    {**{m: m for m in linkable_models.keys()}, "__doc__": "LinkableModels Models are models that can be shared amongst users and groups. They representent the models of the DB"},
)
