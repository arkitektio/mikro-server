import json
from urllib import request
from django.contrib.auth import get_user_model
from grunnlag.scalars import XArrayInput
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
        position = graphene.ID(required=False, description="The position within an acquisition")
        tags = graphene.List(graphene.String, required=False, description="Tags")
        sample = graphene.ID(required=False, description="The sample")

    @bounced()
    def mutate(
        root, info, *args, sample=None, tags=None, variety=None, rep=None, origins=None, position=None, **kwargs
    ):
        rep = models.Representation.objects.get(id=rep)
        rep.sample_id = sample or rep.sample_id
        if tags:
            rep.tags.set(tags)
        rep.variety = variety or rep.variety

        if position:
            rep.position_id = position

        if origins:
            for o in origins:
                assert o != rep.id, "Cannot have self as origin"
                rep.derived.remove(o)

            rep.origins.set(origins)

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
        experiments = graphene.List(
            graphene.ID,
            required=False,
            description="Which experiments does this representation belong to",
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
        datasets = graphene.List(
            graphene.ID,
            required=False,
            description="Which datasets does this representation belong to",
        )
        xarray = graphene.Argument(XArrayInput, required=True, description="The X Arra")
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the repsresentation?",
        )

        file_origins = graphene.List(
            graphene.ID,
            required=False,
            description="Which files were used to create this representation",
        )
        roi_origins = graphene.List(
            graphene.ID,
            required=False,
            description="Which rois were used to create this representation",
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
        datasets = kwargs.pop("datasets", None)
        experiments = kwargs.pop("experiments", None)
        file_origins = kwargs.pop("file_origins", None)
        roi_origins = kwargs.pop("roi_origins", None)

        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        if not sampleid:
            if origins:
                sampleid = models.Representation.objects.get(id=origins[0]).sample_id

        rep = models.Representation.objects.create(
            name=name,
            sample_id=sampleid,
            variety=variety,
            creator=creator,
            meta=meta,
            store=xarray,
        )

        print(omero)
        logger.info(f"ROIS {roi_origins}")

        rep.save()

        if omero:
            omero = models.Omero.objects.create(
                representation=rep,
                planes=omero.get("planes", None),
                channels=omero.get("channels", None),
                scale=omero.get("scale", None),
                physical_size=omero.get("physical_size", None),
                acquisition_date=omero.get("acquisition_date", None),
                objective_settings=omero.get("objective_settings", None),
                imaging_environment=omero.get("imaging_environment", None),
                affine_transformation=omero.get("affine_transformation", None),
                instrument_id=omero.get("instrument", None),
                position_id=omero.get("position", None),
                objective_id =omero.get("objective", None),
            )

        if tags:
            rep.tags.add(*tags)
        if origins:
            rep.origins.add(*origins)
        if experiments:
            rep.experiments.add(*experiments)
        if file_origins:
            rep.file_origins.add(*file_origins)
        if datasets:
            rep.datasets.add(*datasets)
        if roi_origins:
            rep.roi_origins.add(*roi_origins)


        return rep

    class Meta:
        type = types.Representation


class PinRepresentation(BalderMutation):
    """Sets the pin"""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Representation.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Representation
