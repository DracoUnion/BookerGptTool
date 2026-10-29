from pydantic import BaseModel, field_validator
from typing import *

class FListResult(BaseModel):
    implementation_approach: str
    file_list: List[str]
    data_structures_and_interfaces: List[str]
    program_call_flow: List[str]
    anything_unclear: str


class TasksResult(BaseModel):
    required_packages: List[str]
    required_other_language_third_party_packages: List[str]
    file_descs: List[str]
    task_list: List[str]
    full_api_spec: str
    shared_knowledge: str
    anything_uncliear: str