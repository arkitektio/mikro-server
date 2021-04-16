import os
from delt.settings import get_active_settings
import yaml
import logging
from typing import List, Optional
from pydantic import BaseModel
from enum import Enum
from requests_oauthlib import OAuth2Session
from oauthlib.oauth2 import BackendApplicationClient
from oauthlib.oauth2 import TokenExpiredError

logger = logging.getLogger(__name__)


class GrantType(str, Enum):
    IMPLICIT = "implicit"
    PASSWORD = "password"
    BACKEND = "backend"

class HerreConfig(BaseModel):
    secure: bool 
    host: str
    port: int 
    client_id: str 
    client_secret: str
    authorization_grant_type: GrantType
    scopes: List[str]
    redirect_uri: Optional[str]

    def __str__(self) -> str:
        return f"{'Secure' if self.secure else 'Insecure'} Connection to {self.host}:{self.port} on Grant {self.authorization_grant_type}"


class ConfigError(Exception):
    pass


class Auth:

    def __init__(self) -> None:
        settings = get_active_settings()
        config_path = "bergen.yaml"
        herre_dict = {}

        if os.path.isfile(config_path):
            with open(config_path,"r") as file:
                logger.info("Using local configuration ")
                config = yaml.load(file, Loader=yaml.FullLoader)
                if "herre" in config:
                    herre_dict.update(config["herre"])
                else:
                    raise ConfigError(f"No herre section in {config_path}")
        else:
            raise ConfigError(f"Couldn't find {config_path}")

        self.herre = HerreConfig(**herre_dict)
        assert self.herre.authorization_grant_type == GrantType.BACKEND

        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "0" if self.herre.secure else "1"

        self.scopes =  self.herre.scopes + ["introspection"]
        self.scope = " ".join(self.scopes)
        print(self.scope)

        self.backend = BackendApplicationClient(client_id=self.herre.client_id, scope=self.scope)

        self.client = OAuth2Session(client=self.backend, scope=self.scope)
        self.token = self.client.fetch_token(token_url=f'http://{self.herre.host}:{self.herre.port}/o/token/',
        client_secret=self.herre.client_secret)


    def post(self, *args, **kwargs):
        try:
            return self.client.post( *args, **kwargs)
        except TokenExpiredError as e:
            logger.info("Had to refresh the token")
            self.token = self.client.refresh_token(f'http://{self.herre.host}:{self.herre.port}/o/token/')

        self.client = OAuth2Session(client=self.backend, token=self.token)
        return self.client.post( *args, **kwargs)

