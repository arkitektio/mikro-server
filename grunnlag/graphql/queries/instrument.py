import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    InstrumentFilter,
    LabelFilter,
)


class Instruments(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.Instrument
        filter = InstrumentFilter
        paginate = True
        operation = "instruments"


class Instrument(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by")
        name = graphene.String(description="The name to search by")

    def resolve(root, info, id=None, name=None):
        if id:
            return models.Instrument.objects.get(id=id)
        if name:
            return models.Instrument.objects.get(name=name)

    class Meta:
        type = types.Instrument
        operation = "instrument"
