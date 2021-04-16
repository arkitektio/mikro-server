from pydantic.main import BaseModel
from ....messages.types import  UNPROVIDE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import List, Optional



class UnProvideMessageMetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

class UnProvideMetaModel(MessageMetaModel):
    type: str = UNPROVIDE
    extensions: Optional[UnProvideMessageMetaExtensionsModel]

class UnProvideDataModel(MessageDataModel):

    node: Optional[str] #TODO: Maybe not optional
    template: Optional[str]

class UnProvideMessage(MessageModel):
    data: UnProvideDataModel
    meta: UnProvideMetaModel