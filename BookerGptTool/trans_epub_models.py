from pydantic import BaseModel, field_validator, parse_obj_as
from typing import *

class Meta(BaseModel):
    name: str
    slug: str
    name_cn: str
    toc: List[List[str]] = []

class Chunk(BaseModel):
    raw: str
    fmt: str = ''
    trans : str = ''
    toc: List[List[str]] = []