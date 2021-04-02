from ..messages.base import MessageDataModel, MessageMetaModel, MessageMetaExtensionsModel, MessageModel

from typing import Optional
from ..messages.types import ACTIVATE_POD


class ActivatePodMetaModel(MessageMetaModel):
    type: str = ACTIVATE_POD

class ActivatePodDataModel(MessageDataModel):
    pod: Optional[int]


class ActivatePodMessage(MessageModel):
    data: ActivatePodDataModel
    meta: ActivatePodMetaModel














