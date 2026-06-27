from pydantic import BaseModel, field_validator
from typing import *

class OCRContentResult(BaseModel):
    type: Literal['paragraph', 'title', 'list', 'table', 'quote', 'image', 'code']
    markdown: str
    bbox: List[float]

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, bbox):
        if not len(bbox) == 4:
            raise ValueError('bbox 长度应为 4')
        for it in bbox:
            if not 0 <= it <= 1:
                raise ValueError('bbox 每个元素应在 0 和 1 之间')
        return bbox
            


class OCRResult(BaseModel):
    direction: Literal['horizonal', 'vertical']
    contents: List[OCRContentResult]