from pydantic import BaseModel, field_validator
from typing import *

class OverViewClassResult(BaseModel):
    name: str
    fields: List[str]
    methods: List[str]

class OverviewResult(BaseModel):
    file: str
    desc: str
    process: List[str]
    structure: List[str]
    classes: List[OverViewClassResult]
    vars: List[str]
    funcs: List[str]