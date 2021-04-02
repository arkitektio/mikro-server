from ..messages.base import MessageDataModel, MessageMetaModel, MessageMetaExtensionsModel, MessageModel
from pydantic import BaseModel
from typing import Optional
from ..messages.types import ASSIGNATION
class AssignationParams(BaseModel):
    pass

class AssignationMetaExtensionsModel(MessageMetaExtensionsModel):
    progress: Optional[str]
    callback: Optional[str]

class AssignationMetaModel(MessageMetaModel):
    type: str
    extensions: Optional[AssignationMetaExtensionsModel]

class AssignationDataModel(MessageDataModel):
    id: int
    node: Optional[int] #TODO: Maybe not optional
    pod: Optional[int]
    template: Optional[int]
    status: Optional[str]
    statusmessage: Optional[str]
    reference: str
    callback: Optional[str]
    progress: Optional[str]

    inputs: dict
    outputs: Optional[dict]
    params: Optional[AssignationParams]


class AssignationMessage(MessageModel):
    data: AssignationDataModel
    meta: AssignationMetaModel

    @classmethod
    def fromAssignation(cls, assignation, **extensions):
        
        data = {
            "reference": assignation.reference,
            "id" : assignation.id,
            "node": assignation.node.id if assignation.node else None,
            "template": assignation.template.id if assignation.template else None,
            "pod": assignation.pod.id if assignation.pod else None,
            "callback": assignation.callback,
            "progress": assignation.progress,
            "inputs": assignation.inputs or {},
            "params": {}
        }

        meta = {
            "reference": assignation.reference,
            "type" : ASSIGNATION,
            "extensions": {
                **extensions,
                "callback": assignation.callback,
                "progress": assignation.progress

            }
        }

        return cls(**{"data": data, "meta": meta})


    













