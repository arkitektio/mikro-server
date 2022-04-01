from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput
from grunnlag import models, types


class InputVector(graphene.InputObjectType):
    x = graphene.Float(description="X-coordinate")
    y = graphene.Float(description="Y-coordinate")
    z = graphene.Float(description="Z-coordinate")


class CreateSizeFeature(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        label = graphene.ID(
            required=True, description="The Representation this ROI belongs to"
        )
        size = graphene.Float(description="The size", required=True)
        creator = graphene.ID(description="The creator of this feature")

    @bounced(anonymous=False)
    def mutate(root, info, label, size, creator=None):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        label = models.Label.objects.get(id=label)

        print(type)
        feature = models.SizeFeature.objects.create(
            creator=creator, label=label, size=size
        )

        return feature

    class Meta:
        type = types.SizeFeature
        operation = "createSizeFeature"
