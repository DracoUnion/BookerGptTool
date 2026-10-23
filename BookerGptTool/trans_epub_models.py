from pydantic import BaseModel, field_validator, parse_obj_as
from typing import *

class Meta(BaseModel):
    name: str
    slug: str
    name_cn: str

class Chunk(BaseModel):
    raw: str
    fmt: str = ''
    trans : str = ''
    toc: List[List[str]] = []

class TocExtResult(BaseModel):
    no: int
    chapter_title: bool
    reason: str

class TocLevelChange(BaseModel):
    line: int
    before: int
    after: int

class TocTextChange(BaseModel):
    line: int
    before: str
    after: str

class TocChanges(BaseModel): 
    level_changes: List[TocLevelChange]
    text_changes: List[TocTextChange]