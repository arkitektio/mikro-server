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


class CreateCamera(BalderMutation):
    """Creates an Camera
    
    This mutation creates an Instrument and returns the created Instrument.
    The serial number is required and the manufacturer is inferred from the serial number.
    """

    class Arguments:
        name = graphene.String(required=True)
        serial_number = graphene.String(required=True)
        sensor_size_x = graphene.Int(required=True)
        sensor_size_y = graphene.Int(required=True)
        physical_sensor_size_x = graphene.Float(required=True)
        physical_sensor_size_y = graphene.Float(required=True)
        created_while = AssignationID(required=False, description="The assignation id")
        model = graphene.String(required=False)
        bit_depth = graphene.Int(required=False)


    @bounced()
    def mutate(
        root,
        info,
        serial_number,
        **kwargs
    ):
        instrument, _ = models.Camera.objects.get_or_create(
            serial_number=serial_number, defaults=dict(
            **kwargs,
            **fill_created(info)
            )
        )
        return instrument

    class Meta:
        type = types.Camera
