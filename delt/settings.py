from django.conf import settings



class DeltSettings:

    def __init__(self) -> None:
        self.api_version = 0.1

        self.inward = settings.DELT["INWARD"]
        self.outward = settings.DELT["OUTWARD"]
        self.type = settings.DELT["TYPE"]
        self.port = settings.DELT["PORT"]

    @property
    def extensions(self):
        return [{
            "type": "array",
            "params": {
                "S3" : "S3"
            }
        }]


ACTIVE_DELT_SETTINGS = None

def get_active_settings():
    global ACTIVE_DELT_SETTINGS
    if ACTIVE_DELT_SETTINGS is None:
        ACTIVE_DELT_SETTINGS = DeltSettings()
    return ACTIVE_DELT_SETTINGS