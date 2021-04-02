from ..messages.postman.provide import ProvideMessage
from ..messages.base import MessageDataModel, MessageMetaModel, MessageMetaExtensionsModel, MessageModel
from pydantic import BaseModel
from typing import List, Optional
from ..messages.types import PROVISION

class ProvisionParams(BaseModel):
    providers: Optional[List[str]]
    pass

class ProvisionMetaExtensionsModel(MessageMetaExtensionsModel):
    progress: Optional[str]
    callback: Optional[str]

class ProvisionMetaModel(MessageMetaModel):
    type: str
    extensions: Optional[ProvisionMetaExtensionsModel]

class ProvisionDataModel(MessageDataModel):
    id: int
    node: Optional[str] #TODO: Maybe not optional
    pod: Optional[str]
    template: Optional[str]

    status: Optional[str]
    statusmessage: Optional[str]
    reference: str
    params: Optional[ProvisionParams]


class ProvisionMessage(MessageModel):
    data: ProvisionDataModel
    meta: ProvisionMetaModel

    @classmethod
    def fromProvision(cls, provision):
        
        data = {
            "reference": provision.reference,
            "id" : provision.id,
            "node": provision.node.id if provision.node else None,
            "template": provision.template.id if provision.template else None,
            "pod": provision.pod.id if provision.pod else None,
            "params": {}
        }

        meta = {
            "reference": provision.reference,
            "type" : PROVISION,
            "extensions": {
            }
        }

        return cls(**{"data": data, "meta": meta})


    













