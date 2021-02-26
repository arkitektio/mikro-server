from herre.bouncer.utils import bounced
from grunnlag.enums import RepresentationVariety
from balder.enum import InputEnum
from balder.types import BalderMutation
import graphene
from grunnlag.models import Representation
from grunnlag.types import RepresentationType

class UpdateRepresentation(BalderMutation):
    """Updates an Representation (also retriggers meta-data retrieval from data stored in)
    """

    class Arguments:
        rep = graphene.ID(required=True, description="Which sample does this representation belong to")


    @bounced()
    def mutate(root, info, *args, **kwargs):
        rep = Representation.objects.get(id=kwargs.pop("rep"))
        rep.save()
        return rep


    class Meta:
        type = RepresentationType