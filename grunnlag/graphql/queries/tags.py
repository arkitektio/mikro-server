from balder.types.query.base import BalderQuery
import graphene
from grunnlag.filters import ExperimentFilter, TagFilter
from grunnlag import types, models


class Tags(BalderQuery):
    """All Tags
    
    Returns all Tags that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Tags that the user has access to. If the user is an amdin
    or superuser, all Tags will be returned.
    """

    class Meta:
        list = True
        type = types.Tag
        filter = TagFilter
        operation = "tags"
        paginate = True
