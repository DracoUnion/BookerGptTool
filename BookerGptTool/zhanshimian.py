# -*- coding: utf-8 -*-
"""
zhanshimian.py —— 人脸融合：人脸照片 + 多张场景照片 → 融合图（OpenAI gpt-image-edit）

流程：
1. 读取人脸照片路径与场景照片目录（或文件列表）；
2. 遍历场景照片，调用 gpt-image-edit 将人脸融合到每张场景中；
3. 输出融合后的图片到指定目录。

架构参照 AGENTS.md：Agent + Orchestrator + reg_subparser。
"""

import glob
import logging
from os import path

from .zhanshimian_agent import ZhanshimianAgent
from .zhanshimian_models import FaceFusionOutput

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 编排器
# ════════════════════════════════════════════════════════════════

class ZhanshimianOrchestrator:
    """协调人脸融合批量处理与输出。"""

    def __init__(self, args):
        self.args = args
        self.agent = ZhanshimianAgent(args)

    def run(self) -> FaceFusionOutput:
        face_img = self.args.face
        if not path.isfile(face_img):
            raise ValueError(f'人脸照片不存在：{face_img}')

        # 解析场景照片列表
        scene_imgs = self._collect_scenes(self.args.scenes)
        if not scene_imgs:
            raise ValueError('未找到任何场景照片')

        # 输出目录
        out_dir = self.args.output or 'zhanshimian_output'
        from os import makedirs
        makedirs(out_dir, exist_ok=True)

        logger.info(f'开始融合：人脸={face_img}, 场景数={len(scene_imgs)}, 输出目录={out_dir}')
        result = self.agent.fuse_batch(face_img, scene_imgs, out_dir)

        # 统计
        ok = sum(1 for r in result.results if r.success)
        fail = len(result.results) - ok
        logger.info(f'完成：成功 {ok} 张，失败 {fail} 张，输出目录={out_dir}')

        # 失败详情
        for r in result.results:
            if not r.success:
                logger.warning(f'  失败: {r.scene_image} -> {r.error}')

        return result

    def _collect_scenes(self, scenes_arg: str) -> list:
        """收集场景照片路径：支持目录、逗号分隔文件列表、glob 模式。"""
        # 如果是目录，递归收集常见图片格式
        if path.isdir(scenes_arg):
            exts = ('*.jpg', '*.jpeg', '*.png', '*.webp', '*.bmp')
            files = []
            for ext in exts:
                files.extend(glob.glob(path.join(scenes_arg, '**', ext), recursive=True))
            return sorted(files)
        # 如果包含逗号，按逗号分割
        if ',' in scenes_arg:
            return [s.strip() for s in scenes_arg.split(',') if s.strip() and path.isfile(s.strip())]
        # 否则尝试作为 glob 模式
        files = glob.glob(scenes_arg, recursive=True)
        return sorted([f for f in files if path.isfile(f)])


# ════════════════════════════════════════════════════════════════
# 入口 & 子命令注册
# ════════════════════════════════════════════════════════════════

def zhanshimian_handle(args):
    """入口函数：创建编排器并运行。"""
    ZhanshimianOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'zhanshimian',
        help='人脸融合：人脸照片 + 多张场景照片 → 融合图 (OpenAI gpt-image-edit)',
    )
    parser.add_argument('face', help='人脸照片路径（必选）')
    parser.add_argument('scenes', help='场景照片目录、逗号分隔文件列表或 glob 模式')
    parser.add_argument('-o', '--output', help='输出目录（默认 zhanshimian_output）')
    parser.add_argument('--size', default='1024x1024', help='输出图片尺寸（默认 1024x1024）')
    parser.add_argument('--model', default='gpt-image-1', help='模型名称（默认 gpt-image-1）')
    parser.set_defaults(func=zhanshimian_handle)
    return parser