from grunnlag.models import Representation
from balder.types import BalderSubscription
from herre.bouncer.utils import bounced
from grunnlag.types import RepresentationType


class MyNewestRep(BalderSubscription):
    USERGROUP = lambda user: f"newest_rep_{user.id}"

    class Arguments:
        pass

    class Meta:
        type = RepresentationType
        model = Representation

    
    @bounced()
    def subscribe(root, info, *args, **kwargs):
        print(f"Wanted user {info.context.user.id}")
        return [f"newest_rep_{info.context.user.id}"]


