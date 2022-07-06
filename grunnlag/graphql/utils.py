from guardian.shortcuts import assign_perm
import graphene
from django.contrib.contenttypes.models import ContentType

ct_types = {
    f"{ct.app_label}_{ct.model}".replace(" ", "_").upper(): ct
    for ct in ContentType.objects.filter(app_label__in=["grunnlag", "bord"])
}

AvailableModelsEnum = type(
    "AvailableModels",
    (graphene.Enum,),
    {m: m for m in ct_types.keys()},
)
