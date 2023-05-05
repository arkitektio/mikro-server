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


from grunnlag.scalars import AssignationID
logger = logging.getLogger(__name__)


class CreateMetric(BalderMutation):
    """Create a metric

    This mutation creates a metric and returns the created metric.
    
    """


    class Arguments:
        representation = graphene.ID(
            required=False,
            description="Which Representaiton does this metric belong to",
        )
        sample = graphene.ID(
            required=False,
            description="Which Representaiton does this metric belong to",
        )
        experiment = graphene.ID(
            required=False,
            description="Which Representaiton does this metric belong to",
        )
        key = graphene.String(
            required=True,
            description="A cleartext description what this representation represents as data",
        )
        value = MetricValue(required=True)

        creator = graphene.String(
            required=False,
            description="The Email of the user creating the Representation (only for backend apps)",
        )
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced()
    def mutate(
        root,
        info,
        key,
        value,
        creator=None,
        sample=None,
        experiment=None,
         created_while=None,
        representation=None,
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        metric = models.Metric.objects.create(
            representation_id=representation,
            key=key,
            value=value,
            creator=creator,
            sample_id=sample,
            experiment_id=experiment,
            created_while=created_while,
        )
        return metric

    class Meta:
        type = types.Metric
