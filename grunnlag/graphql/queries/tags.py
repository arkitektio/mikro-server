from balder.types.query.base import BalderQuery
import graphene
from grunnlag.filters import ExperimentFilter, TagFilter
from grunnlag import types, models


class Tags(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Tag
        filter = TagFilter
        operation = "tags"
        paginate = True
