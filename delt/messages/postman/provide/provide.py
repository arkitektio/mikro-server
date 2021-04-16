from pydantic.main import BaseModel
from ....messages.types import  PROVIDE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import List, Optional

class ProvideParams(BaseModel):
    providers: Optional[List[str]]


class ProvideMetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

class ProvideMetaModel(MessageMetaModel):
    type: str = PROVIDE
    extensions: Optional[ProvideMetaExtensionsModel]

class ProvideDataModel(MessageDataModel):

    node: Optional[str] #TODO: Maybe not optional
    template: Optional[str]
    params: Optional[ProvideParams]


class ProvideMessage(MessageModel):
    data: ProvideDataModel
    meta: ProvideMetaModel