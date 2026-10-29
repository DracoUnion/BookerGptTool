# -*- coding: utf-8 -*-
"""
podcast.py —— 文章 → 播客脚本 + 音频 + 封面 + 小宇宙发布文案

流程：
1. 读取文章 Markdown；
2. 用 LLM 生成 15 分钟百家讲坛风格播客脚本（~4000 字，分段）；
3. 可选：用 IndexTTS2 本地推理逐段生成并拼接为 MP3；
4. 用 LLM 生成小宇宙发布文案；
5. 可选：生成播客封面 HTML（01fish 色板）。

架构参照 article_img 子命令（Agent + Orchestrator + reg_subparser）。
"""

import json
import logging
import os
import re
import subprocess
import sys
from os import path

from .util import write_text
from .podcast_agent import PodcastAgent
from .podcast_models import PodcastCopy

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

PALETTE = {
    'green': '#1A3328', 'paper': '#F2EDE3', 'red': '#C44536',
    'moss': '#7A8C80', 'dark': '#0F1F18',
}


# ════════════════════════════════════════════════════════════════
# HTML 渲染（播客封面）
# ════════════════════════════════════════════════════════════════

def cover_html(title: str) -> str:
    """生成播客封面 HTML（01fish 色板，900x383 宽图，浏览器打开另存为 PNG）。"""
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>封面-01fish</title>
<style>
body{{margin:0;display:flex;justify-content:center;padding:40px;background:#eee}}
.cover{{width:900px;height:383px;position:relative;background:{PALETTE['green']};
  color:{PALETTE['paper']};border-radius:24px;overflow:hidden;box-sizing:border-box;
  padding:60px;display:flex;flex-direction:column;justify-content:center;
  font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif}}
.cover .brand{{position:absolute;top:44px;left:60px;font-size:26px;font-weight:700;letter-spacing:3px;color:{PALETTE['paper']};opacity:.55}}
.cover .title{{font-size:56px;font-weight:800;line-height:1.25;color:{PALETTE['paper']}}}
.cover .accent{{width:90px;height:10px;background:{PALETTE['red']};margin:28px 0 20px}}
.cover .subtitle{{font-size:30px;color:{PALETTE['paper']};opacity:.8}}
</style></head><body><div class="cover">
  <div class="brand">01fish</div>
  <div class="title">{title}</div>
  <div class="accent"></div>
  <div class="subtitle">播客节目 · 百家讲坛式讲书</div>
</div></body></html>
<!-- 公众号头图（宽图）：浏览器打开 → 右键另存为 PNG -->'''


# ════════════════════════════════════════════════════════════════
# 编排器
# ════════════════════════════════════════════════════════════════

class PodcastOrchestrator:
    """协调播客脚本、音频、封面与小宇宙文案生成。"""

    def __init__(self, args):
        self.args = args
        self.agent = PodcastAgent(args)
        self.article = self._read_article(args.input)
        self.title = args.title or self._guess_title(self.article)

    def _read_article(self, fname: str) -> str:
        if not path.isfile(fname):
            raise ValueError(f'文章文件不存在：{fname}')
        return open(fname, encoding='utf8').read()

    @staticmethod
    def _guess_title(article: str) -> str:
        m = re.search(r'^#\s+(.+)$', article, flags=re.M)
        return m.group(1).strip() if m else '未命名'

    def step_script(self):
        """生成播客脚本，返回 (标题, 完整 Markdown, 分段落)。"""
        logger.info('[1] 生成播客脚本')
        md = self.agent.gen_script(self.article)
        raw_title = md.split('\n', 1)[0].lstrip('#').strip() if md.strip() \
            else (self.title or '未命名')
        paragraphs = [p.strip() for p in md.split('\n\n') if p.strip()]
        return raw_title, md, paragraphs

    def step_xy_copy(self) -> PodcastCopy:
        """生成小宇宙发布文案。"""
        logger.info('[4] 生成小宇宙文案')
        return self.agent.gen_xy_copy(self.article, self.title)

    def _tts_dir(self) -> str:
        return os.environ.get('INDEXTTS_DIR') or path.expanduser('~/index-tts')

    def step_audio(self, paragraphs, out_mp3) -> bool:
        """用 IndexTTS2 本地推理逐段生成并拼接为 MP3。失败返回 False。"""
        tts_dir = self._tts_dir()
        voice_ref = os.environ.get('VOICE_REF') or path.expanduser('~/voice_ref.wav')
        if not path.exists(tts_dir) or not path.exists(voice_ref):
            logger.warning(f'  ⚠ IndexTTS2 或参考音色缺失（{tts_dir} / {voice_ref}），跳过 TTS，仅输出脚本。')
            return False
        parts = []
        try:
            for i, chunk in enumerate([p for p in paragraphs if p][:8]):
                part = path.join(path.dirname(out_mp3), f'_tts_part_{i:03d}.wav')
                script = (
                    "import sys, os\n"
                    f"sys.path.insert(0, {json.dumps(tts_dir)})\n"
                    "os.chdir(" + json.dumps(tts_dir) + ")\n"
                    "from indextts.infer_v2 import IndexTTS2\n"
                    f"tts = IndexTTS2(checkpoints='checkpoints', cfg_path='checkpoints/config.yaml')\n"
                    f"tts.infer({json.dumps(voice_ref)}, {json.dumps(chunk)}, {json.dumps(part)})\n"
                )
                subprocess.run([sys.executable, '-c', script], check=True, timeout=600)
                parts.append(part)
            self._concat_wavs(parts, out_mp3)
            return True
        except Exception as e:
            logger.warning(f'  ⚠ TTS 失败：{e}，仅输出脚本文本，可手动录制。')
            return False

    @staticmethod
    def _concat_wavs(parts, out_mp3):
        import wave
        if not parts:
            raise RuntimeError('no parts')
        data = b''
        params = None
        for p in parts:
            with wave.open(p, 'rb') as w:
                if params is None:
                    params = w.getparams()
                data += w.readframes(w.getnframes())
        tmp = out_mp3 + '.tmp.wav'
        with wave.open(tmp, 'wb') as w:
            w.setparams(params)
            w.writeframes(data)
        subprocess.run(['ffmpeg', '-y', '-i', tmp, out_mp3], check=True, timeout=300)

    def run(self):
        if not self.article.strip():
            raise ValueError('文章内容为空')
        out_dir = self.args.output or (path.dirname(self.args.input) or '.')
        os.makedirs(out_dir, exist_ok=True)

        script_title, script_md, paragraphs = self.step_script()
        base = self.args.base or script_title
        script_path = path.join(out_dir, f'{base}-播客脚本.md')
        write_text(script_path, script_md)
        logger.info(f'[2] ✓ {script_path}')

        audio_path = ''
        if not self.args.no_tts:
            audio_path = path.join(out_dir, f'{base}.mp3')
            if self.step_audio(paragraphs, audio_path):
                logger.info(f'[3] ✓ {audio_path}')

        xy = self.step_xy_copy()
        if not xy.show_notes:
            xy.show_notes = script_md

        cover_path = ''
        if not self.args.no_cover:
            cover_path = path.join(out_dir, f'{base}-播客封面.html')
            write_text(cover_path, cover_html(script_title))
            logger.info(f'[5] ✓ {cover_path}')

        return {
            'script': script_path, 'audio': audio_path, 'cover': cover_path,
            'copy': xy, 'title': script_title,
        }


# ════════════════════════════════════════════════════════════════
# 入口 & 子命令注册
# ════════════════════════════════════════════════════════════════

def podcast_handle(args):
    """入口函数：创建编排器并运行。"""
    PodcastOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'podcast',
        help='文章 → 播客脚本 + 音频 + 封面 + 小宇宙文案 (LLM+IndexTTS2)',
    )
    parser.add_argument('input', help='文章 Markdown 文件路径')
    parser.add_argument('-o', '--output', help='输出目录')
    parser.add_argument('--base', default='', help='输出文件名基底')
    parser.add_argument('--title', default='', help='播客标题')
    parser.add_argument('--no-tts', action='store_true', help='跳过 IndexTTS2 音频生成')
    parser.add_argument('--no-cover', action='store_true', help='跳过封面 HTML')
    parser.set_defaults(func=podcast_handle)
    return parser