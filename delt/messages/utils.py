from .host import DeActivatePodMessage, ActivatePodMessage
from .postman.assign import AssignMessage, AssignReturnMessage, AssignProgressMessage, AssignCriticalMessage, AssignYieldsMessage, BouncedAssignMessage, BouncedCancelAssignMessage
from .base import MessageModel
from .types import ACTIVATE_POD, ASSIGN, ASSIGN_CRITICAL, ASSIGN_PROGRES, ASSIGN_RETURN, ASSIGN_YIELD, BOUNCED_ASSIGN, BOUNCED_CANCEL_ASSIGN, BOUNCED_CANCEL_PROVIDE, BOUNCED_PROVIDE, DEACTIVATE_POD, PROVIDE, PROVIDE_DONE, PROVIDE_CRITICAL, PROVIDE_PROGRESS
from .postman.provide import ProvideDoneMessage, ProvideCriticalMessage, ProvideMessage, ProvideProgressMessage, BouncedProvideMessage, BouncedCancelProvideMessage

registry = {
    PROVIDE_DONE:  ProvideDoneMessage,
    BOUNCED_PROVIDE: BouncedProvideMessage,
    BOUNCED_CANCEL_PROVIDE: BouncedCancelProvideMessage,
    PROVIDE_CRITICAL: ProvideCriticalMessage,
    PROVIDE_PROGRESS: ProvideProgressMessage,
    PROVIDE: ProvideMessage,


    ASSIGN: AssignMessage,
    ASSIGN_CRITICAL: AssignCriticalMessage,
    ASSIGN_PROGRES: AssignProgressMessage,
    ASSIGN_RETURN: AssignReturnMessage,
    ASSIGN_YIELD: AssignYieldsMessage,
    BOUNCED_ASSIGN: BouncedAssignMessage,
    BOUNCED_CANCEL_ASSIGN: BouncedCancelAssignMessage,

    ACTIVATE_POD: ActivatePodMessage,
    DEACTIVATE_POD: DeActivatePodMessage
}


class MessageError(Exception):
    pass


def expandToMessage(message: dict) -> MessageModel:
    try:
        cls: MessageModel = registry[message["meta"]["type"]]
    except:
        raise MessageError(f"Didn't find an expander for message {message}")

    return cls(**message)
    

