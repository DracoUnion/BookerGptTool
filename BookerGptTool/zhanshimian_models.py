# -*- coding: utf-8 -*-
"""
zhanshimian_models.py —— 人脸融合的数据模型
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


class FaceFusionInput(_Base):
    """人脸融合输入：人脸照片 + 场景照片列表"""
    face_image: str = Field(..., description="人脸照片路径（必选）")
    scene_images: List[str] = Field(..., description="场景照片路径列表（必选）")


class FaceFusionResult(_Base):
    """单张人脸融合结果"""
    scene_image: str = Field(..., description="原场景照片路径")
    output_image: str = Field(..., description="融合后输出图片路径")
    success: bool = Field(..., description="是否成功")
    error: Optional[str] = Field(default=None, description="错误信息（失败时）")


class FaceFusionOutput(_Base):
    """人脸融合整体输出"""
    face_image: str = Field(..., description="输入人脸照片路径")
    results: List[FaceFusionResult] = Field(..., description="所有融合结果")