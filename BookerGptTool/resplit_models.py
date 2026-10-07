from pydantic import BaseModel
from typing import *

class ChapterSplitResult(BaseModel):
    no: int
    split: bool
    confidence: float
    reason: str
    key_evidence: List[str]