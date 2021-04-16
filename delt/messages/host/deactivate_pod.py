from ...messages.base import MessageDataModel, MessageMetaModel,MessageModel

from typing import Optional
from ...messages.types import DEACTIVATE_POD


class MetaModel(MessageMetaModel):
    type: str = DEACTIVATE_POD

class DataModel(MessageDataModel):
    pod: Optional[int]


class DeActivatePodMessage(MessageModel):
    data: DataModel
    meta: MetaModel