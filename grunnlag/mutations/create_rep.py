from herre.bouncer.utils import bounced
from grunnlag.enums import RepresentationVariety
from balder.enum import InputEnum
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
import logging
from bergen.console import console


logger = logging.getLogger(__name__)

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
        variety = kwargs.pop("variety", RepresentationVariety.UNKNOWN)
        name = kwargs.pop("name")
        tags = kwargs.pop("tags", [])

        print(variety.value)
        try:
            rep = models.Representation.objects.create(name=name, sample_id = sampleid, variety=variety.value)
            rep.tags.add(*tags)
            rep.save()
        except Exception as e:
            console.print_exception()
            logger.error(e)

        print(rep)
        return rep


    class Meta:
        type = types.Representation