from ...messages.base import MessageDataModel, MessageMetaModel,MessageModel

from typing import Optional
from ...messages.types import ACTIVATE_POD


class MetaModel(MessageMetaModel):
    type: str = ACTIVATE_POD

class DataModel(MessageDataModel):
    pod: Optional[int]


class ActivatePodMessage(MessageModel):
    data: DataModel
    meta: MetaModel