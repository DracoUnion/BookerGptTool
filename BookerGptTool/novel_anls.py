import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import json_repair as json_repair
from ebooklib import epub
from pyquery import PyQuery
from tqdm import tqdm

from .novel_anls_models import (
    BookAnalysisReport, BookMeta, Chapter, ChapterSummary,
    MODULE_CLASS_MAP,
)
from .novel_anls_pmt import (
    SCAN_SYSTEM_PROMPT, SCAN_PROMPT,
    AGGREGATE_SYSTEM_PROMPT, AGGREGATE_PROMPT_MAP,
)
from .util import call_llm_retry, set_openai_props

logger = logging.getLogger(__name__)


class NovelAnlsAgent:
    """统一封装小说分析的结构化 LLM 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(args)

    def _call(
        self,
        user_prompt: str,
        response_model,
        system_prompt: str,
    ):
        """发起一次结构化 LLM 调用并解析 JSON 为 Pydantic 响应。"""
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        def parse(raw):
            data = json_repair.loads(raw)
            return response_model.model_validate(data)

        return call_llm_retry(
            msgs, self.model,
            retry=self.args.retry,
            temp=getattr(self.args, 'temp', 0.3),
            max_tokens=self.args.max_tokens,
            parse_output=parse,
        )

    def scan_chapter(
        self,
        chapter_index: int,
        chapter_title: str,
        chapter_text: str,
    ) -> ChapterSummary:
        """扫描单章，返回结构化摘要。"""
        user_prompt = SCAN_PROMPT.format(
            chapter_index=chapter_index,
            chapter_title=json.dumps(chapter_title, ensure_ascii=False),
            chapter_text=chapter_text,
        )
        return self._call(
            user_prompt,
            ChapterSummary,
            SCAN_SYSTEM_PROMPT,
        )

    def aggregate_module(
        self,
        module_name: str,
        summaries: List[ChapterSummary],
        book_meta: BookMeta,
    ):
        """聚合指定模块，返回对应的 Pydantic 模型。"""
        prompt_template = AGGREGATE_PROMPT_MAP[module_name]
        user_prompt = prompt_template.format(
            all_chapter_summaries=json.dumps(
                [summary.model_dump() for summary in summaries],
                ensure_ascii=False,
            ),
            book_meta=json.dumps(book_meta.model_dump(), ensure_ascii=False),
        )
        return self._call(
            user_prompt,
            MODULE_CLASS_MAP[module_name],
            AGGREGATE_SYSTEM_PROMPT,
        )


def extract_text_from_epub(epub_path: str) -> List[Chapter]:
    """解析 EPUB，按 spine 顺序提取章节。"""
    book = epub.read_epub(epub_path)
    chapters = []
    chapter_index = 1

    for item_id, _ in book.spine:
        item = book.get_item(item_id)
        if item is None or item.get_type() != 9:  # ITEM_DOCUMENT
            continue

        content = item.get_content().decode("utf-8", errors="ignore")
        doc = PyQuery(content)
        doc.remove('script, style')

        text = doc.text()
        text = re.sub(r"\n\s*\n", "\n\n", text).strip()

        if not text:
            continue

        title_el = doc('h1, h2, h3').first()
        title = title_el.text().strip() if title_el else f"第{chapter_index}章"

        chapters.append(Chapter(
            index=chapter_index,
            title=title,
            text=text,
        ))
        chapter_index += 1

    return chapters


class BookAnalyzerOrchestrator:
    """负责 EPUB 解析、阶段调度、结果汇总和报告保存。"""

    def __init__(self, args):
        self.args = args
        self.epub_path = args.fname
        self.book_meta = BookMeta(
            book_title=getattr(args, 'book_title', None),
            author=getattr(args, 'author', None),
            blurb=getattr(args, 'blurb', None),
        )
        self.max_workers_stage1 = getattr(args, 'threads', 8)
        self.max_workers_stage2 = getattr(args, 'threads', 10)
        self.agent = NovelAnlsAgent(args)

        self.chapters: List[Chapter] = []
        self.summaries: List[ChapterSummary] = []
        self.report: Dict[str, Any] = {}

    def _scan_single_chapter(self, chapter: Chapter) -> ChapterSummary:
        """单章扫描任务（供线程池调用）。"""
        return chapter.index, self.agent.scan_chapter(
            chapter_index=chapter.index,
            chapter_title=chapter.title,
            chapter_text=chapter.text,
        )

    def _run_stage1(self) -> None:
        """执行阶段一：并行扫描所有章节。"""
        max_chapters = getattr(self.args, 'max_chapters', None)
        target = self.chapters
        if max_chapters:
            target = target[:max_chapters]
            logger.info(f"⚠️ 仅处理前 {max_chapters} 章（限制模式）")

        logger.info(
            f"🔍 阶段一：并行扫描（共 {len(target)} 章，"
            f"并发数 {self.max_workers_stage1}）..."
        )

        futures = []
        with ThreadPoolExecutor(max_workers=self.max_workers_stage1) as executor:
            for chapter in target:
                future = executor.submit(self._scan_single_chapter, chapter)
                futures.append(future)

            results = {}
            with tqdm(total=len(futures), desc="扫描进度") as pbar:
                for future in as_completed(futures):
                    idx, summary = future.result()
                    results[idx] = summary
                    pbar.update(1)

        self.summaries = [results[index] for index in sorted(results.keys())]
        logger.info(f"✅ 阶段一完成，共 {len(self.summaries)} 份摘要")

    def _aggregate_single_module(self, module_name: str) -> tuple:
        """单模块聚合任务（供线程池调用）。"""
        if module_name not in MODULE_CLASS_MAP:
            return module_name, {"error": f"未找到模块 {module_name} 的模型"}

        try:
            result = self.agent.aggregate_module(
                module_name=module_name,
                summaries=self.summaries,
                book_meta=self.book_meta,
            )
            return module_name, result.model_dump()
        except Exception as error:
            return module_name, {"error": str(error), "module": module_name}

    def _run_stage2(self) -> None:
        """执行阶段二：并行聚合所有模块。"""
        if not self.summaries:
            logger.info("❌ 错误：没有章节摘要，请先执行阶段一")
            return

        module_names = list(MODULE_CLASS_MAP.keys())
        logger.info(
            f"🧩 阶段二：并行聚合（共 {len(module_names)} 个模块，"
            f"并发数 {self.max_workers_stage2}）..."
        )

        futures = []
        with ThreadPoolExecutor(max_workers=self.max_workers_stage2) as executor:
            for module_name in module_names:
                future = executor.submit(
                    self._aggregate_single_module,
                    module_name,
                )
                futures.append(future)

            with tqdm(total=len(futures), desc="聚合进度") as pbar:
                for future in as_completed(futures):
                    module_name, data = future.result()
                    self.report[module_name] = data
                    pbar.update(1)

        logger.info("✅ 阶段二完成，全部模块聚合完毕")

    def load_chapters(self) -> None:
        """加载并解析 EPUB。"""
        logger.info(f"📖 正在解析 EPUB: {self.epub_path}")
        self.chapters = extract_text_from_epub(self.epub_path)
        logger.info(f"✅ 共解析出 {len(self.chapters)} 章")

    def save_report(self, output_path: str = "book_analysis_report.json") -> None:
        """保存最终报告。"""
        report = BookAnalysisReport(
            book_meta=self.book_meta,
            total_chapters=len(self.summaries),
            chapter_summaries=self.summaries,
            modules=self.report,
        )
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(report.model_dump(mode="json"), file, ensure_ascii=False, indent=2)
        logger.info(f"💾 完整报告已保存至: {output_path}")

    def run_full_pipeline(self) -> BookAnalysisReport:
        """全自动执行完整流程。"""
        output_path = self.epub_path[:-5] + '_book_anls.json' \
            if self.epub_path.endswith('.epub') \
            else self.epub_path + '_book_anls.json'
        self.load_chapters()
        self._run_stage1()
        self._run_stage2()
        self.save_report(output_path)
        return BookAnalysisReport(
            book_meta=self.book_meta,
            total_chapters=len(self.summaries),
            chapter_summaries=self.summaries,
            modules=self.report,
        )

    def run(self) -> None:
        """打印分析结果摘要。"""
        if not self.epub_path.endswith('.epub'):
            logger.info('请提供 EPUB 文件')
            return
        result = self.run_full_pipeline()
        logger.info("\n🎉 拆解完成！")
        logger.info(f"已生成 {len(result.modules)} 个模块")
        for module_name in result.modules.keys():
            logger.info(f"  - {module_name}")


def novel_anls(args):
    """CLI 入口函数。"""
    orchestrator = BookAnalyzerOrchestrator(args)
    orchestrator.run()
