# 小说拆书工具（Novel-Splitter）概要设计文档

## 1. 系统概述

### 1.1 目标

将海量小说文本自动拆解为结构化数据，为后续微调写作大模型提供高质量训练语料。核心能力：

- **逐章滚动处理**：突破模型上下文限制，用外部状态维持全书一致性
- **结构化提取**：从每章提取摘要、场景、角色、设定、伏笔、时间线
- **训练对生成**：自动构造“骨架→正文”“描述→场景”等微调样本
- **增量可恢复**：支持断点续跑，避免长任务失败重头再来

### 1.2 设计原则

| 原则 | 说明 |
|---|---|
| 状态外置 | 全书一致性靠 `state.json`，不靠模型上下文 |
| 单章处理 | 每次 LLM 调用只喂“压缩状态 + 当前章” |
| 幂等可恢复 | 每章独立产物，失败可单独重跑 |
| 分层降级 | 长章自动切场景；状态过长自动压缩/检索 |
| 人工可介入 | 冲突标记 + 抽检接口，不追求全自动 |

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI 层 (click/typer)                  │
│   split / resume / status / export / inspect / validate     │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                      编排层 (Pipeline)                       │
│  ChapterLoop → ExtractStage → MergeStage → PersistStage     │
└───────┬──────────────┬──────────────┬──────────────┬────────┘
        │              │              │              │
┌───────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌────▼───────┐
│  预处理模块   │ │  提取模块   │ │  状态模块   │ │  导出模块   │
│ Preprocessor │ │ Extractor  │ │ StateMgr   │ │ Exporter   │
│ 切章/切场景   │ │ LLM调用     │ │ 合并/压缩   │ │ JSONL输出  │
└───────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────┬───────┘
        │              │              │              │
┌───────▼──────────────▼──────────────▼──────────────▼───────┐
│                    基础设施层 (Infra)                        │
│  LLM Client │ Prompt Store │ Vector Store │ Logger │ Cache  │
└─────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                      持久化层 (Storage)                      │
│  state.json │ chapters/*.json │ outputs/*.jsonl │ logs/     │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 模块设计

### 3.1 预处理模块（Preprocessor）

**职责**：把原始小说变成可逐章处理的干净章节。

| 功能 | 说明 |
|---|---|
| 格式解析 | 支持 txt / epub / md，统一转纯文本 |
| 章节切分 | 正则匹配“第X章/卷/节/Chapter X”，失败时按长度兜底切分 |
| 场景切分 | 章内按空行、分隔符或 LLM 辅助切成 2~5 个场景块 |
| 长度控制 | 单块超过 `max_chunk_tokens` 时二次切分，相邻块重叠 200~500 字 |
| 清洗 | 去页眉页脚、广告、乱码、多余空白 |
| 元信息抽取 | 标题、作者、章节数、总字数 |

**输出**：`chapters/ch_{id}.json`，含 `id, title, blocks[], char_count`。

### 3.2 提取模块（Extractor）

**职责**：调用 LLM，从单章（或单场景块）提取结构化信息。

**输入组装**：
```
[System] 提取指令（固定）
[User]   压缩全局状态 + 当前章正文 + 输出 schema
```

**输出字段**（JSON Schema 固定）：

```json
{
  "chapter_id": 12,
  "chapter_summary": "200字以内",
  "scenes": [{"loc":"","chars":[],"goal":"","conflict":"","result":""}],
  "characters": [{"name":"","aliases":[],"change":"","status":""}],
  "settings": [{"key":"","value":"","change_type":"new/update"}],
  "foreshadowing": [{"id":"","desc":"","action":"plant/resolve"}],
  "timeline": [{"event":"","time_hint":""}],
  "training_pairs": [{"task":"skeleton2text","input":"","output":""}],
  "conflict": false
}
```

**关键机制**：

| 机制 | 说明 |
|---|---|
| JSON 模式 | 强制 `response_format=json`，降低解析失败 |
| 低温度 | temperature=0.1~0.3，保证稳定 |
| 重试 | 解析失败重试 2~3 次，指数退避 |
| 分块合并 | 长章分块提取后，再用一次 LLM 合并为章级结果 |
| 缓存 | 以 `hash(chapter_text + prompt_version)` 为 key，避免重复调用 |

### 3.3 状态模块（StateManager）

**职责**：维护并更新全局状态 `state.json`，这是全书一致性的核心。

**状态结构**：

```json
{
  "book_meta": {"title":"","genre":"","main_plot":""},
  "rolling_summary": "前情提要，≤500字",
  "characters": [{"name":"","aliases":[],"role":"","status":"","last_seen":12,"arc":""}],
  "locations": [],
  "items": [],
  "timeline": [],
  "open_threads": [{"id":"","desc":"","planted_ch":3,"status":"open"}],
  "chapter_summaries": [{"ch":1,"summary":"","detail":""}]
}
```

**更新流程**：

```
旧状态 + 本章提取结果 → LLM 合并 → 新状态
                          ↓
              规则校验（角色去重、伏笔状态机）
                          ↓
              压缩（早期章节摘要降级为一句）
                          ↓
              持久化 state.json + 快照
```

**压缩策略**：

- 最近 3 章保留详细摘要，更早只留一句话
- 角色只保留主要角色 + 当前章相关角色（按 `last_seen` 和出场频次）
- 已回收伏笔移入历史，不注入下一章提示词
- 状态超过 `state_budget_tokens` 时，对 `characters/settings` 做向量检索注入

### 3.4 检索模块（Retriever，可选）

当状态过大时启用：

- 把角色卡、设定、伏笔写入向量库（如 Chroma/FAISS）
- 处理当前章时，用章节摘要做 query，召回 Top-K 相关条目
- 只把召回的条目注入提示词，替代全量状态

### 3.5 导出模块（Exporter）

**职责**：把训练对导出为微调可用格式。

| 格式 | 用途 |
|---|---|
| JSONL (ChatML) | Qwen 系列 SFT 微调 |
| JSONL (preference) | ORPO 偏好对（好/差输出） |
| 结构化 JSON | 人工审阅、二次加工 |
| Markdown 摘要 | 书籍大纲、角色谱系 |

**ChatML 样例**：
```json
{"messages":[
  {"role":"system","content":"你是一位中文网文写作助手"},
  {"role":"user","content":"任务：正文增强\n输入：{skeleton}"},
  {"role":"assistant","content":"{original_text}"}
]}
```

### 3.6 基础设施（Infra）

| 组件 | 说明 |
|---|---|
| LLM Client | 统一接口，支持 OpenAI / Anthropic / 本地 vLLM / Ollama，可配置 fallback |
| Prompt Store | 提示词模板集中管理，带版本号，便于 A/B 与回溯 |
| Logger | 结构化日志，记录每章耗时、token、重试、冲突 |
| Cache | 本地文件缓存（sqlite/parquet），按 hash 命中 |
| Config | YAML 配置 + 环境变量覆盖 |

---

## 4. 数据流与文件布局

```
project/
├── config.yaml
├── data/
│   ├── raw/novel.txt
│   ├── chapters/
│   │   ├── ch_0001.json
│   │   └── ...
│   ├── state/
│   │   ├── global_state.json
│   │   └── snapshots/ch_0100.json
│   ├── outputs/
│   │   ├── training_pairs.jsonl
│   │   ├── summaries.md
│   │   └── review_queue.jsonl
│   └── cache/
└── logs/
    └── run_20261011.log
```

---

## 5. CLI 接口设计

```bash
# 1. 预处理：切章 + 切场景
novel-split preprocess --input data/raw/novel.txt --out data/chapters/

# 2. 拆书主流程（逐章提取 + 状态滚动）
novel-split split --config config.yaml

# 3. 断点续跑（从上次中断处继续）
novel-split resume --config config.yaml

# 4. 指定章节范围重跑
novel-split split --from 100 --to 150 --force

# 5. 查看进度与状态
novel-split status
novel-split inspect --chapter 42

# 6. 导出训练数据
novel-split export --format chatml --out data/outputs/train.jsonl
novel-split export --format preference --out data/outputs/orpo.jsonl

# 7. 一致性校验与人工抽检
novel-split validate --sample 20
novel-split review --conflicts-only
```

---

## 6. 核心流程伪代码

```python
def run_pipeline(cfg):
    chapters = load_chapters(cfg.data_dir)
    state = load_or_init_state(cfg.state_path)
    
    for ch in chapters:
        if ch.id < cfg.start_from or already_done(ch, cfg):
            continue
        
        try:
            # 1. 组装上下文
            ctx = build_context(state, ch, cfg)
            
            # 2. 提取（含长章分块）
            if ch.token_count > cfg.max_chunk_tokens:
                blocks = ch.blocks
                partials = [extract(block, ctx) for block in blocks]
                result = merge_partials(partials, ch)
            else:
                result = extract(ch, ctx)
            
            # 3. 冲突检测
            if detect_conflict(state, result):
                result["conflict"] = True
                enqueue_review(ch, result)
            
            # 4. 状态合并
            state = merge_state(state, result)
            
            # 5. 持久化
            save_json(f"chapters/ch_{ch.id}.json", result)
            save_json("state/global_state.json", state)
            if ch.id % cfg.snapshot_every == 0:
                save_snapshot(state, ch.id)
            
            log_success(ch, result)
        
        except Exception as e:
            log_error(ch, e)
            if cfg.fail_fast:
                raise
            continue
    
    export_training_pairs(cfg)
```

---

## 7. 配置设计（config.yaml）

```yaml
llm:
  provider: openai          # openai / anthropic / vllm / ollama
  model: gpt-4o-mini
  temperature: 0.2
  max_tokens: 4096
  timeout: 120
  retry: 3
  fallback:
    provider: ollama
    model: qwen2.5:14b

preprocess:
  min_chapter_chars: 800
  max_chunk_tokens: 3000
  overlap_tokens: 300
  chapter_pattern: "^第[零一二三四五六七八九十百千万\\d]+[章节卷]"

state:
  state_budget_tokens: 2500
  recent_chapters_detailed: 3
  enable_vector_retrieval: true
  vector_top_k: 8

pipeline:
  snapshot_every: 50
  fail_fast: false
  concurrency: 4            # 并发处理章数（注意状态顺序依赖）
  cache_enabled: true

export:
  formats: [chatml, preference, markdown]
  min_output_chars: 120
```

---

## 8. 错误处理与一致性保障

| 场景 | 策略 |
|---|---|
| LLM 返回非 JSON | 正则兜底 + 重试 + 降级到 fallback 模型 |
| 长章切块失败 | 按固定 token 强切，标记 `low_quality` |
| 状态合并冲突 | 标记 `conflict=true`，写入 review 队列，不阻断流程 |
| 专有名词被改写 | 提示词约束 + 后处理替换表（从 state 里的 aliases 生成） |
| 中途崩溃 | 章级 checkpoint，`resume` 从最后成功章继续 |
| 并发写状态冲突 | 状态更新串行；仅提取阶段可并发 |

---

## 9. 质量保障机制

1. **抽检**：每 20~50 章随机抽 1 章，人工核对角色/伏笔/时间线
2. **一致性校验**：脚本比对 state 中角色出现章节与原文实际出现章节
3. **训练对过滤**：导出前按长度、重复率、专有名词一致性过滤
4. **提示词版本管理**：每次改 prompt 记版本号，训练对中保留版本便于回溯
5. **成本与耗时统计**：记录每章 token 消耗、耗时、重试次数

---

## 10. 技术选型建议

| 层 | 推荐 |
|---|---|
| CLI | `typer` + `rich` |
| 配置 | `pydantic-settings` + YAML |
| LLM 调用 | `openai` SDK / `litellm`（统一多provider） |
| JSON 校验 | `pydantic` + `jsonschema` |
| 向量库 | `chromadb` 或 `faiss` |
| 缓存 | `diskcache` 或 sqlite |
| 并发 | `asyncio` + `httpx`（提取阶段），状态更新串行 |
| 日志 | `loguru` 或标准 `logging` + JSON formatter |
| 打包 | `uv` / `poetry`，入口 `novel-split` |

---

## 11. 可扩展方向

- **多书并行**：每本书独立 state，共享 LLM 客户端与缓存
- **增量导入**：新书加入时复用 prompt 与 pipeline
- **偏好对生成**：同一 skeleton 用不同温度生成多版本，人工/规则打分构造 ORPO 数据
- **风格迁移**：在导出阶段按风格标签筛选训练对
- **Web 审阅界面**：把 review 队列可视化，支持在线修改后回写 state

---

## 12. 里程碑建议

| 阶段 | 目标 | 交付 |
|---|---|---|
| M1 | 打通单章提取 | preprocess + extract + 单章 JSON 输出 |
| M2 | 状态滚动 | StateManager + resume + snapshot |
| M3 | 长章与一致性 | 分块合并 + 冲突检测 + review 队列 |
| M4 | 导出与微调对接 | Exporter + ChatML/ORPO 格式 |
| M5 | 质量与规模化 | 检索、并发、成本统计、抽检工具 |

---

**一句话总结**：这个 CLI 工具的核心是 **“预处理 → 逐章提取 → 状态滚动合并 → 导出”** 四段式流水线，用外部 `state.json` 弥补模型上下文不足，用章级 checkpoint 保证长任务可恢复，最终产出可直接用于 Qwen-27B SFT/ORPO 的高质量训练对。