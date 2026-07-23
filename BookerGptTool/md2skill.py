import torch
import requests
import tarfile
import numpy as np
from io import BytesIO
from os import path
import re
import os
import shutil
import yaml
import json_repair as json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import functools
from sentence_transformers import SentenceTransformer
from typing import Any, Dict, Optional, List, Callable
from .util import ask_chatgpt_retry, set_openai_props, ngram_jaccard, ext_code_block, ext_cont_block
from .md2skill_pmt import *
from .md2skill_gen import generate_claude_skills
from .md2skill_chunker import chunk_markdown
from .md2skill_models import BookSchema, RawSkill, ChunkSkill, SKUType

TYPE_PMT_MAP = {
    '技术手册': TECH_EXT_PMT,
    '叙事类': NARRATIVE_EXT_PMT,
    '方法论': METHOD_EXT_PMT,
    '学术教材': ACADEMIC_EXT_PMT,
    '保险合同': INSURANCE_EXT_PMT,
    '行业报告': REPORT_EXT_PMT,
    '医学法律': MED_LGL_EXT_PMT,
    '流程规范': PROC_EXT_PMT,
}

# 领域同义词库：值 → 标准化标签
DOMAIN_SYNONYMS: Dict[str, str] = {
    # 保险领域
    "保险": "保险", "insurance": "保险", "保障": "保险",
    "理赔": "保险·理赔", "赔付": "保险·理赔", "claims": "保险·理赔",
    "核保": "保险·核保", "承保": "保险·核保", "underwriting": "保险·核保",
    # 法律领域
    "法律": "法律", "法规": "法律", "legal": "法律", "法务": "法律",
    "合同": "法律·合同", "contract": "法律·合同", "条款": "法律·合同",
    # 技术领域
    "技术": "技术", "technology": "技术", "tech": "技术",
    "开发": "技术·开发", "编程": "技术·开发", "programming": "技术·开发",
    "运维": "技术·运维", "devops": "技术·运维", "ops": "技术·运维",
    # 医学领域
    "医学": "医学", "medical": "医学", "临床": "医学·临床",
    "药学": "医学·药学", "pharmacy": "医学·药学",
    # 金融领域
    "金融": "金融", "finance": "金融", "财务": "金融",
    "投资": "金融·投资", "investment": "金融·投资",
}


def parse_raw_skill(raw_skill: str) -> Optional[RawSkill]:
    """从 LLM 输出的 YAML Frontmatter + Markdown Body 中解析出 RawSkill。"""
    RE_RAW_SKILL = r'---\n([\s\S]+?)\n---\n([\s\S]+)'
    m = re.search(RE_RAW_SKILL, raw_skill)
    if not m: return None
    try:
        meta = yaml.safe_load(m.group(1))
    except:
        return None
    body = m.group(2)

    # 补全缺失字段
    if 'name' not in meta:
        first_line = body.split("\n")[0].strip("# ").strip()
        meta["name"] = re.sub(
            r"[^a-zA-Z0-9一-鿿]+", "-", first_line
        ).strip("-").lower()[:50]

    slug = _to_kebab(meta['name'])
    trigger = meta.pop('trigger', "通用知识查询")

    return RawSkill(
        name=meta['name'],
        slug=slug,
        trigger=trigger,
        domain=meta.get('domain', ''),
        body=body,
        raw_text=raw_skill,
        prerequisites=meta.get('prerequisites', []),
        source_ref=meta.get('source_ref', ''),
        confidence=meta.get('confidence', 0.0),
        characters=meta.get('characters', []),
        timeline=meta.get('timeline', ''),
        prompt_version=meta.get('prompt_version', ''),
    )


def normalize_skills_tags(skills: List[RawSkill]) -> Dict[str, str]:
    """批量归一化所有 Skill 的 domain 标签。返回原始 → 归一化的映射表。"""
    tag_map: Dict[str, str] = {}
    for skill in skills:
        ori = skill.domain
        norm = DOMAIN_SYNONYMS.get(ori.lower(), ori)
        if ori != norm:
            tag_map[ori] = norm
            skill.domain = norm
    return tag_map


def cluster_skills(
    skills: List[RawSkill],
    emb_model_name: str,
    threshold: float = 0.88
) -> List[List[RawSkill]]:
    normalize_skills_tags(skills)

    # 构建每个 Skill 的文本表示（trigger + body 前 500 字）
    texts = [
        f"{s.trigger} {s.body[:500]}"
        for s in skills
    ]

    '''
    st = SentenceTransformer(emb_model_name)
    st = torch.quantization.quantize_dynamic(
        st, {torch.nn.Linear}, dtype=torch.qint8)
    vectors = st.encode(texts)
    sims = st.similarity(vectors, vectors)
    '''

    # 贪心聚类
    used = [False] * len(skills)
    clusters = []

    for i in range(len(skills)):
        if used[i]:  continue
        cluster = [skills[i]]
        used[i] = True

        for j in range(i + 1, len(skills)):
            if used[j]:  continue
            sim = ngram_jaccard(texts[i], texts[j])
            if sim >= threshold:
                cluster.append(skills[j])
                used[j] = True

        clusters.append(cluster)

    return clusters


# 事实型关键词（高置信度）
_FACTUAL_PATTERNS = re.compile(
    r"档案|简历|生卒|籍贯|出生|家族|官职|品级|俸禄|"
    r"收支|账目|银两|数据|数量|人口|产量|价格|"
    r"地理|位置|建筑|结构|布局|面积|距离|"
    r"设定|背景|世界观|历史|年表|时间线|大事记|"
    r"物品|器物|武器|装备|规格|材质",
    re.IGNORECASE,
)

# 程序型关键词（高置信度）
_PROCEDURAL_PATTERNS = re.compile(
    r"流程|步骤|操作|程序|方法|策略|战术|技巧|"
    r"如何|怎样|应对|处理|执行|部署|实施|"
    r"决策|判断|选择|权衡|博弈|谈判|"
    r"IF.*THEN|前置条件|触发条件|预期结果|"
    r"第[一二三四五六七八九十]步|Step\s*\d",
    re.IGNORECASE,
)

# 关系型关键词（高置信度）
_RELATIONAL_PATTERNS = re.compile(
    r"关系|派系|阵营|联盟|对立|从属|"
    r"标签|分类|层级|等级|体系|谱系|"
    r"术语|定义|概念|词汇|名词解释|"
    r"网络|图谱|依赖|影响链|因果链",
    re.IGNORECASE,
)


def classify_skill(skill: RawSkill) -> SKUType:
    """对单个 Skill 进行 SKU 类型分类（纯规则）"""

    text = f"{skill.name} {skill.trigger} {skill.body[:500]}"
    scores = {
        SKUType.FACTUAL: len(_FACTUAL_PATTERNS.findall(text)),
        SKUType.PROCEDURAL: len(_PROCEDURAL_PATTERNS.findall(text)),
        SKUType.RELATIONAL: len(_RELATIONAL_PATTERNS.findall(text)),
    }
    max_type = max(scores, key=lambda k: scores[k])

    # 明确命中（最高分 >= 2 且领先第二名 >= 1）
    sorted_scores = sorted(scores.values(), reverse=True)
    if sorted_scores[0] >= 2 and sorted_scores[0] - sorted_scores[1] >= 1:
        return max_type

    # 弱信号时用启发式规则
    body = skill.body.lower()

    # 有编号步骤 → procedural
    if re.search(r"[1-9]\.", body):
        return SKUType.PROCEDURAL

    # 有箭头关系 → relational
    if "→" in body or "->" in body or "关系" in body:
        return SKUType.RELATIONAL

    # 默认 factual（事实类最通用）
    return SKUType.FACTUAL


def get_pmt_by_type(tp):
    """根据 book_type 解析出 prompt 模板"""
    # 精确匹配
    if tp in TYPE_PMT_MAP:
        return TYPE_PMT_MAP[tp]

    # 模糊匹配
    bt = tp.lower()
    if any(kw in bt for kw in ("叙事", "小说", "故事", "fiction", "narrative")):
        return TYPE_PMT_MAP["叙事类"]
    if any(kw in bt for kw in ("方法", "框架", "methodology", "framework")):
        return TYPE_PMT_MAP["方法论"]
    if any(kw in bt for kw in ("教材", "学术", "academic", "textbook")):
        return TYPE_PMT_MAP["学术教材"]
    if any(kw in bt for kw in ("保险", "保单", "保障", "理赔", "insurance")):
        return TYPE_PMT_MAP["保险合同"]
    if any(kw in bt for kw in ("报告", "研报", "白皮书", "report")):
        return TYPE_PMT_MAP["行业报告"]
    if any(kw in bt for kw in ("医学", "法律", "金融", "medical", "legal")):
        return TYPE_PMT_MAP["医学法律"]
    if any(kw in bt for kw in ("规范", "标准", "规程", "条例", "手册", "manual", "guide", "操作")):
        return TYPE_PMT_MAP["技术手册"]

    return DFT_EXT_PMT


def check_hallucination(
    body: str, source_text: str
) -> bool:
    """
    幻觉初筛：检查 body 中的关键术语是否在 source_text 中出现。

    策略：提取 body 中的非常见术语（> 2 字的中文词或英文词），
    检查其是否在原文中出现。超过 40% 的术语未出现则标记疑似幻觉。
    """

    # Skill 结构性停用词（R1 输出的格式标签，不属于幻觉）
    _CN_STOPWORDS = {
        "执行步骤", "输出格式", "格式要求", "输出格式要求", "前置条件",
        "触发条件", "判断条件", "操作步骤", "注意事项", "具体步骤",
        "排查步骤", "解决方案", "处理方法", "诊断步骤", "核心步骤",
        "检查项目", "原因分析", "结果输出", "结论建议", "适用场景",
        "原因为", "如适用", "事件详情", "配置项", "存在状态",
        "匹配情况", "检查上游", "列出调度", "资源使用", "解决办法",
    }
    _EN_STOPWORDS = {
        "this", "that", "with", "from", "your", "have", "will", "when",
        "null", "true", "false", "none", "else", "step", "then", "each",
        "following", "output", "input", "check", "verify", "ensure",
        "execute", "confirm", "should", "must", "below", "above",
        "format", "result", "trigger", "domain", "skill", "prerequisites",
    }

    # 提取 body 中的关键词（> 2 字中文词或英文单词）
    cn_terms = set(re.findall(r"[一-鿿]{3,}", body)) - _CN_STOPWORDS
    en_terms = set(
        w.lower()
        for w in re.findall(r"[A-Za-z]{4,}", body)
        if w.lower() not in _EN_STOPWORDS
    )

    all_terms = cn_terms | en_terms
    if len(all_terms) < 3:
        return True

    source_lower = source_text.lower()
    missing = {t for t in all_terms if t.lower() not in source_lower}

    miss_rate = len(missing) / len(all_terms)
    if miss_rate > 0.4:
        return False

    return True


def _to_kebab(name: str) -> str:
    """将技能名转为 kebab-case slug"""
    s = re.sub(r"[^\w\s一-鿿-]", "", name)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s[:60] or "unnamed-skill"


def ext_toc_preface(md, preface_len=3000):
    toc = '\n'.join(re.findall(r'^#+\s+.+?$', md, re.M))
    preface = md[:preface_len]
    if len(md) > preface_len:
        preface += '\n\n[正文省略...]'
    return toc, preface


class Md2SkillAgent:
    """封装所有 LLM 调用，一个方法对应一次调用。"""

    def __init__(self, model, args):
        self.model = model
        self.args = args

    def generate_schema(self, toc, preface) -> BookSchema:
        """Step 1: 从目录和前言推断知识结构 schema"""
        prompt = SCHEMA_PMT.replace('{toc}', toc) \
            .replace('{preface}', preface)
        parse_output = lambda s: BookSchema.model_validate(
            json.loads(ext_code_block(schema_raw)))
        schema_raw = ask_chatgpt_retry(prompt, self.model, self.args, parse_output)
        return schema_raw

    def generate_raw_skills(
        self, book_type: str, content: str, context: str
    ) -> List[RawSkill]:
        """Step 2: 从一个文本块中提取原始技能"""
        prompt = get_pmt_by_type(book_type) \
            .replace('{content}', content) \
            .replace('{context}', context)
        parse_output = lambda s: ext_cont_block(s).split('[split/]')
        raw_texts = ask_chatgpt_retry(prompt, self.model, self.args, parse_output)
        return [rs for rs in (parse_raw_skill(rt) for rt in raw_texts) if rs]

    def merge_cluster(self, cluster: List[RawSkill]) -> Optional[RawSkill]:
        """Step 3: 将相似技能集群合并为一个"""
        text = '\n\n[split/]\n\n'.join([s.raw_text for s in cluster])
        prompt = REDUCE_PMT.replace('{count}', str(len(cluster))) \
            .replace('{skills}', text)
        merged_text = ask_chatgpt_retry(prompt, self.model, self.args, ext_cont_block)
        return parse_raw_skill(merged_text)


class Md2SkillOrchestrator:
    """编排整个 md2skill 流程：文件缓存、步骤调度、线程池。"""

    def __init__(self, args):
        self.args = args
        self.agent = Md2SkillAgent(args.model, args)
        self.lock = Lock()

    def _write_yaml(self, fname, res):
        """线程安全写入 YAML（支持 Pydantic 对象和普通对象）。"""
        with self.lock:
            data = res.model_dump(mode="json") if hasattr(res, 'model_dump') else res
            with open(fname, 'w', encoding='utf8') as f:
                f.write(yaml.safe_dump(data, allow_unicode=True))

    def _load_yaml(self, fname) -> Any:
        """从文件加载 YAML。"""
        return yaml.safe_load(open(fname, encoding='utf8').read())

    def _load_or_generate_yaml(self, fname, generator_fn):
        """缓存模式：文件存在则直接读取，否则生成并保存。"""
        if path.isfile(fname):
            return self._load_yaml(fname)
        result = generator_fn()
        self._write_yaml(fname, result)
        return result

    def _step1_schema(self, md) -> BookSchema:
        print(f'[1] 生成 SCHEMA')
        schema_fname = path.join(self.output_dir, 'schema.yaml')

        if path.isfile(schema_fname):
            raw = self._load_yaml(schema_fname)
            return BookSchema.model_validate(raw)

        toc, preface = ext_toc_preface(md)
        schema = self.agent.generate_schema(toc, preface)
        self._write_yaml(schema_fname, schema)
        return schema

    def _step2_raw_skills(self, md, schema: BookSchema) -> List[ChunkSkill]:
        print(f'[2] 生成原始技能')
        raw_skill_fname = path.join(self.output_dir, 'raw_skills.yaml')

        if path.isfile(raw_skill_fname):
            raw = self._load_yaml(raw_skill_fname)
            return [ChunkSkill.model_validate(c) for c in raw]

        chunks = chunk_markdown(
            md, path.basename(self.args.fname)[:-3]).chunks
        chunk_skills = [
            ChunkSkill(content=c.content, context=c.context)
            for c in chunks
        ]
        self._write_yaml(raw_skill_fname, chunk_skills)

        pool = ThreadPoolExecutor(self.args.threads)
        hdls = []

        for i, cs in enumerate(chunk_skills):
            if cs.generated: continue
            h = pool.submit(
                self._gen_raw_skill,
                schema.book_type, chunk_skills, i,
                raw_skill_fname,
            )
            hdls.append(h)

        for h in hdls:
            h.result()

        return chunk_skills

    def _gen_raw_skill(
        self, book_type: str,
        chunk_skills: List[ChunkSkill], idx: int,
        raw_skill_fname: str,
    ):
        """线程内：生成单个 chunk 的原始技能并回写。"""
        cs = chunk_skills[idx]
        cs.raw_skills = self.agent.generate_raw_skills(
            book_type, cs.content, cs.context,
        )
        cs.generated = True
        for rs in cs.raw_skills:
            print(f'[2] {rs.name}')
        self._write_yaml(raw_skill_fname, chunk_skills)

    def _step3_clusters(self, chunk_skills: List[ChunkSkill]) -> List[List[RawSkill]]:
        print(f'[3] 原始技能聚类')
        clusters_fname = path.join(self.output_dir, 'clusters.yaml')

        def build():
            all_skills = []
            for cs in chunk_skills:
                all_skills.extend(cs.raw_skills)
            if not all_skills:
                print(f'[3] 未找到任何技能，无法聚类')
                return []
            return cluster_skills(all_skills, self.args.emb)

        if path.isfile(clusters_fname):
            raw = self._load_yaml(clusters_fname)
            return [[RawSkill.model_validate(s) for s in cluster] for cluster in raw]

        clusters = build()
        if clusters:
            self._write_yaml(clusters_fname, clusters)
        return clusters

    def _step4_skills(self, clusters: List[List[RawSkill]]) -> List[RawSkill]:
        print(f'[4] 技能分类')
        skills_fname = path.join(self.output_dir, 'skills.yaml')

        if path.isfile(skills_fname):
            raw = self._load_yaml(skills_fname)
            skills = [RawSkill.model_validate(s) for s in raw]
        else:
            skills: List[Optional[RawSkill]] = [None] * len(clusters)
            pool = ThreadPoolExecutor(self.args.threads)
            hdls = []

            for i, c in enumerate(clusters):
                if len(c) == 1:
                    skills[i] = c[0]
                    continue
                h = pool.submit(
                    self._merge_cluster, c, skills, i, skills_fname)
                hdls.append(h)

            for h in hdls:
                h.result()

            skills = [s for s in skills if s]
            self._write_yaml(skills_fname, skills)

        for s in skills:
            print(f"[4] {s.name}")
            if s.type: continue
            s.type = classify_skill(s).value
        self._write_yaml(skills_fname, skills)

        return skills

    def _merge_cluster(
        self, cluster: List[RawSkill],
        skills: List[Optional[RawSkill]],
        idx: int, skills_fname: str,
    ):
        """线程内：合并一个集群并回写。"""
        merged = self.agent.merge_cluster(cluster)
        if merged:
            skills[idx] = cluster[0].model_copy(update={
                'body': merged.body,
            })
            print(f'[3] {skills[idx].name}')
        self._write_yaml(skills_fname, skills)

    def _step5_package(self, skills: List[RawSkill]):
        zip_fname = self.args.fname[:-3] + '.zip'
        print(f'[5] 打包输出 {zip_fname}')
        generate_claude_skills(
            [s.model_dump() for s in skills], zip_fname)

    def run(self):
        print(self.args)

        if not self.args.fname.endswith('.md'):
            print('请提供 MD 文件')
            return

        md = open(self.args.fname, encoding='utf8').read()
        self.output_dir = self.args.fname[:-3] + '_md2skill'
        os.makedirs(self.output_dir, exist_ok=True)

        schema = self._step1_schema(md)
        chunk_skills = self._step2_raw_skills(md, schema)
        clusters = self._step3_clusters(chunk_skills)
        if not clusters:
            return
        skills = self._step4_skills(clusters)
        self._step5_package(skills)

        print('[*] 完成')


def md2skill(args):
    print(args)
    set_openai_props(args)
    orchestrator = Md2SkillOrchestrator(args)
    orchestrator.run()
