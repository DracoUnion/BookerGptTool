import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from ebooklib import epub
from openai import OpenAI
from tqdm import tqdm

from .novel_anls_models import (
    BookAnalysisReport, BookMeta, Chapter, ChapterSummary,
    MODULE_CLASS_MAP,
)
from .novel_anls_pmt import (
    SCAN_SYSTEM_PROMPT, SCAN_PROMPT,
    AGGREGATE_SYSTEM_PROMPT, AGGREGATE_PROMPT_MAP,
)


load_dotenv()
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
MODEL = os.getenv("MODEL_NAME", "gpt-4o-mini")
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "15"))
rate_limiter = Semaphore(RATE_LIMIT)


class NovelAnlsAgent:
    """统一封装小说分析的结构化 LLM 调用。"""

    def __init__(
        self,
        model: str = MODEL,
        temperature: float = 0.3,
        llm_client: Optional[OpenAI] = None,
    ):
        self.model = model
        self.temperature = temperature
        self.client = llm_client or client

    def _call(
        self,
        user_prompt: str,
        response_model,
        system_prompt: str,
    ):
        """发起一次结构化 LLM 调用并解析 Pydantic 响应。"""
        with rate_limiter:
            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
                temperature=self.temperature,
            )
        return completion.choices[0].message.parsed

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
        soup = BeautifulSoup(content, "lxml")

        for tag in soup(["script", "style"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n\s*\n", "\n\n", text).strip()

        if not text:
            continue

        title_tag = soup.find(["h1", "h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else f"第{chapter_index}章"

        chapters.append(Chapter(
            index=chapter_index,
            title=title,
            text=text,
        ))
        chapter_index += 1

    return chapters


class BookAnalyzerOrchestrator:
    """负责 EPUB 解析、阶段调度、结果汇总和报告保存。"""

    def __init__(
        self,
        epub_path: str,
        book_meta: Optional[BookMeta] = None,
        max_workers_stage1: int = 8,
        max_workers_stage2: int = 10,
        agent: Optional[NovelAnlsAgent] = None,
    ):
        self.epub_path = epub_path
        self.book_meta = book_meta or BookMeta()
        self.max_workers_stage1 = max_workers_stage1
        self.max_workers_stage2 = max_workers_stage2
        self.agent = agent or NovelAnlsAgent()

        self.chapters: List[Chapter] = []
        self.summaries: List[ChapterSummary] = []
        self.report: Dict[str, Any] = {}

    def _scan_single_chapter(self, chapter: Chapter) -> ChapterSummary:
        """单章扫描任务（供线程池调用）。"""
        return self.agent.scan_chapter(
            chapter_index=chapter.index,
            chapter_title=chapter.title,
            chapter_text=chapter.text,
        )

    def _run_stage1(self, max_chapters: Optional[int] = None) -> None:
        """执行阶段一：并行扫描所有章节。"""
        target = self.chapters
        if max_chapters:
            target = target[:max_chapters]
            print(f"⚠️ 仅处理前 {max_chapters} 章（限制模式）")

        print(
            f"🔍 阶段一：并行扫描（共 {len(target)} 章，"
            f"并发数 {self.max_workers_stage1}）..."
        )

        futures = {}
        with ThreadPoolExecutor(max_workers=self.max_workers_stage1) as executor:
            for chapter in target:
                future = executor.submit(self._scan_single_chapter, chapter)
                futures[future] = chapter.index

            results = {}
            with tqdm(total=len(futures), desc="扫描进度") as pbar:
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as error:
                        print(f"❌ 第 {index} 章扫描失败: {error}")
                        results[index] = ChapterSummary(
                            chapter=index,
                            title=(
                                self.chapters[index - 1].title
                                if index <= len(self.chapters) else None
                            ),
                            summary="扫描失败",
                            key_events=[],
                            new_characters=[],
                            character_updates=[],
                            emotional_tone=5,
                            chapter_end_hook=None,
                            foreshadowing_planted=[],
                            foreshadowing_payoff=[],
                            conflict_level=5,
                            word_count=(
                                len(self.chapters[index - 1].text)
                                if index <= len(self.chapters) else 0
                            ),
                        )
                    pbar.update(1)

        self.summaries = [results[index] for index in sorted(results.keys())]
        print(f"✅ 阶段一完成，共 {len(self.summaries)} 份摘要")

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
            print("❌ 错误：没有章节摘要，请先执行阶段一")
            return

        module_names = list(MODULE_CLASS_MAP.keys())
        print(
            f"🧩 阶段二：并行聚合（共 {len(module_names)} 个模块，"
            f"并发数 {self.max_workers_stage2}）..."
        )

        futures = {}
        with ThreadPoolExecutor(max_workers=self.max_workers_stage2) as executor:
            for module_name in module_names:
                future = executor.submit(
                    self._aggregate_single_module,
                    module_name,
                )
                futures[future] = module_name

            with tqdm(total=len(futures), desc="聚合进度") as pbar:
                for future in as_completed(futures):
                    module_name = futures[future]
                    try:
                        result_module_name, data = future.result()
                        self.report[result_module_name] = data
                    except Exception as error:
                        print(f"❌ 模块 [{module_name}] 聚合失败: {error}")
                        self.report[module_name] = {
                            "error": str(error),
                            "module": module_name,
                        }
                    pbar.update(1)

        print("✅ 阶段二完成，全部模块聚合完毕")

    def load_chapters(self) -> None:
        """加载并解析 EPUB。"""
        print(f"📖 正在解析 EPUB: {self.epub_path}")
        self.chapters = extract_text_from_epub(self.epub_path)
        print(f"✅ 共解析出 {len(self.chapters)} 章")

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
        print(f"💾 完整报告已保存至: {output_path}")

    def run_full_pipeline(
        self,
        max_chapters: Optional[int] = None,
        output_path: str = "book_analysis_report.json",
    ) -> BookAnalysisReport:
        """全自动执行完整流程。"""
        self.load_chapters()
        self._run_stage1(max_chapters=max_chapters)
        self._run_stage2()
        self.save_report(output_path)
        return BookAnalysisReport(
            book_meta=self.book_meta,
            total_chapters=len(self.summaries),
            chapter_summaries=self.summaries,
            modules=self.report,
        )


if __name__ == "__main__":
    book_meta = BookMeta(
        book_title="诡秘之主",
        author="爱潜水的乌贼",
        blurb="穿越到蒸汽与机械的诡异世界，成为占卜家...",
    )

    orchestrator = BookAnalyzerOrchestrator(
        epub_path="./books/example.epub",
        book_meta=book_meta,
        max_workers_stage1=8,
        max_workers_stage2=10,
    )

    result = orchestrator.run_full_pipeline(
        max_chapters=30,
        output_path="./report.json",
    )

    print("\n🎉 拆解完成！")
    print(f"已生成 {len(result.modules)} 个模块")
    for module_name in result.modules.keys():
        print(f"  - {module_name}")
