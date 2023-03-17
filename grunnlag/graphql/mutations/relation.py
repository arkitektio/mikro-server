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

class CreateRelation(BalderMutation):
    """Creates a new Relation
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        name = graphene.String(description="The name of the relation", required=True)
        description = graphene.String(description="The description of the relation", required=False)

    @bounced(anonymous=False)
    def mutate(root, info, name, description=None):

        rel, _ = models.Relation.objects.update_or_create(
            name=name, defaults=dict(description=description)
        )

        return rel

    class Meta:
        type = types.Relation
        operation = "createRelation"
