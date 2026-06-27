from pydantic import BaseModel, field_validator, parse_obj_as
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
    funcs: List[FuncExtResult]

class CodeDescItemResult(ClsFuncExtResult):
    file: str

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

class SrcAnlsDetailCodeResult(BaseModel):
    file: str
    class_or_func: str
    line: str

class SrcAnlsDetailUnitResult(BaseModel):
    no: int
    name: str
    points: List[str]
    code: List[SrcAnlsDetailCodeResult]

class SrcAnlsDetailResult(BaseModel):
    units: List[SrcAnlsDetailUnitResult]

class RestDetailSummaryResult(BaseModel):
    concept: str
    desc: str

class RestDetailExerciseResult(BaseModel):
    no: int
    title: str
    contents: List[str]

class RestDetailResult(BaseModel):
    learning_targets: List[str]
    code_map: List[str]
    life_analogy: List[str]
    summary: List[RestDetailSummaryResult]
    exercises: List[RestDetailExerciseResult]

class DetailResult(RestDetailResult, SrcAnlsDetailCodeResult):
    no: int = 0
    fixed: bool = False
