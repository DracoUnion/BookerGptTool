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

class ChapterJudgeAccResult(BaseModel):
    no: int
    confidence: float
    final_reason: str
    accepted_evidence: List[str]
    rejected_evidence: List[str]
    need_human_review: bool

class ChapterJudgeRejResult(BaseModel):
    no: int
    reason: str
    need_human_review: bool

class ChapterJudgeResult(BaseModel):
    chapter_starts: List[ChapterJudgeAccResult]
    rejected_lines: List[ChapterJudgeRejResult]