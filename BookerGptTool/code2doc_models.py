from pydantic import BaseModel, field_validator, Field
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

class VarExtResult(BaseModel):
    name: str
    type: str
    desc: str

class FieldExtResult(BaseModel):
    name: str
    class_: str = Field(..., alias='class')
    type: str
    desc: str

class VarFieldExtResult(BaseModel):
    vars: List[VarExtResult]
    fields: List[FieldExtResult]