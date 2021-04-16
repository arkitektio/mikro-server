from ....messages.types import CANCEL_ASSIGN, CANCEL_PROVIDE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import Optional


class MetaModel(MessageMetaModel):
    type: str = CANCEL_ASSIGN

class DataModel(MessageDataModel):
    reference: str


class CancelAssignMessage(MessageModel):
    data: DataModel
    meta: MetaModel