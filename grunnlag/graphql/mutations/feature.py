from typing import Any
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput
from grunnlag import models, types
from grunnlag.scalars import FeatureValue
from grunnlag.utils import fill_created

from grunnlag.scalars import AssignationID
class CreateFeature(BalderMutation):
    """Creates a Feature
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        label = graphene.ID(
            required=True, description="The Label this Feature belongs to"
        )
        key = graphene.String(description="The key of the feature")
        value = FeatureValue(description="The value of the feature", required=True)
        creator = graphene.ID(description="The creator of this feature")
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced(anonymous=False)
    def mutate(root, info, label, key, value, created_while=None, creator=None):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        label = models.Label.objects.get(id=label)

        feature = models.Feature.objects.create(
            creator=creator, label=label, key=key,created_while=created_while, value=value, **fill_created(info)
        )

        return feature

    class Meta:
        type = types.Feature
        operation = "createfeature"
