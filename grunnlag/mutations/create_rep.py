from herre.bouncer.utils import bounced
from grunnlag.enums import RepresentationVariety
from balder.enum import InputEnum
from balder.types import BalderMutation
import graphene
from grunnlag.models import Representation
from grunnlag.types import RepresentationType

class CreateRepresentation(BalderMutation):
    """Creates a Representation
    """

    class Arguments:
        sample = graphene.ID(required=True, description="Which sample does this representation belong to")
        name = graphene.String(required=True, description="A cleartext description what this representation represents as data")
        variety = graphene.Argument(InputEnum.from_choices(RepresentationVariety), required=True, description="A description of the variety")
        tags = graphene.List(graphene.String, required=False, description="Do you want to tag the representation?")


    @bounced()
    def mutate(root, info, *args, **kwargs):
        sampleid = kwargs.pop("sample")
        variety = kwargs.pop("variety", None)
        name = kwargs.pop("name")
        tags = kwargs.pop("tags", [])


        rep = Representation.objects.create(name=name, sample_id = sampleid, variety=variety)
        rep.tags.add(*tags)
        rep.save()
        return rep


    class Meta:
        type = RepresentationType