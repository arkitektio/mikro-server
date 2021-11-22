from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from grunnlag.enums import RepresentationVariety
from balder.enum import InputEnum
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
import logging
import namegenerator


logger = logging.getLogger(__name__)


class CreateMetric(BalderMutation):
    """Creates a Representation"""

    class Arguments:
        rep = graphene.ID(
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
        value = GenericScalar(required=True)

        creator = graphene.String(
            required=False,
            description="The Email of the user creating the Representation (only for backend apps)",
        )

    @bounced()
    def mutate(
        root, info, key, value, creator=None, sample=None, experiment=None, rep=None
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        metric = models.Metric.objects.create(
            rep_id=rep,
            key=key,
            value=value,
            creator=creator,
            smaple_id=sample,
            experiment_id=experiment,
        )
        return metric

    class Meta:
        type = types.Metric
