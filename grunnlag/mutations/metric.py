from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from herre.bouncer.utils import bounced
from grunnlag.enums import RepresentationVariety
from balder.enum import InputEnum
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from bergen.console import console
import logging
import namegenerator


logger = logging.getLogger(__name__)

class UpdateRepresentation(BalderMutation):
    """Updates an Representation (also retriggers meta-data retrieval from data stored in)
    """

    class Arguments:
        rep = graphene.ID(required=True, description="Which sample does this representation belong to")


    @bounced()
    def mutate(root, info, *args, **kwargs):
        rep = models.Representation.objects.get(id=kwargs.pop("rep"))
        rep.save()
        return rep


    class Meta:
        type = types.Representation



class CreateMetric(BalderMutation):
    """Creates a Representation
    """

    class Arguments:
        rep = graphene.ID(required=True, description="Which Representaiton does this metric belong to")
        key = graphene.String(required=True, description="A cleartext description what this representation represents as data")
        value = GenericScalar(required=True)

        creator = graphene.String(required=False, description="The Email of the user creating the Representation (only for backend apps)")


    @bounced()
    def mutate(root, info, rep, key, value, creator = None):
        creator = info.context.user or (get_user_model().objects.get(email=creator) if creator else None)

        metric = models.RepresentationMetric.objects.create(rep_id=rep, key=key, value=value, creator=creator)
        return metric


    class Meta:
        type = types.RepresentationMetric


