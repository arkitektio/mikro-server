import graphene
from graphene.utils.subclass_with_meta import SubclassWithMeta_Meta


class BalderObjectMeta(SubclassWithMeta_Meta):
    pass

    def __new__(cls, name, bases, ns, **kwargs):
        print(f"Registering {name}")
        return super(SubclassWithMeta_Meta, cls).__new__(cls, name, bases, ns, **kwargs)

    def __init__(cls, name, bases, ns, **kwargs):
        super(SubclassWithMeta_Meta, cls).__init__(name, bases, ns)
        print("registering")




class BalderQuery(graphene.ObjectType, metaclass=BalderObjectMeta):
    Output = None

    class Arguments:
        None

    