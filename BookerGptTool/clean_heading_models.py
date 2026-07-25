from pydantic import BaseModel, field_validator
from typing import *

class CleanHeadingLineResult(BaseModel):
    line: int
    role: Literal["info", "copyright", "toc", "preface", "about", "body", "etc"]