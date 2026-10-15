from typing import List
from pydantic import BaseModel, Field


class DupMetrics(BaseModel):
    """生成文案与素材的重复度指标"""
    ngram_similarity: float = Field(..., description='生成文案与素材的 n-gram Jaccard 相似度，0-1，越大表示重复越严重')
    ngram_coverage: float = Field(..., description='生成文案中被素材 n-gram 覆盖的比例，0-1，越大表示重复越严重')
    repeated_ngrams: List[str] = Field(default_factory=list, description='生成文案与素材共有的 n-gram 片段列表，即需要规避的重复部分')
    is_duplicated: bool = Field(..., description='是否重复度过高（达到阈值）')
