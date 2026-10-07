from pydantic import BaseModel
from typing import *

class ChapterSplitCheckResult(BaseModel):
    no: int
    overturn_probability: float
    alternative: str
    counter_evidence: List[str]
    context_conflict: str
    keep_split: bool

class ChapterSplitResult(BaseModel):
    no: int
    split: bool
    confidence: float
    reason: str
    key_evidence: List[str]