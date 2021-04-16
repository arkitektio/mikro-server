from ....messages.generics import Token
from ....messages.types import BOUNCED_CANCEL_ASSIGN
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import Optional


class MetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

class MetaModel(MessageMetaModel):
    type: str = BOUNCED_CANCEL_ASSIGN
    extensions: MetaExtensionsModel
    token: Token

class DataModel(MessageDataModel):
    reference: str


class BouncedCancelAssignMessage(MessageModel):
    data: DataModel
    meta: MetaModel