from pydantic import BaseModel

class ChapterSplitResult(BaseModel):
    no: int
    split: bool