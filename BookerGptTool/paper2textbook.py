import html
import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from os import path

import yaml

from .openai import logger as oai_logger
from .paper2textbook_agent import Paper2TextbookAgent
from .paper2textbook_models import *
from .paper2textbook_pmt import HTML_TEMPLATE, TEX_STYLE
from .util import extname

logging.basicConfig(level=logging.INFO, format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


TIERS = {'lite': 1, 'standard': 2, 'deep': 3}
DIALECTS = {'md': 'Markdown', 'tex': 'LaTeX', 'html': 'HTML'}


class Paper2TextbookOrchestrator:
    """编排论文阅读、教学设计、章节写作和教材交付。"""

    def __init__(self, args):
        self.args = args
        if args.rounds is None:
            args.rounds = TIERS[args.tier]
        if args.rounds < 1:
            raise ValueError('review rounds must be positive')
        self.agent = Paper2TextbookAgent(args)
        if args.out is None:
            stem = path.splitext(path.basename(args.fname))[0]
            args.out = stem + '_textbook'
        self.out = path.abspath(args.out)
        self.pool = ThreadPoolExecutor(max_workers=args.threads)
        self.ext = args.format

    def _write_yaml(self, fname, obj):
        data = obj.model_dump() if hasattr(obj, 'model_dump') else obj
        path.dirname(fname) and os.makedirs(path.dirname(fname), exist_ok=True)
        with open(fname, 'w', encoding='utf8') as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    def _load_paper(self):
        cached = path.join(self.out, 'paper.md')
        if path.isfile(cached) and path.getsize(cached):
            return open(cached, encoding='utf8').read()
        fname = path.abspath(self.args.fname)
        if not path.isfile(fname):
            raise ValueError('请提供本地 MD/TEX/TXT/PDF 文件')
        ext = extname(fname).lower()
        if ext == 'pdf':
            try:
                import fitz
            except ImportError as ex:
                raise ValueError('读取 PDF 需要安装 PyMuPDF') from ex
            with fitz.open(fname) as doc:
                text = '\n\n'.join(page.get_text() for page in doc)
        elif ext in {'md', 'markdown', 'tex', 'txt'}:
            text = open(fname, encoding='utf8').read()
        else:
            raise ValueError('仅支持 MD/TEX/TXT/PDF 文件')
        open(cached, 'w', encoding='utf8').write(text)
        return text

    def _gen_brief(self, paper):
        fname = path.join(self.out, 'brief.yaml')
        if path.isfile(fname) and path.getsize(fname):
            return BriefResult(**yaml.safe_load(open(fname, encoding='utf8')))
        result = self.agent.gen_brief(paper, self.args.lang, self.args.tier)
        self._write_yaml(fname, result)
        return result

    def _gen_outline(self, paper, brief):
        fname = path.join(self.out, 'outline.yaml')
        if path.isfile(fname) and path.getsize(fname):
            return OutlineResult(**yaml.safe_load(open(fname, encoding='utf8')))
        result = self.agent.gen_outline(paper, brief)
        self._write_yaml(fname, result)
        return result

    def _chapter_fname(self, no):
        width = max(2, len(str(no)))
        return path.join(self.out, 'chapter_' + str(no).zfill(width) + '.' + self.ext)

    def _gen_one_chapter(self, paper, brief, outline, chapter):
        fname = self._chapter_fname(chapter.no)
        if path.isfile(fname) and path.getsize(fname):
            return chapter.no, open(fname, encoding='utf8').read()
        body = self.agent.gen_chapter(
            paper, brief, outline, chapter.no, chapter.title, DIALECTS[self.ext]
        )
        for _ in range(self.args.rounds):
            review = self.agent.review_chapter(paper, brief, outline, chapter.no, body)
            if review.verdict.strip().upper() == 'PERFECT' or '[PERFECT/]' in review.comment:
                break
            body = self.agent.fix_chapter(
                paper, brief, outline, chapter.no, body, review.comment, DIALECTS[self.ext]
            )
        open(fname, 'w', encoding='utf8').write(body)
        return chapter.no, body

    def _gen_chapters(self, paper, brief, outline):
        chapters = {}
        futures = [self.pool.submit(self._gen_one_chapter, paper, brief, outline, ch)
                   for ch in outline.chapters]
        for future in futures:
            no, body = future.result()
            chapters[no] = body
        return chapters

    def _exercise_fname(self, no):
        return path.join(self.out, 'exercises_' + str(no).zfill(2) + '.json')

    def _gen_exercises(self, paper, brief, outline):
        exercises = {}
        for chapter in outline.chapters:
            fname = self._exercise_fname(chapter.no)
            if path.isfile(fname) and path.getsize(fname):
                data = json.loads(open(fname, encoding='utf8').read())
                result = ExercisesResult(**data)
            else:
                result = self.agent.gen_exercises(
                    paper, brief, outline, chapter.no, chapter.title
                )
                with open(fname, 'w', encoding='utf8') as f:
                    json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)
            exercises[chapter.no] = result
        return exercises

    @staticmethod
    def _exercise_markdown(result):
        lines = []
        for item in result.exercises:
            lines.extend([
                f'### {item.no}. {item.prompt}',
                '', '**提示：** ' + item.hint, '',
                '<details><summary>解答</summary>', '', item.solution,
                '', '</details>', '',
            ])
        return '\n'.join(lines)

    def _assemble_md(self, brief, outline, chapters, exercises):
        lines = [f'# {outline.title}', '', outline.preface, '', '## 教学简报',
                 '', f'- **读者假设：** {brief.baseline}',
                 f'- **学习目标：** {"；".join(brief.learning_outcomes)}', '',]
        for chapter in outline.chapters:
            lines += [f'# 第 {chapter.no} 章 {chapter.title}', '', chapters[chapter.no],
                      '', '## 练习与解答', '', self._exercise_markdown(exercises[chapter.no])]
        open(path.join(self.out, 'book.md'), 'w', encoding='utf8').write('\n'.join(lines))

    def _assemble_tex(self, brief, outline, chapters, exercises):
        with open(path.join(self.out, 'textbook-style.tex'), 'w', encoding='utf8') as f:
            f.write(TEX_STYLE)
        chapter_files = []
        for chapter in outline.chapters:
            fname = f'chapter_{chapter.no:02d}.tex'
            chapter_files.append(fname)
            open(path.join(self.out, fname), 'w', encoding='utf8').write(
                f'\\section{{{chapter.title}}}\n' + chapters[chapter.no] + '\n'
            )
        solutions = []
        for chapter in outline.chapters:
            solutions.extend([f'\\subsection*{{{chapter.title}}}', self._exercise_tex(exercises[chapter.no])])
        open(path.join(self.out, 'solutions.tex'), 'w', encoding='utf8').write('\n'.join(solutions))
        inputs = '\n'.join('\\input{' + path.splitext(f)[0] + '}' for f in chapter_files)
        main = '\\documentclass[UTF8,11pt]{ctexart}\n\\input{textbook-style.tex}\n'
        main += '\\title{' + outline.title.replace('&', '\\&') + '}\n\\begin{document}\n\\maketitle\n'
        main += inputs + '\n\\section*{习题解答}\n\\input{solutions}\n\\end{document}\n'
        open(path.join(self.out, 'main.tex'), 'w', encoding='utf8').write(main)

    @staticmethod
    def _exercise_tex(result):
        lines = []
        for item in result.exercises:
            lines += [f'\\paragraph{{{item.no}.}} {item.prompt}', '\\textit{提示：} ' + item.hint,
                      '\\textbf{解答：} ' + item.solution, '']
        return '\n'.join(lines)

    def _assemble_html(self, brief, outline, chapters, exercises):
        nav = ' '.join(f'<a href="#chapter-{ch.no}">{html.escape(ch.title)}</a>' for ch in outline.chapters)
        body = []
        for chapter in outline.chapters:
            body += [f'<section id="chapter-{chapter.no}"><h2>{html.escape(chapter.title)}</h2>', chapters[chapter.no],
                     '<h3>练习与解答</h3>', self._exercise_html(exercises[chapter.no]), '</section>']
        content = HTML_TEMPLATE.format(lang=self.args.lang, title=html.escape(outline.title),
                                       preface=html.escape(outline.preface), nav=nav, body='\n'.join(body))
        open(path.join(self.out, 'tutorial.html'), 'w', encoding='utf8').write(content)

    @staticmethod
    def _exercise_html(result):
        return '\n'.join('<article class="exercise"><p><strong>%s.</strong> %s</p><details><summary>提示与解答</summary><p>%s</p><p>%s</p></details></article>' %
                         (item.no, html.escape(item.prompt), html.escape(item.hint), html.escape(item.solution))
                         for item in result.exercises)

    def _assemble(self, brief, outline, chapters, exercises):
        if self.ext == 'md':
            self._assemble_md(brief, outline, chapters, exercises)
        elif self.ext == 'tex':
            self._assemble_tex(brief, outline, chapters, exercises)
        else:
            self._assemble_html(brief, outline, chapters, exercises)
        open(path.join(self.out, 'README.md'), 'w', encoding='utf8').write(
            '# paper2textbook 输出\n\n'
            f'- 教材：`{"book.md" if self.ext == "md" else "main.tex" if self.ext == "tex" else "tutorial.html"}`\n'
            '- 中间结果可重复运行并断点续作。\n'
            '- LaTeX 输出可在安装 XeLaTeX 与 ctex 后运行 `xelatex main.tex`。\n'
        )

    def run(self):
        if not path.isfile(self.args.fname):
            raise ValueError('请提供本地 MD/TEX/TXT/PDF 文件')
        os.makedirs(self.out, exist_ok=True)
        logger.info(self.args)
        paper = self._load_paper()
        brief = self._gen_brief(paper)
        outline = self._gen_outline(paper, brief)
        chapters = self._gen_chapters(paper, brief, outline)
        exercises = self._gen_exercises(paper, brief, outline)
        self._assemble(brief, outline, chapters, exercises)
        logger.info('[DONE] 教材已写入 %s', self.out)


def paper2textbook(args):
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    return Paper2TextbookOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser('paper2textbook', help='paper to textbook')
    parser.add_argument('fname', help='本地 MD/TEX/TXT/PDF 文件')
    parser.add_argument('-o', '--out', type=str, help='output dir name')
    parser.add_argument('-t', '--tier', choices=tuple(TIERS), default='standard', help='lite/standard/deep')
    parser.add_argument('-f', '--format', choices=('md', 'tex', 'html'), default='md', help='output format')
    parser.add_argument('-l', '--lang', choices=('zh', 'en'), default='zh', help='教材语言')
    parser.add_argument('-r', '--rounds', type=int, help='review rounds; defaults to tier')
    parser.add_argument('-x', '--threads', type=int, default=4, help='thread num')
    parser.add_argument('-D', '--debug', action='store_true', help='debug mode')
    parser.set_defaults(func=paper2textbook)
    parser.set_defaults(rounds=None)
