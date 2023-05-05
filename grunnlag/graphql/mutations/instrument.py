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
from grunnlag.utils import fill_created

from grunnlag.scalars import AssignationID
logger = logging.getLogger(__name__)


class CreateInstrument(BalderMutation):
    """Creates an Instrument
    
    This mutation creates an Instrument and returns the created Instrument.
    The serial number is required and the manufacturer is inferred from the serial number.
    """

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
        objectives = graphene.List(
            graphene.ID,
            required=False,
            description="Which objectives are installed on this instrument",
        )
        lot_number = graphene.String(required=False)
        serial_number = graphene.String(required=False)
        model = graphene.String(required=False)
        manufacturer = graphene.String(required=False)
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced()
    def mutate(
        root,
        info,
        name,
        dichroics=None,
        detectors=None,
        filters=None,
        lot_number=None,
        objectives=None,
        serial_number=None,
        model=None,
        created_while=None,
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
                created_while=created_while,
                manufacturer=manufacturer,
                **fill_created(info)
            ),
            name=name,
        )
        if objectives:
            instrument.objectives.set(objectives)


        return instrument

    class Meta:
        type = types.Instrument
