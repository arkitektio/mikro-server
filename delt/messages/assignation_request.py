from ..messages.base import MessageDataModel, MessageMetaExtensionsModel, MessageMetaModel, MessageModel
from pydantic import BaseModel
from typing import Optional
from enum import Enum

class AssignationAction(str, Enum):
    CANCEL = 'cancel'
    START = "start"

class AssignationRequestParams(BaseModel):
    pass

class AssignationRequestMetaAuthModel(MessageMetaExtensionsModel):
    token: str

class AssignationRequestMetaExtensionsModel(MessageMetaExtensionsModel):
    # Set by postman consumer
    progress: Optional[str]
    callback: Optional[str]

    # Set by postman
    with_progress: Optional[bool]  = False
    with_callback: Optional[bool] = False

class AssignationRequestMetaModel(MessageMetaModel):
    type: str
    auth: AssignationRequestMetaAuthModel
    extensions: Optional[AssignationRequestMetaExtensionsModel]

class AssignationRequestDataModel(MessageDataModel):
    node: Optional[int] #TODO: Maybe not optional
    pod: Optional[int]
    template: Optional[int]
    action: AssignationAction = AssignationAction.START
    reference: str
    callback: Optional[str]
    progress: Optional[str]

    inputs: dict
    params: Optional[AssignationRequestParams]


class AssignationRequestMessage(MessageModel):
    data: AssignationRequestDataModel
    meta: AssignationRequestMetaModel