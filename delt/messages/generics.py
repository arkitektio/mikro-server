from typing import List
from pydantic import BaseModel

class Token(BaseModel):
    roles: List[str]
    scopes: List[str]
    user: int

