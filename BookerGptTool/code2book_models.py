from pydantic import BaseModel, field_validator
from typing import *

class FuncExtResult(BaseModel):
    name: str
    desc: str  

class ClsExtResult(BaseModel):
    name: str
    desc: str
    methods: List[FuncExtResult]

class ClsFuncExtResult(BaseModel):
    desc: str
    process: List[str]
    structure: List[str]
    classes: List[ClsExtResult]
    func: List[FuncExtResult]

class OutlineNodeResult(BaseModel):
    no: int
    name: str
    desc: str
    src: List[str]


class OutlineChapterResult(BaseModel):
    no: int
    name: str
    desc: str
    nodes: List[OutlineNodeResult]

class OutlinePartResult(BaseModel):
    no: int
    name: str
    desc: str
    chapters: List[OutlineChapterResult]

class OutlineResult(BaseModel):
    parts: List[OutlinePartResult]