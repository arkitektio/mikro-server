from typing import Mapping
from ..messages.types import ALLOWANCE
from ..messages.base import MessageDataModel, MessageMetaModel, MessageModel


class AllowanceMetaModel(MessageMetaModel):
    type: str = ALLOWANCE

class AllowanceDataModel(MessageDataModel):
    pod_template_map: Mapping[int, int]


class AllowanceMessage(MessageModel):
    data: AllowanceDataModel
    meta: AllowanceMetaModel