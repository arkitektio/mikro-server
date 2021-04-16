from pydantic.main import BaseModel
from ....messages.types import  ASSIGN, PROVIDE
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import List, Optional

class AssignParams(BaseModel):
    providers: Optional[List[str]]


class MetaExtensionsModel(MessageMetaExtensionsModel):
    with_progress: bool = False

class MetaModel(MessageMetaModel):
    type: str = ASSIGN
    extensions: Optional[MetaExtensionsModel]

class DataModel(MessageDataModel):
    node: Optional[str] #TODO: Maybe not optional
    template: Optional[str]
    pod: Optional[str]

    params: Optional[AssignParams]

    args: Optional[dict]
    kwargs: Optional[dict]


class AssignMessage(MessageModel):
    data: DataModel
    meta: MetaModel