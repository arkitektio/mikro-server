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

logger = logging.getLogger(__name__)


class CreateObjective(BalderMutation):
    """Creates an Instrument
    
    This mutation creates an Instrument and returns the created Instrument.
    The serial number is required and the manufacturer is inferred from the serial number.
    """

    class Arguments:
        name = graphene.String(required=True)
        serial_number = graphene.String(required=True)
        magnification = graphene.Float(required=True)
        manufacturer = graphene.String(required=False)

    @bounced()
    def mutate(
        root,
        info,
        name,
        serial_number,
        magnification,
        manufacturer=None,
    ):
        instrument, _ = models.Objective.objects.update_or_create(
            serial_number=serial_number, defaults=dict(
            name=name,
            magnification=magnification,
            **fill_created(info)
            )
        )
        return instrument

    class Meta:
        type = types.Objective
