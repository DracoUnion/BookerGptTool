from pydantic import BaseModel, field_validator
from typing import *

class CleanHeadingResult(BaseModel):
    info: List[List[int]]
    copyright: List[List[int]]
    toc: List[List[int]]
    preface: List[List[int]]
    about: List[List[int]]
    body: List[List[int]]
    etc: List[List[int]]

    @field_validator(
        "info", "copyright", "toc",
        "preface", "about", "body", "etc",
    )
    @classmethod
    def check_arr(arr):
        for inner in arr:
            if len(inner) != 2:
                raise ValueError('内层数组长度必须为 2')
        return arr