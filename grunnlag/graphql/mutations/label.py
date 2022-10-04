from unicodedata import name
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


class CreateLabel(BalderMutation):
    """Creates a Label
    
    This mutation creates a Label and returns the created Label.
    We require a reference to the image pixel value that the label belongs to.
    (Labels can be created for any pixel in an image, no matter if this image
    is a mask or not). However labels can only be created for pixels that are
    integer values.

    

    """

    class Arguments:
        instance = graphene.Int(
            required=True, description="The instance this Label belongs to"
        )
        representation = graphene.ID(
            required=True, description="The Representation this Label belongs to"
        )
        name = graphene.String(description="The label name")
        creator = graphene.ID(description="The creator of this label")

    @bounced(anonymous=False)
    def mutate(root, info, instance, representation, creator=None, name=None):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        label, created = models.Label.objects.get_or_create(
            instance=instance,
            representation_id=representation,
            creator=creator,
            name=name,
        )

        return label

    class Meta:
        type = types.Label
        operation = "createLabel"
