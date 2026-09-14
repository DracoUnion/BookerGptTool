from typing import List

from pydantic import BaseModel


class PrereqGap(BaseModel):
    knowledge: str
    why_needed: str
    teaching_action: str


class BriefResult(BaseModel):
    subject: str
    audience: str
    baseline: str
    language: str
    tier: str
    learning_outcomes: List[str]
    prerequisite_gaps: List[PrereqGap]
    plan: str


class OutlineNodeResult(BaseModel):
    no: int
    title: str
    question: str
    key_points: List[str]


class OutlineChapterResult(BaseModel):
    no: int
    title: str
    purpose: str
    nodes: List[OutlineNodeResult]


class OutlineResult(BaseModel):
    title: str
    preface: str
    chapters: List[OutlineChapterResult]


class ExerciseItem(BaseModel):
    no: int
    prompt: str
    hint: str
    solution: str


class ExercisesResult(BaseModel):
    exercises: List[ExerciseItem]


class ReviewResult(BaseModel):
    verdict: str
    comment: str
