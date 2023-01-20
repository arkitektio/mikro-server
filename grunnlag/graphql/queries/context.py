from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ContextFilter
from grunnlag import types, models


class Contexts(BalderQuery):
    """All Experiments
    
    This query returns all Experiments that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Experiments that the user has access to. If the user is an amdin
    or superuser, all Experiments will be returned.

    If you want to retrieve only the Experiments that you have created,
    use the `myExperiments` query.
    
    """

    class Meta:
        list = True
        type = types.Context
        filter = ContextFilter
        operation = "contexts"
        paginate = True


class ContextDetail(BalderQuery):
    """Get a single experiment by ID"
    
    Returns a single experiment by ID. If the user does not have access
    to the experiment, an error will be raised.
    
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    def resolve(self, info, id):
        x = models.Context.objects.get(id=id)
        # assert (
        #     info.context.user.has_perm("grunnlag.view_experiment", x)
        #     or x.creator == info.context.user
        # ), "You do not have permission to view this representation"

        return x

    class Meta:
        type = types.Context
        operation = "context"


class MyContexts(BalderQuery):
    """My Experiments runs a fast query on the database to return all
    Experiments that the user has created. This query is faster than
    the `experiments` query, but it does not return all Experiments that
    the user has access to."""

    class Meta:
        list = True
        personal = "creator"
        type = types.Context
        filter = ContextFilter
        paginate = True
        operation = "mycontexts"