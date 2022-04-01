import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    LabelFilter,
)


class Labels(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.Label
        filter = LabelFilter
        paginate = True
        operation = "labels"


class Label(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by")

    def resolve(root, info, id):

        return models.Label.objects.get(id=id)

    class Meta:
        type = types.Label
        operation = "label"


class LabelFor(BalderQuery):
    """Get a label for a specific instance on a specific representation"""

    class Arguments:
        representation = graphene.ID(description="The ID to search by", required=True)
        instance = graphene.Int(
            description="The instance on the representation", required=True
        )

    def resolve(root, info, representation, instance):

        return models.Label.objects.get(
            representation_id=representation, instance=instance
        )

    class Meta:
        type = types.Label
        operation = "labelFor"
