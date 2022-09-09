import json
from django.contrib.auth import get_user_model
from grunnlag.scalars import XArray
from grunnlag.omero import OmeroRepresentationInput
from lok import bounced
from grunnlag.enums import RepresentationVariety, RepresentationVarietyInput
from balder.enum import InputEnum
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
import logging
import namegenerator
from graphene.types.generic import GenericScalar


logger = logging.getLogger(__name__)


class UpdateRepresentation(BalderMutation):
    """Updates an Representation (also retriggers meta-data retrieval from data stored in)"""

    class Arguments:
        rep = graphene.ID(
            required=True, description="Which sample does this representation belong to"
        )
        variety = RepresentationVarietyInput(
            required=False, description="The variety of the representation"
        )
        origins = graphene.List(
            graphene.ID,
            required=False,
            description="Which representations were used to create this representation",
        )
        tags = graphene.List(graphene.String, required=False, description="Tags")
        sample = graphene.ID(required=False, description="The sample")

    @bounced()
    def mutate(
        root, info, *args, sample=None, tags=None, variety=None, rep=None, origins=None
    ):
        rep = models.Representation.objects.get(id=rep)
        rep.sample_id = sample or rep.sample_id
        if tags:
            rep.tags.set(tags)
        rep.variety = variety or rep.variety

        if origins:
            for o in origins:
                assert o != rep.id, "Cannot have self as origin"
                rep.derived.remove(o)

            rep.origins.set(origins)

        rep.save()
        return rep

    class Meta:
        type = types.Representation


class CreateRepresentation(BalderMutation):
    """Creates a Representation"""

    class Arguments:
        sample = graphene.ID(
            required=False,
            description="Which sample does this representation belong to",
        )
        name = graphene.String(
            required=False,
            description="A cleartext description what this representation represents as data",
        )
        creator = graphene.String(
            required=False,
            description="The Email of the user creating the Representation (only for backend apps)",
        )
        variety = graphene.Argument(
            RepresentationVarietyInput,
            required=False,
            description="A description of the variety",
        )
        array = graphene.Argument(XArray, required=False, description="The X Arra")
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the representation?",
        )
        origins = graphene.List(
            graphene.ID,
            required=False,
            description="Which representations were used to create this representation",
        )
        files = graphene.List(
            graphene.ID,
            required=False,
            description="Which files were used to create this representation",
        )
        meta = GenericScalar(required=False, description="Meta Parameters")
        omero = graphene.Argument(OmeroRepresentationInput)

    @bounced()
    def mutate(root, info, *args, creator=None, **kwargs):
        sampleid = kwargs.pop("sample", None)
        variety = kwargs.pop("variety", RepresentationVariety.UNKNOWN.value)
        name = kwargs.pop("name", namegenerator.gen())
        tags = kwargs.pop("tags", [])
        meta = kwargs.pop("meta", None)
        omero = kwargs.pop("omero", None)
        origins = kwargs.pop("origins", None)
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        print("oiNAOINSOINSAOISNOASNOASINOASINS")

        print(omero)

        rep = models.Representation.objects.create(
            name=name,
            sample_id=sampleid,
            variety=variety,
            creator=creator,
            meta=meta,
        )

        if omero:
            omero = models.Omero.objects.create(representation=rep, **omero)

        if tags:
            rep.tags.add(*tags)
        if origins:
            rep.origins.add(*origins)

        rep.save()

        return rep

    class Meta:
        type = types.Representation


class DeleteRepresentationResult(graphene.ObjectType):
    id = graphene.String()


class DeleteRepresentation(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="The ID of the two deletet Representation", required=True
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        rep = models.Representation.objects.get(id=id)
        rep.delete()
        return {"id": id}

    class Meta:
        type = DeleteRepresentationResult


class FromXArray(BalderMutation):
    """Creates a Representation"""

    class Arguments:
        sample = graphene.ID(
            required=False,
            description="Which sample does this representation belong to",
        )
        name = graphene.String(
            required=False,
            description="A cleartext description what this representation represents as data",
        )
        creator = graphene.String(
            required=False,
            description="The Email of the user creating the Representation (only for backend apps)",
        )
        variety = graphene.Argument(
            RepresentationVarietyInput,
            required=False,
            description="A description of the variety",
        )
        xarray = graphene.Argument(XArray, required=True, description="The X Arra")
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the representation?",
        )

        files = graphene.List(
            graphene.ID,
            required=False,
            description="Which files were used to create this representation",
        )
        origins = graphene.List(
            graphene.ID,
            required=False,
            description="Which representations were used to create this representation",
        )
        meta = GenericScalar(required=False, description="Meta Parameters")
        omero = graphene.Argument(OmeroRepresentationInput)

    @bounced()
    def mutate(root, info, *args, creator=None, **kwargs):
        sampleid = kwargs.pop("sample", None)
        variety = kwargs.pop("variety", RepresentationVariety.UNKNOWN.value)
        name = kwargs.pop("name", namegenerator.gen())
        tags = kwargs.pop("tags", [])
        meta = kwargs.pop("meta", None)
        omero = kwargs.pop("omero", None)
        xarray = kwargs.pop("xarray", None)
        origins = kwargs.pop("origins", None)
        files = kwargs.pop("files", None)

        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        rep = models.Representation.objects.create(
            name=name,
            sample_id=sampleid,
            variety=variety,
            creator=creator,
            meta=meta,
            store=xarray,
        )

        if omero:
            omero = models.Omero.objects.create(representation=rep, **omero)

        if tags:
            rep.tags.add(*tags)
        if origins:
            rep.origins.add(*origins)
        if files:
            rep.files.add(*files)

        rep.save()

        return rep

    class Meta:
        type = types.Representation
