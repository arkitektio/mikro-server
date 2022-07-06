from balder.types.query import BalderQuery
from grunnlag.graphql.utils import AvailableModelsEnum, ct_types
import graphene
from django.contrib.auth.models import Group, Permission
from grunnlag import types


class PermissionsFor(BalderQuery):
    class Arguments:
        model = graphene.Argument(AvailableModelsEnum, required=True)
        name = graphene.String(required=False)

    def resolve(self, info, model, name=None):

        ct = ct_types[model]

        f = Permission.objects.filter(content_type=ct)
        if name:
            f = f.filter(name__icontains=name)

        return f.order_by("name")

    class Meta:
        type = types.Permission
        list = True
        operation = "permissionsFor"
