from ....messages.generics import Token
from pydantic.main import BaseModel
from ....messages.types import  BOUNCED_ASSIGN
from ....messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from typing import List, Optional



class AssignParams(BaseModel):
    providers: Optional[List[str]]

class MetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

class MetaModel(MessageMetaModel):
    type: str = BOUNCED_ASSIGN
    extensions: Optional[MetaExtensionsModel]
    token: Token

class DataModel(MessageDataModel):
    node: Optional[str] #TODO: Maybe not optional
    template: Optional[str]
    pod: Optional[str]

    params: Optional[AssignParams]

    args: Optional[dict]
    kwargs: Optional[dict]


class BouncedAssignMessage(MessageModel):
    data: DataModel
    meta: MetaModel