from pydantic.main import BaseModel
from ....messages.types import  PROVIDE, PROVIDE_CRITICAL, PROVIDE_DONE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import List, Optional


class MetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

class MetaModel(MessageMetaModel):
    type: str = PROVIDE_CRITICAL
    extensions: Optional[MetaExtensionsModel]

class DataModel(MessageDataModel):
    message: Optional[str] #TODO: Maybe not optional

class ProvideCriticalMessage(MessageModel):
    data: DataModel
    meta: MetaModel