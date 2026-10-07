from pydantic import BaseModel
from typing import *

class ChapterSplitCheckResult(BaseModel):
    no: int
    overturn_probability: float
    alternative: str
    counter_evidence: List[str]
    keep_split: bool
    
class ChapterSplitLineResult(BaseModel):
    no: int
    confidence: float
    reason: str

class ChapterSplitResult(BaseModel):
    chapter_starts: List[ChapterSplitLineResult]
