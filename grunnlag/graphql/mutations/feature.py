from typing import Any
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput
from grunnlag import models, types
from grunnlag.scalars import FeatureValue


class CreateFeature(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        label = graphene.ID(
            required=True, description="The Representation this ROI belongs to"
        )
        key = graphene.String(description="The key of the feature")
        value = FeatureValue(description="The size", required=True)
        creator = graphene.ID(description="The creator of this feature")

    @bounced(anonymous=False)
    def mutate(root, info, label, key, value, creator=None):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        label = models.Label.objects.get(id=label)

        feature = models.Feature.objects.create(
            creator=creator, label=label, key=key, value=value
        )

        return feature

    class Meta:
        type = types.Feature
        operation = "createfeature"
