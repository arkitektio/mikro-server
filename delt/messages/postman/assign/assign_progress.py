from enum import Enum
from pydantic.main import BaseModel
from ....messages.types import  ASSIGN_PROGRES, PROVIDE, PROVIDE_DONE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import List, Optional


class MetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

class MetaModel(MessageMetaModel):
    type: str = ASSIGN_PROGRES
    extensions: Optional[MetaExtensionsModel]


class ProgressLevel(str, Enum):
    INFO = "INFO"
    DEBUG = "DEBUG"


class DataModel(MessageDataModel):
    level: ProgressLevel
    message: str

class AssignProgressMessage(MessageModel):
    data: DataModel
    meta: MetaModel