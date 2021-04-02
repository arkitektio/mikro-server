from .views import ArkitektViewBuilder, NegotiateViewBuilder, PointViewBuilder, ProviderViewBuilder
from typing import Any, Callable, Dict, List
from .types import DataModel, Extension, ExtensionParams
from .parser import  parse_data_models
from delt.settings import get_active_settings
from django.conf.urls import url
from django.urls import include, path, re_path
from django.views.decorators.csrf import csrf_exempt


class ServiceRegistry:

    def __init__(self) -> None:
        self.settings = get_active_settings()
        self._models: List[DataModel] = None
        self.extensionBuilder: Dict[str, Callable[[Any], ExtensionParams]] = {}

    def registerModel(self, model: DataModel):
        self.models.append(model)

    @property
    def models(self):
        if "POINT" in self.settings.types:
            if self._models is None:
                if self.settings.register_installed:
                    self._models = parse_data_models()

            return self._models
        return None

    def on_negotiate(self, request):
        return self.settings.negotiate(request)

    def buildPaths(self):
        return (
            url('.well-known/arkitekt_service', ArkitektViewBuilder(self).as_view()),
            url('.well-known/arkitekt_provider', ProviderViewBuilder(self).as_view()),
            url('.well-known/arkitekt_point', PointViewBuilder(self).as_view()),
            url('.well-known/arkitekt_negotiate', csrf_exempt(NegotiateViewBuilder(self).as_view())),
        )





SERVICE_REGISTRY = None

def get_service_registry():
    global SERVICE_REGISTRY
    if SERVICE_REGISTRY is None:
        SERVICE_REGISTRY = ServiceRegistry()
    return SERVICE_REGISTRY