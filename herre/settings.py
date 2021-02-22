from django.conf import settings

class HerreSettings:

    def __init__(self) -> None:
        herre = settings.HERRE

        self.key_type = herre.get("KEY_TYPE")
        self.public_key = herre.get("PUBLIC_KEY")




ACTIVE_SETTINGS = None

def get_active_settings():
    global ACTIVE_SETTINGS
    if ACTIVE_SETTINGS is None:
        ACTIVE_SETTINGS = HerreSettings()
    return ACTIVE_SETTINGS