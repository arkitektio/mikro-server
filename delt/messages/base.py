import json
import aiormq
from pydantic import BaseModel, Field
from typing import Any, Callable, Optional, Type, TypeVar
import uuid

from pydantic.types import UUID4


class MessageMetaExtensionsModel(BaseModel):
    """ Extensions to the AMQP Message protocol

    We wrap this in its own message to allow more"""
    progress: Optional[str]

class MessageMetaModel(BaseModel):
    type: str
    reference: str = Field(default_factory=lambda: str(uuid.uuid4()))
    extensions: Optional[MessageMetaExtensionsModel] = {}


class MessageDataModel(BaseModel):
    pass


T = TypeVar("T")

class MessageModel(BaseModel):
    data: MessageDataModel
    meta: MessageMetaModel

    def to_message(self) -> bytes:
        return json.dumps(self.dict()).encode()

    def to_channels(self) -> bytes:
        return json.dumps(self.dict())
        
    @classmethod
    def from_message(cls: Type[T], message) -> T:
        return cls(**json.loads(message.body.decode()))

    @classmethod
    def from_channels(cls: Type[T], message: str) -> T:
        return cls(**json.loads(message))


    @classmethod
    def unwrapped_message(cls: Type[T], function) -> Callable[[Any], T]:

        async def unwrapped(self, message: aiormq.types.DeliveredMessage, *args, **kwargs):
            print(message)
            input = cls.from_message(message)
            return await function(self, input, message, *args, **kwargs)

        return unwrapped
