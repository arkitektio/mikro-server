import json
from urllib import request
from django.contrib.auth import get_user_model
from grunnlag.scalars import XArrayInput, AssignationID
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
from grunnlag.utils import fill_created
from grunnlag.scalars import AssignationID
from grunnlag.inputs import RepresentationViewInput
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
        root, info, *args, sample=None, tags=None, variety=None, rep=None,   origins=None, position=None, **kwargs
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
        created_while = AssignationID(required=False, description="The assignation id")
        file_origins = graphene.List(
            graphene.ID,
            required=False,
            description="Which files were used to create this representation",
        )
        table_origins = graphene.List(
            graphene.ID,
            required=False,
            description="Which tables were used to create this representation (e.g simulation parameters)",
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
        views = graphene.List(
            RepresentationViewInput,
            required=False,
            description="Views for this representation",
        )

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
        created_while = kwargs.pop("created_while", None)
        experiments = kwargs.pop("experiments", None)
        file_origins = kwargs.pop("file_origins", None)
        roi_origins = kwargs.pop("roi_origins", None)
        table_origins = kwargs.pop("table_origins", None)
        views = kwargs.pop("views", None)

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
            created_while=created_while,
        )

        print(omero)
        logger.info(f"ROIS {roi_origins}")

        rep.save()
        omeromodel = None

        if omero:


            omeromodel = models.Omero.objects.create(
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
                objective_id =omero.get("objective", None),
            )
            
            positions = omero.get("positions", None)
            print(positions)
            if positions:
                omeromodel.positions.set(models.Position.objects.filter(id__in=positions))

            timepoints = omero.get("timepoints", None)
            print(timepoints)
            if timepoints:
                omeromodel.timepoints.set(models.Timepoint.objects.filter(id__in=timepoints))



            omeromodel.save()

        if views:
            if not omeromodel:
                omeromodel = models.Omero.objects.create(representation=rep)

            for view in views:
                channel = view.pop("channel", None)
                position = view.pop("position", None)
                objective = view.pop("objective", None)
                timepoint = view.pop("timepoint", None)


                viewmodel = models.View.objects.create(
                    omero=omeromodel,
                    channel_id=channel,
                    position_id=position,
                    objective_id=objective,
                    timepoint_id=timepoint,
                    **view,
                    **fill_created(info)
                )

        if tags:
            rep.tags.add(*tags)
        if origins:
            rep.origins.add(*origins)
        if experiments:
            rep.experiments.add(*experiments)
        if file_origins:
            rep.file_origins.add(*file_origins)
        if table_origins:
            rep.table_origins.add(*table_origins)
        if datasets:
            rep.datasets.add(*datasets)
        if roi_origins:
            rep.roi_origins.add(*roi_origins)

        rep.save()

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
