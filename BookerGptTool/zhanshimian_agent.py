# -*- coding: utf-8 -*-
"""
zhanshimian_agent.py —— 人脸融合的 LLM 智能体（调用 OpenAI gpt-image-edit）
"""

import base64
import logging
from os import path
from typing import List, Optional

from .openai import call_tti_retry, set_openai_props
from .zhanshimian_models import FaceFusionInput, FaceFusionResult, FaceFusionOutput
from .util import write_text

logger = logging.getLogger(__name__)


def read_image_base64(img_path: str) -> str:
    """读取图片并编码为 base64"""
    with open(img_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


class ZhanshimianAgent:
    """封装人脸融合（gpt-image-edit）的 API 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model or 'gpt-image-1'
        self.size = getattr(args, 'size', '1024x1024')
        set_openai_props(self.args)

    def fuse_one(self, face_b64: str, scene_path: str, out_path: str) -> FaceFusionResult:
        """融合单张场景照片。"""
        try:
            scene_b64 = read_image_base64(scene_path)
            # 调用 gpt-image-edit：人脸 + 场景 → 融合图
            # prompt 简述：把人脸自然地融合到场景中，保持光照、角度、表情一致
            prompt = (
                "将提供的人脸照片自然地融合到场景照片中。"
                "要求：保持人脸原本的五官特征、肤色、表情；"
                "匹配场景的光照方向、阴影、色调、透视角度；"
                "融合后画面自然无痕迹，像真实拍摄一样。"
            )
            img_bytes = call_tti_retry(
                prompt, self.model,
                size=self.size,
                ref_img=base64.b64decode(face_b64),  # 参考人脸
                retry=self.args.retry,
                nothrow=False,
            )
            if img_bytes:
                with open(out_path, 'wb') as f:
                    f.write(img_bytes)
                return FaceFusionResult(
                    scene_image=scene_path,
                    output_image=out_path,
                    success=True,
                )
            else:
                return FaceFusionResult(
                    scene_image=scene_path,
                    output_image='',
                    success=False,
                    error='API 返回空数据',
                )
        except Exception as e:
            logger.error(f'融合失败 {scene_path}: {e}')
            return FaceFusionResult(
                scene_image=scene_path,
                output_image='',
                success=False,
                error=str(e),
            )

    def fuse_batch(self, face_image: str, scene_images: List[str], out_dir: str) -> FaceFusionOutput:
        """批量融合所有场景照片。"""
        face_b64 = read_image_base64(face_image)
        results = []
        for i, scene in enumerate(scene_images, 1):
            logger.info(f'[{i}/{len(scene_images)}] 融合: {scene}')
            out_name = f'fused_{path.basename(scene)}'
            out_path = path.join(out_dir, out_name)
            res = self.fuse_one(face_b64, scene, out_path)
            results.append(res)
        return FaceFusionOutput(
            face_image=face_image,
            results=results,
        )