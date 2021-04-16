

from delt.bridge.base import BaseBridge


class ArkitektBridge(BaseBridge):

    def __init__(self) -> None:
        host = "arkitekt"
        port = "8090"
        super().__init__(host, port)

arkitekt = ArkitektBridge()