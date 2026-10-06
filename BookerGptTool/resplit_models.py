from pydantic import BaseModel

class ChapterSplitResult(BaseModel):
    no: int
    chapter: int