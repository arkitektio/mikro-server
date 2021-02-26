from delt.settings import get_active_settings
from pydantic import BaseModel
from typing import List, Optional

class DataModel(BaseModel):
    identifier: str
    extenders: List[str]
    extensions: Optional[dict]

class DataPoint(BaseModel):
    inward: str
    outward: str
    port: int
    type: str


class ExtensionParams(dict):
    pass

class Extension(BaseModel):
    name: str
    params: ExtensionParams


class DataQuery(BaseModel):
    version: str = get_active_settings().api_version
    point: DataPoint
    models: List[DataModel]
