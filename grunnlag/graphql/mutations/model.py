from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lok import bounced
from grunnlag import models, types
from grunnlag.enums import ModelKind
from pathlib import Path
import ntpath
from grunnlag import models, types
from grunnlag.scalars import ModelFile
import logging
from grunnlag.utils import fill_created

logger = logging.getLogger(__name__)

class CreateImageToImageModel(BalderMutation):
    """Creates an Instrument
    
    This mutation creates an Instrument and returns the created Instrument.
    The serial number is required and the manufacturer is inferred from the serial number.
    """

    class Arguments:
        data = ModelFile(required=True, description="The model")
        kind = graphene.Argument(ModelKind, required=True, description="Which kind of model is this?")
        name = graphene.String(required=True)
        training_data = graphene.List(graphene.ID, required=False, description="Which training data does this model use?")
        experiments = graphene.List(graphene.ID, required=False, description="Which training data does this model use?")

    @bounced()
    def mutate(
        root,
        info,
        data=None,
        kind=None,
        name=None,
        training_data=None,
        experiments=None,
    ):
        instrument= models.ImageToImageModel.objects.create(
            kind=kind, data=data, name=name, **fill_created(info),
        )
        if training_data:
            instrument.training_data.set(training_data)
        if experiments:
            instrument.experiments.set(experiments)

        instrument.save()
        return instrument

    class Meta:
        type = types.ImageToImageModel