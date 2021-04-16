from ....messages.types import CANCEL_PROVIDE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import Optional


class CancelProvideMetaModel(MessageMetaModel):
    type: str = CANCEL_PROVIDE

class CancelProvideDataModel(MessageDataModel):
    reference: str


class CancelProvideMessage(MessageModel):
    data: CancelProvideDataModel
    meta: CancelProvideMetaModel