from ....messages.generics import Token
from ....messages.types import BOUNCED_CANCEL_PROVIDE, CANCEL_PROVIDE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import Optional


class MetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

class MetaModel(MessageMetaModel):
    type: str = BOUNCED_CANCEL_PROVIDE
    extensions: MetaExtensionsModel
    token: Token

class DataModel(MessageDataModel):
    reference: str


class BouncedCancelProvideMessage(MessageModel):
    data: DataModel
    meta: MetaModel