from pydantic import BaseModel, field_validator
from typing import *

class CleanHeadingLineResult(BaseModel):
    start_no: int
    end_no: int
    role: Literal["info", "copyright", "toc", "preface", "about", "body", "etc"]