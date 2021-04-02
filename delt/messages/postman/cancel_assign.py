from ...messages.types import CANCEL_ASSIGN
from ...messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import Optional


class CancelAssignMetaModel(MessageMetaModel):
    type: str = CANCEL_ASSIGN

class CancelAssignDataModel(MessageDataModel):
    reference: str


class CancelAssignMessage(MessageModel):
    data: CancelAssignDataModel
    meta: CancelAssignMetaModel