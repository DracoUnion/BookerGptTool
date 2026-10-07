from pydantic import BaseModel
from typing import *

class CheckSplitLineResult(BaseModel):
    no: int
    overturn_probability: float
    alternative: str
    counter_evidence: List[str]
    keep_split: bool

class CheckSplitResult(BaseModel):
    reviews: List[CheckSplitLineResult]
    
class ChapterSplitLineResult(BaseModel):
    no: int
    confidence: float
    reason: str

class ChapterSplitResult(BaseModel):
    chapter_starts: List[ChapterSplitLineResult]
