from django.utils.module_loading import import_string
from delt.service.types import Service, ServiceType
from django.conf import settings

class DeltSettings:

    def __init__(self) -> None:
        self.api_version = 0.1
        self.service_dict = settings.ARKITEKT_SERVICE

        self.types = self.service_dict.get("TYPES", [])
        self.outward = self.service_dict.get("OUTWARD")
        self.inward = self.service_dict.get("INWARD")

        self.port = self.service_dict.get("PORT", 8080)
        self.dependencies = self.service_dict.get("DEPENDENCIES", [])
        self.name = self.service_dict.get("NAME")
        self.version = self.service_dict.get("VERSION", "0.1")
        self.negotiate = import_string(self.service_dict.get("NEGOTIATE_HOOK"))
        self.point_type = self.service_dict.get("POINT_TYPE", "graphql")

        self.register_installed = self.service_dict.get("REGISTER_INSTALLED", False)



    @property
    def service(self):
        dc = self.service_dict
        return Service(types=self.types, outward=self.outward, inward=self.inward, port=self.port, dependencies=self.dependencies, name=self.name, version=self.version)




ACTIVE_DELT_SETTINGS = None

def get_active_settings():
    global ACTIVE_DELT_SETTINGS
    if ACTIVE_DELT_SETTINGS is None:
        ACTIVE_DELT_SETTINGS = DeltSettings()
    return ACTIVE_DELT_SETTINGS