from .views import DataPointViewBuilder, DataModelViewBuilder, DataQueryViewBuilder, ExtensionsViewBuilder
from typing import Any, Callable, Dict, List
from .types import DataModel, Extension, ExtensionParams
from .parser import  parse_data_models
from delt.settings import get_active_settings
from django.conf.urls import url
from django.urls import include, path, re_path



class DataModelRegistry:

    def __init__(self) -> None:
        self.settings = get_active_settings()
        self.models: List[DataModel] = []
        self.extensionBuilder: Dict[str, Callable[[Any], ExtensionParams]] = {}


    def registerInstalledModels(self, exclude=[]):
        self.models = parse_data_models()

    def registerDataModel(self, model: DataModel):
        self.models.append(model)

    def registerExtensionBuilder(self, extension: str, builder: Callable[[Any],ExtensionParams]):
        self.extensionBuilder[extension] = builder

    def buildExtensionsForRequest(self, request) -> List[Extension]:
        return [Extension(name=key, params=builder(request)) for key, builder in self.extensionBuilder.items()]
    
    def buildPaths(self):
        return (
            url('.well-known/arnheim_models', DataModelViewBuilder(self).as_view()),
            url('.well-known/arnheim_point', DataPointViewBuilder(self).as_view()),
            url('.well-known/arnheim_query', DataQueryViewBuilder(self).as_view()),
            url('.well-known/extensions', ExtensionsViewBuilder(self).as_view()),
            url('.well-known/arkitekt', ExtensionsViewBuilder(self).as_view()),
        )





DATA_MODEL_REGISTRY = None

def get_datamodel_registry():
    global DATA_MODEL_REGISTRY
    if DATA_MODEL_REGISTRY is None:
        DATA_MODEL_REGISTRY = DataModelRegistry()
    return DATA_MODEL_REGISTRY