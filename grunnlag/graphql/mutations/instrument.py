from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from grunnlag.enums import RepresentationVariety
from balder.enum import InputEnum
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from grunnlag.scalars import MetricValue
import logging
import namegenerator


logger = logging.getLogger(__name__)


class CreateInstrument(BalderMutation):
    """Creates a Representation"""

    class Arguments:
        name = graphene.String(required=True)
        dichroics = graphene.List(
            GenericScalar,
            required=False,
            description="Which Representaiton does this metric belong to",
        )
        detectors = graphene.List(
            GenericScalar,
            required=False,
            description="Which Representaiton does this metric belong to",
        )
        filters = graphene.List(
            GenericScalar,
            required=False,
            description="Which Representaiton does this metric belong to",
        )
        lot_number = graphene.String(required=False)
        serial_number = graphene.String(required=False)
        model = graphene.String(required=False)
        manufacturer = graphene.String(required=False)

    @bounced()
    def mutate(
        root,
        info,
        name,
        dichroics=None,
        detectors=None,
        filters=None,
        lot_number=None,
        serial_number=None,
        model=None,
        manufacturer=None,
    ):
        creator = info.context.user

        instrument, _ = models.Instrument.objects.update_or_create(
            dict(
                dichroics=dichroics,
                detectors=detectors,
                filters=filters,
                lot_number=lot_number,
                serial_number=serial_number,
                model=model,
                manufacturer=manufacturer,
            ),
            name=name,
        )
        return instrument

    class Meta:
        type = types.Instrument
