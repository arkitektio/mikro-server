from django.apps import apps
from .types import DataModel
DATA_MODELS = None






def parse_data_models():
    global DATA_MODELS
    if not DATA_MODELS:
        allmodels = apps.get_models()
        DATA_MODELS = []


        for model in allmodels:
            meta = model._meta
            try: 
                is_arnheim = meta.arkitekt
            except:
                continue
            if is_arnheim:
                module = model._meta.app_label
                identifier = model.__name__.lower()
                extenders = meta.extenders if hasattr(meta, "extenders") else []
                DATA_MODELS.append(DataModel(module=module, identifier=identifier, extenders=extenders))

            
    return DATA_MODELS