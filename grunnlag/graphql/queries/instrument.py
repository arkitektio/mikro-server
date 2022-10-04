import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    InstrumentFilter,
    LabelFilter,
)


class Instruments(BalderQuery):
    """All Instruments
    
    This query returns all Instruments that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Instruments that the user has access to. If the user is an amdin
    or superuser, all Instruments will be returned."""

    class Meta:
        list = True
        type = types.Instrument
        filter = InstrumentFilter
        paginate = True
        operation = "instruments"


class Instrument(BalderQuery):
    """Get a single instrumes by ID
    
    Returns a single instrument by ID. If the user does not have access
    to the instrument, an error will be raised."""

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
