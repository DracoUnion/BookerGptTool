import os
import json
import re
from typing import List, Optional, Dict, Any, Type, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from abc import ABC, abstractmethod

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from ebooklib import epub
from bs4 import BeautifulSoup
from tqdm import tqdm

# ============================================================
#  1. 环境与客户端
# ============================================================

load_dotenv()
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
MODEL = os.getenv("MODEL_NAME", "gpt-4o-mini")
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "15"))
rate_limiter = Semaphore(RATE_LIMIT)

# ============================================================
#  2. Pydantic 模型定义
# ============================================================

# ---------- 阶段一：单章摘要 ----------
class CharacterBrief(BaseModel):
    name: str = Field(description="角色名")
    role: str = Field(description="本章中呈现的角色定位")
    traits: List[str] = Field(default_factory=list, description="性格/特征")


class CharacterUpdate(BaseModel):
    name: str = Field(description="已有角色名")
    change: str = Field(description="本章中的具体变化")


class ChapterSummary(BaseModel):
    module: Literal["章节摘要"] = "章节摘要"
    chapter: int = Field(description="章节序号")
    title: Optional[str] = Field(None, description="章节标题")
    summary: str = Field(description="50字以内核心摘要")
    key_events: List[str] = Field(default_factory=list, description="本章关键事件")
    new_characters: List[CharacterBrief] = Field(default_factory=list)
    character_updates: List[CharacterUpdate] = Field(default_factory=list)
    emotional_tone: int = Field(ge=0, le=10, description="情绪基调 0-10")
    chapter_end_hook: Optional[str] = Field(None, description="章尾钩子")
    foreshadowing_planted: List[str] = Field(default_factory=list, description="新埋伏笔")
    foreshadowing_payoff: List[str] = Field(default_factory=list, description="本章回收的伏笔")
    conflict_level: int = Field(ge=0, le=10, description="冲突烈度 0-10")
    word_count: int = Field(description="本章字数")


# ---------- 阶段二：十大模块 ----------
class GenreModule(BaseModel):
    module: Literal["题材与定位"] = "题材与定位"
    genre: Optional[str] = None
    target_audience: Optional[str] = None
    market_position: Optional[str] = None
    unique_selling_point: Optional[str] = None


class TitleModule(BaseModel):
    module: Literal["书名与简介"] = "书名与简介"
    title_analysis: Optional[Dict[str, Any]] = None
    blurb_hooks: Optional[List[str]] = None
    blurb_effectiveness: Optional[str] = None


class WorldModule(BaseModel):
    module: Literal["世界观设定"] = "世界观设定"
    core_rules: Optional[str] = None
    factions: Optional[List[Dict[str, str]]] = None
    unique_elements: Optional[List[str]] = None
    logic_consistency: Optional[str] = None
    world_immersion_score: Optional[int] = None


class CharacterModule(BaseModel):
    module: Literal["人物图谱"] = "人物图谱"
    protagonist: Optional[Dict[str, Any]] = None
    supporting_chars: Optional[List[Dict[str, Any]]] = None
    antagonist: Optional[Dict[str, Any]] = None
    relationship_map: Optional[str] = None


class StructureModule(BaseModel):
    module: Literal["主线与结构"] = "主线与结构"
    main_storyline: Optional[str] = None
    structure: Optional[Dict[str, Any]] = None
    subplots: Optional[List[Dict[str, str]]] = None
    structure_score: Optional[int] = None


class RhythmModule(BaseModel):
    module: Literal["节奏与爽点"] = "节奏与爽点"
    climax_points: Optional[List[Dict[str, Any]]] = None
    satisfaction_interval: Optional[str] = None
    flat_periods: Optional[List[Dict[str, str]]] = None
    conflict_escalation: Optional[str] = None
    emotion_curve: Optional[str] = None
    rhythm_score: Optional[int] = None


class GimmickModule(BaseModel):
    module: Literal["金手指/核心创意"] = "金手指/核心创意"
    unique_setting: Optional[str] = None
    innovation: Optional[str] = None
    core_gimmick: Optional[str] = None
    creativity_score: Optional[int] = None
    execution_score: Optional[int] = None


class OpeningModule(BaseModel):
    module: Literal["开篇钩子"] = "开篇钩子"
    first_500_words_hook: Optional[str] = None
    protagonist_first_impression: Optional[str] = None
    chapter_end_hooks: Optional[List[Dict[str, Any]]] = None
    golden_three_effectiveness: Optional[str] = None
    drop_off_risk: Optional[str] = None


class ForeshadowModule(BaseModel):
    module: Literal["伏笔与铺垫"] = "伏笔与铺垫"
    foreshadowing_list: Optional[List[Dict[str, Any]]] = None
    information_asymmetry: Optional[str] = None
    continuity: Optional[str] = None
    foreshadowing_score: Optional[int] = None


class PatternModule(BaseModel):
    module: Literal["可迁移模式"] = "可迁移模式"
    structural_patterns: Optional[List[str]] = None
    character_patterns: Optional[List[str]] = None
    rhythm_patterns: Optional[List[str]] = None
    writing_techniques: Optional[List[str]] = None
    imitation_advice: Optional[str] = None


# 模块名 → Pydantic 类映射
MODULE_CLASS_MAP: Dict[str, Type[BaseModel]] = {
    "题材与定位": GenreModule,
    "书名与简介": TitleModule,
    "世界观设定": WorldModule,
    "人物图谱": CharacterModule,
    "主线与结构": StructureModule,
    "节奏与爽点": RhythmModule,
    "金手指/核心创意": GimmickModule,
    "开篇钩子": OpeningModule,
    "伏笔与铺垫": ForeshadowModule,
    "可迁移模式": PatternModule,
}

# ============================================================
#  3. EPUB 解析器
# ============================================================

def extract_text_from_epub(epub_path: str) -> List[Dict[str, Any]]:
    """解析 EPUB，按 spine 顺序提取章节"""
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

        chapters.append({
            "index": chapter_index,
            "title": title,
            "text": text
        })
        chapter_index += 1

    return chapters


# ============================================================
#  4. 提示词（阶段一 + 阶段二 10个独立提示词）
# ============================================================

SCAN_PROMPT = """
你是一位小说章节扫描分析师。你的任务是对给定的**单章正文**进行结构化扫描，提取该章的核心信息。

当前章节序号：{{chapter_index}}
当前章节标题：{{chapter_title}}
当前章节正文：{{chapter_text}}

只输出 JSON，严格遵循以下 Schema：
{
  "module": "章节摘要",
  "chapter": {{chapter_index}},
  "title": {{chapter_title}},
  "summary": "50字以内摘要",
  "key_events": ["事件1", "事件2"],
  "new_characters": [{"name": "...", "role": "...", "traits": ["..."]}],
  "character_updates": [{"name": "...", "change": "..."}],
  "emotional_tone": 0-10,
  "chapter_end_hook": "...或null",
  "foreshadowing_planted": ["..."],
  "foreshadowing_payoff": ["..."],
  "conflict_level": 0-10,
  "word_count": 数字
}
"""

AGGREGATE_PROMPT_GENRE = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"题材与定位"模块。

输入摘要：{{all_chapter_summaries}}
书籍元信息：{{book_meta}}

输出 JSON：
{"module":"题材与定位","genre":"...","target_audience":"...","market_position":"...","unique_selling_point":"..."}
"""

AGGREGATE_PROMPT_TITLE = """
你是一位小说拆解聚合架构师。基于全书章节摘要和书籍元信息，生成"书名与简介"模块。

输入摘要：{{all_chapter_summaries}}
书籍元信息：{{book_meta}}

输出 JSON：
{"module":"书名与简介","title_analysis":{"memorability":0-10,"relevance":0-10,"comment":"..."},"blurb_hooks":["..."],"blurb_effectiveness":"..."}
"""

AGGREGATE_PROMPT_WORLD = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"世界观设定"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"世界观设定","core_rules":"...","factions":[{"name":"...","desc":"...","chapters_involved":"第X-Y章"}],"unique_elements":["..."],"logic_consistency":"...","world_immersion_score":0-10}
"""

AGGREGATE_PROMPT_CHARACTER = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"人物图谱"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"人物图谱","protagonist":{"name":"...","desire":"...","flaw":"...","belief":"...","arc":"从...到..."},"supporting_chars":[{"name":"...","role":"...","relation_evolution":"..."}],"antagonist":{"name":"...","pressure_model":"...","motivation":"...","final_confrontation_chapter":null或数字},"relationship_map":"..."}
"""

AGGREGATE_PROMPT_STRUCTURE = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"主线与结构"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"主线与结构","main_storyline":"...","structure":{"acts":[{"act":"起/承/转/合","chapters":"第X-Y章","summary":"..."}]},"subplots":[{"name":"...","chapters":"...","resolution_chapter":"..."}],"structure_score":0-10}
"""

AGGREGATE_PROMPT_RHYTHM = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"节奏与爽点"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"节奏与爽点","climax_points":[{"chapter":数字,"type":"...","intensity":0-10}],"satisfaction_interval":"...","flat_periods":[{"chapters":"第X-Y章","desc":"..."}],"conflict_escalation":"...","emotion_curve":"...","rhythm_score":0-10}
"""

AGGREGATE_PROMPT_GIMMICK = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"金手指/核心创意"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"金手指/核心创意","unique_setting":"...","innovation":"...","core_gimmick":"...","creativity_score":0-10,"execution_score":0-10}
"""

AGGREGATE_PROMPT_OPENING = """
你是一位小说拆解聚合架构师。基于全书章节摘要（聚焦前3章），生成"开篇钩子"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"开篇钩子","first_500_words_hook":"...","protagonist_first_impression":"...","chapter_end_hooks":[{"chapter":数字,"hook":"...","strength":0-10}],"golden_three_effectiveness":"...","drop_off_risk":"..."}
"""

AGGREGATE_PROMPT_FORESHADOW = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"伏笔与铺垫"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"伏笔与铺垫","foreshadowing_list":[{"id":1,"plant_chapter":数字,"payoff_chapter":数字或null,"description":"...","function":"...","is_resolved":true/false}],"information_asymmetry":"...","continuity":"...","foreshadowing_score":0-10}
"""

AGGREGATE_PROMPT_PATTERN = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"可迁移模式"模块。

输入摘要：{{all_chapter_summaries}}

输出 JSON：
{"module":"可迁移模式","structural_patterns":["..."],"character_patterns":["..."],"rhythm_patterns":["..."],"writing_techniques":["..."],"imitation_advice":"..."}
"""

AGGREGATE_PROMPT_MAP: Dict[str, str] = {
    "题材与定位": AGGREGATE_PROMPT_GENRE,
    "书名与简介": AGGREGATE_PROMPT_TITLE,
    "世界观设定": AGGREGATE_PROMPT_WORLD,
    "人物图谱": AGGREGATE_PROMPT_CHARACTER,
    "主线与结构": AGGREGATE_PROMPT_STRUCTURE,
    "节奏与爽点": AGGREGATE_PROMPT_RHYTHM,
    "金手指/核心创意": AGGREGATE_PROMPT_GIMMICK,
    "开篇钩子": AGGREGATE_PROMPT_OPENING,
    "伏笔与铺垫": AGGREGATE_PROMPT_FORESHADOW,
    "可迁移模式": AGGREGATE_PROMPT_PATTERN,
}


# ============================================================
#  5. BaseAgent + 具体Agent实现
# ============================================================

class BaseAgent(ABC):
    """所有Agent的抽象基类，封装LLM调用"""
    
    def __init__(self, model: str = MODEL, temperature: float = 0.3):
        self.model = model
        self.temperature = temperature

    @abstractmethod
    def get_system_prompt(self) -> str:
        """返回系统提示词（纯角色指令，不含变量占位符）"""
        pass

    @abstractmethod
    def get_response_model(self) -> Type[BaseModel]:
        """返回 Pydantic 响应模型"""
        pass

    def render_user_content(self, **kwargs) -> str:
        """
        渲染用户内容（将变量注入提示词模板）。
        子类可重写此方法实现自定义渲染逻辑。
        """
        prompt_template = self.get_user_prompt_template()
        for key, value in kwargs.items():
            if isinstance(value, (dict, list)):
                prompt_template = prompt_template.replace(
                    f"{{{{{key}}}}}", json.dumps(value, ensure_ascii=False)
                )
            else:
                prompt_template = prompt_template.replace(
                    f"{{{{{key}}}}}", str(value)
                )
        return prompt_template

    @abstractmethod
    def get_user_prompt_template(self) -> str:
        """返回用户提示词模板（含 {{变量}} 占位符）"""
        pass

    def call(self, **kwargs) -> BaseModel:
        """
        执行LLM调用，返回结构化结果。
        kwargs 会传递给 render_user_content。
        """
        user_content = self.render_user_content(**kwargs)
        system_prompt = self.get_system_prompt()
        response_model = self.get_response_model()

        with rate_limiter:
            try:
                completion = client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format=response_model,
                    temperature=self.temperature,
                )
                return completion.choices[0].message.parsed
            except Exception as e:
                print(f"⚠️ 结构化解析失败，尝试降级: {e}")
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=self.temperature,
                )
                raw = resp.choices[0].message.content
                return response_model.model_validate_json(raw)


# ---------- 阶段一：章节扫描Agent ----------
class ChapterScanAgent(BaseAgent):
    """单章扫描Agent"""
    
    def get_system_prompt(self) -> str:
        return "你是一位小说章节扫描分析师。只输出 JSON。"
    
    def get_user_prompt_template(self) -> str:
        return SCAN_PROMPT
    
    def get_response_model(self) -> Type[BaseModel]:
        return ChapterSummary
    
    def scan_chapter(self, chapter_index: int, chapter_title: str, chapter_text: str) -> ChapterSummary:
        """扫描单章，返回结构化摘要"""
        return self.call(
            chapter_index=chapter_index,
            chapter_title=json.dumps(chapter_title, ensure_ascii=False),
            chapter_text=chapter_text,
        )


# ---------- 阶段二：模块聚合Agent基类 ----------
class BaseAggregateAgent(BaseAgent):
    """聚合Agent的基类，提供公共渲染逻辑"""
    
    def __init__(self, book_meta: Dict[str, str], model: str = MODEL, temperature: float = 0.3):
        super().__init__(model, temperature)
        self.book_meta = book_meta
    
    def render_user_content(self, summaries: List[ChapterSummary], **kwargs) -> str:
        """将摘要数组和元信息注入提示词"""
        summaries_json = json.dumps([s.model_dump() for s in summaries], ensure_ascii=False)
        book_meta_json = json.dumps(self.book_meta, ensure_ascii=False)
        prompt = self.get_user_prompt_template()
        return prompt.replace("{{all_chapter_summaries}}", summaries_json)\
                     .replace("{{book_meta}}", book_meta_json)
    
    def call(self, summaries: List[ChapterSummary], **kwargs) -> BaseModel:
        """重写call，传入summaries"""
        return super().call(summaries=summaries, **kwargs)


# ---------- 10个模块聚合Agent ----------
class GenreAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_GENRE
    def get_response_model(self) -> Type[BaseModel]:
        return GenreModule


class TitleAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_TITLE
    def get_response_model(self) -> Type[BaseModel]:
        return TitleModule


class WorldAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_WORLD
    def get_response_model(self) -> Type[BaseModel]:
        return WorldModule


class CharacterAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_CHARACTER
    def get_response_model(self) -> Type[BaseModel]:
        return CharacterModule


class StructureAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_STRUCTURE
    def get_response_model(self) -> Type[BaseModel]:
        return StructureModule


class RhythmAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_RHYTHM
    def get_response_model(self) -> Type[BaseModel]:
        return RhythmModule


class GimmickAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_GIMMICK
    def get_response_model(self) -> Type[BaseModel]:
        return GimmickModule


class OpeningAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_OPENING
    def get_response_model(self) -> Type[BaseModel]:
        return OpeningModule


class ForeshadowAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_FORESHADOW
    def get_response_model(self) -> Type[BaseModel]:
        return ForeshadowModule


class PatternAgent(BaseAggregateAgent):
    def get_system_prompt(self) -> str:
        return "你是一位小说拆解聚合架构师。只输出 JSON。"
    def get_user_prompt_template(self) -> str:
        return AGGREGATE_PROMPT_PATTERN
    def get_response_model(self) -> Type[BaseModel]:
        return PatternModule


# Agent 工厂映射
AGGREGATE_AGENT_MAP: Dict[str, Type[BaseAggregateAgent]] = {
    "题材与定位": GenreAgent,
    "书名与简介": TitleAgent,
    "世界观设定": WorldAgent,
    "人物图谱": CharacterAgent,
    "主线与结构": StructureAgent,
    "节奏与爽点": RhythmAgent,
    "金手指/核心创意": GimmickAgent,
    "开篇钩子": OpeningAgent,
    "伏笔与铺垫": ForeshadowAgent,
    "可迁移模式": PatternAgent,
}


# ============================================================
#  6. Orchestrator（流程编排器）
# ============================================================

class BookAnalyzerOrchestrator:
    """
    流程编排器：负责EPUB解析、阶段一并发调度、阶段二并发调度、
    结果汇总与报告保存。
    """
    
    def __init__(
        self,
        epub_path: str,
        book_meta: Optional[Dict[str, str]] = None,
        max_workers_stage1: int = 8,
        max_workers_stage2: int = 10,
    ):
        self.epub_path = epub_path
        self.book_meta = book_meta or {"book_title": None, "author": None, "blurb": None}
        self.max_workers_stage1 = max_workers_stage1
        self.max_workers_stage2 = max_workers_stage2
        
        self.chapters: List[Dict[str, Any]] = []
        self.summaries: List[ChapterSummary] = []
        self.report: Dict[str, Any] = {}
    
    # ---------- 阶段一：并行扫描 ----------
    def _scan_single_chapter(self, ch: Dict[str, Any]) -> ChapterSummary:
        """单章扫描任务（供线程池调用）"""
        agent = ChapterScanAgent()
        return agent.scan_chapter(
            chapter_index=ch["index"],
            chapter_title=ch["title"],
            chapter_text=ch["text"],
        )
    
    def _run_stage1(self, max_chapters: Optional[int] = None) -> None:
        """执行阶段一：并行扫描所有章节"""
        target = self.chapters
        if max_chapters:
            target = target[:max_chapters]
            print(f"⚠️ 仅处理前 {max_chapters} 章（限制模式）")
        
        print(f"🔍 阶段一：并行扫描（共 {len(target)} 章，并发数 {self.max_workers_stage1}）...")
        
        futures = {}
        with ThreadPoolExecutor(max_workers=self.max_workers_stage1) as executor:
            for ch in target:
                future = executor.submit(self._scan_single_chapter, ch)
                futures[future] = ch["index"]
            
            results = {}
            with tqdm(total=len(futures), desc="扫描进度") as pbar:
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                    except Exception as e:
                        print(f"❌ 第 {idx} 章扫描失败: {e}")
                        results[idx] = ChapterSummary(
                            chapter=idx,
                            title=self.chapters[idx-1]["title"] if idx <= len(self.chapters) else None,
                            summary="扫描失败",
                            key_events=[],
                            new_characters=[],
                            character_updates=[],
                            emotional_tone=5,
                            chapter_end_hook=None,
                            foreshadowing_planted=[],
                            foreshadowing_payoff=[],
                            conflict_level=5,
                            word_count=len(self.chapters[idx-1]["text"]) if idx <= len(self.chapters) else 0,
                        )
                    pbar.update(1)
        
        self.summaries = [results[i] for i in sorted(results.keys())]
        print(f"✅ 阶段一完成，共 {len(self.summaries)} 份摘要")
    
    # ---------- 阶段二：并行聚合 ----------
    def _aggregate_single_module(self, mod_name: str) -> tuple:
        """单模块聚合任务（供线程池调用）"""
        agent_class = AGGREGATE_AGENT_MAP.get(mod_name)
        if not agent_class:
            return mod_name, {"error": f"未找到模块 {mod_name} 的Agent"}
        
        agent = agent_class(book_meta=self.book_meta)
        try:
            result = agent.call(summaries=self.summaries)
            return mod_name, result.model_dump()
        except Exception as e:
            return mod_name, {"error": str(e), "module": mod_name}
    
    def _run_stage2(self) -> None:
        """执行阶段二：并行聚合所有模块"""
        if not self.summaries:
            print("❌ 错误：没有章节摘要，请先执行阶段一")
            return
        
        module_names = list(AGGREGATE_AGENT_MAP.keys())
        print(f"🧩 阶段二：并行聚合（共 {len(module_names)} 个模块，并发数 {self.max_workers_stage2}）...")
        
        futures = {}
        with ThreadPoolExecutor(max_workers=self.max_workers_stage2) as executor:
            for mod_name in module_names:
                future = executor.submit(self._aggregate_single_module, mod_name)
                futures[future] = mod_name
            
            with tqdm(total=len(futures), desc="聚合进度") as pbar:
                for future in as_completed(futures):
                    mod_name = futures[future]
                    try:
                        mod_name_result, data = future.result()
                        self.report[mod_name_result] = data
                    except Exception as e:
                        print(f"❌ 模块 [{mod_name}] 聚合失败: {e}")
                        self.report[mod_name] = {"error": str(e), "module": mod_name}
                    pbar.update(1)
        
        print("✅ 阶段二完成，全部模块聚合完毕")
    
    # ---------- 公共方法 ----------
    def load_chapters(self) -> None:
        """加载并解析EPUB"""
        print(f"📖 正在解析 EPUB: {self.epub_path}")
        self.chapters = extract_text_from_epub(self.epub_path)
        print(f"✅ 共解析出 {len(self.chapters)} 章")
    
    def save_report(self, output_path: str = "book_analysis_report.json") -> None:
        """保存最终报告"""
        final_output = {
            "book_meta": self.book_meta,
            "total_chapters": len(self.summaries),
            "chapter_summaries": [s.model_dump() for s in self.summaries],
            "modules": self.report,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_output, f, ensure_ascii=False, indent=2)
        print(f"💾 完整报告已保存至: {output_path}")
    
    def run_full_pipeline(
        self,
        max_chapters: Optional[int] = None,
        output_path: str = "book_analysis_report.json",
    ) -> Dict[str, Any]:
        """全自动执行完整流程"""
        self.load_chapters()
        self._run_stage1(max_chapters=max_chapters)
        self._run_stage2()
        self.save_report(output_path)
        return {
            "chapter_summaries": [s.model_dump() for s in self.summaries],
            "modules": self.report,
        }


# ============================================================
#  7. 主入口
# ============================================================

if __name__ == "__main__":
    book_meta = {
        "book_title": "诡秘之主",
        "author": "爱潜水的乌贼",
        "blurb": "穿越到蒸汽与机械的诡异世界，成为占卜家..."
    }

    orchestrator = BookAnalyzerOrchestrator(
        epub_path="./books/example.epub",  # 替换为你的epub路径
        book_meta=book_meta,
        max_workers_stage1=8,
        max_workers_stage2=10,
    )

    result = orchestrator.run_full_pipeline(
        max_chapters=30,  # 测试时限制，生产设为 None
        output_path="./report.json",
    )

    print("\n🎉 拆解完成！")
    print(f"已生成 {len(result['modules'])} 个模块")
    for mod_name in result["modules"].keys():
        print(f"  - {mod_name}")