from ..messages.types import PROVISION_REQUEST
from ..messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from pydantic import BaseModel
from typing import Optional



class ProvisionRequestParams(BaseModel):
    pass

class ProvisionRequestMetaAuthModel(MessageMetaExtensionsModel):
    token: str

class ProvisionRequestMetaExtensionsModel(MessageMetaExtensionsModel):
    progress: Optional[str]
    callback: Optional[str]

    # Set by postman
    with_progress: Optional[bool]  = False
    with_callback: Optional[bool] = False


class ProvisionRequestMetaModel(MessageMetaModel):
    type: str = PROVISION_REQUEST
    auth: ProvisionRequestMetaAuthModel
    extensions: Optional[ProvisionRequestMetaExtensionsModel]

class ProvisionRequestDataModel(MessageDataModel):
    parent: Optional[int]
    node: Optional[int] 
    template: Optional[int]
    reference: str
    params: Optional[ProvisionRequestParams]


class ProvisionRequestMessage(MessageModel):
    data: ProvisionRequestDataModel
    meta: ProvisionRequestMetaModel