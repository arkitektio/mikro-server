from balder.types.mutation.base import BalderMutation
import graphene
from graphene.types.generic import GenericScalar
from lok import bounced


class Negotiate(BalderMutation):
    class Arguments:
        additionals = GenericScalar(description="Additional Parameters")
        internal = graphene.Boolean(description="is this now a boolean")

    @bounced(only_jwt=True)
    def mutate(root, info, *args, internal=False, additionals={}):
        host = info.context.get_host().split(":")[0] if not internal else "minio"

        return {
            "protocol": "s3",
            "path": f"{host}:9000",
            "params": {
                "access_key": "weak_access_key",
                "secret_key": "weak_secret_key",
            },
        }

    class Meta:
        type = GenericScalar
        operation = "negotiate"
