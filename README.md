# BookerGptTool

BookerGptTool 是一个基于 OpenAI 兼容 API 的命令行工具集，当前保留翻译、文本调用、推理与生词处理等核心命令。

> **迁移说明**：其他子命令已迁移到独立的兄弟项目：
>
> - 代码与文档：`code2doc`、`code2book`、`re` → `../BookerCodeTool`
> - Markdown/知识库：`clean-heading`、`erchuang`、`md2kg`、`md2skill`、`fmt-chunk`、`md2wiki`、`resplit`、`trans-chunk`、`derep` → `../BookerMarkdownTool`
> - 媒体内容：`xhs-img`、`article-img`、`ppt`、`xhs-art`、`jike-art`、`podcast`、`gzh-art`、`zhanshimian` → `../BookerMediaTool`
> - 论文与科研：`paper2textbook`、`any2textbook`、`paper2code`、`auto-research` → `../BookerPaperTool`
> - 游戏与微信自动化：`play-game`、`play-android-game`、`play-game-chrome`、`wx-auto-reply` → `../BookerAutoTool`
> - 下载与采集：`bili-ai-proj`、`zhihu-radar`、`wx-summary` → `../BookerDownloadTool`
> - 其他：`pdf-ocr` → `../BookerPdfTool`，`forward` → `../BookerProxyTool`，`trans-epub` → `../BookerEpubTool`，`fin-report` → `../BookerFinReportTool`

## 安装

推荐使用 Python 3.8 或更高版本（项目声明支持 Python 3.6+）：

```bash
pip install git+https://github.com/DracoUnion/BookerGptTool.git
```

也可以在源码目录中安装：

```bash
pip install .
```

安装后提供两个等价的命令：`gpt-tool` 和 `BookerGptTool`。

## 配置 API

工具通过 OpenAI 兼容接口访问大模型。至少需要设置 API Key：

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_CHAT_MODEL="gpt-4o-mini"
```

Windows PowerShell：

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_CHAT_MODEL = "gpt-4o-mini"
```

`OPENAI_BASE_URL` 可替换为其他 OpenAI 兼容服务的地址。视觉任务可额外配置：

```bash
export OPENAI_VIS_MODEL="视觉模型名称"
export OPENAI_TTI_MODEL="文生图模型名称"
export EMB_MODEL_PATH="moka-ai/m3e-base"
```

不要把 API Key 提交到 Git 仓库或写入公开脚本中。

## 基本用法

查看帮助和版本：

```bash
gpt-tool -h
gpt-tool --version
gpt-tool <子命令> -h
```

通用参数应放在子命令之前，例如：

```bash
gpt-tool --model gpt-4o-mini --retry 3 call "解释什么是向量数据库"
```

常用全局参数：

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `-m, --model` | 聊天模型名称 | `OPENAI_CHAT_MODEL`，未设置时为 `gpt-3.5-turbo` |
| `-k, --key` | OpenAI API Key | `OPENAI_API_KEY` |
| `-H, --host` | API 地址 | `OPENAI_BASE_URL` |
| `-vm, --vmodel` | 视觉模型名称 | `OPENAI_VIS_MODEL` |
| `-im, --tti-model` | 文生图模型名称 | `OPENAI_TTI_MODEL` |
| `-r, --retry` | 重试次数 | `1000000` |
| `-tm, --temp` | temperature | `1` |
| `-mt, --max-tokens` | 最大输出 token 数 | 不限制 |
| `-st, --stream` | 启用流式输出 | 关闭 |
| `--emb` | Embedding 模型路径 | `moka-ai/m3e-base` |

## 子命令

### 翻译与文本生成

```bash
# 翻译一句话
gpt-tool trans "Hello, world!"

# 翻译 YAML 文件
gpt-tool trans-yaml input.yaml

# 使用自定义问题调用模型
gpt-tool call "请总结这段文字的要点"

# 根据表格中的问题进行推理
gpt-tool infer questions.yaml
```

`trans-yaml` 和 `infer` 支持 `--threads`、`--limit` 等参数；使用子命令帮助查看完整选项。

### 生词处理

```bash
# 解析 EPUB 中的生词/词汇分享表
gpt-tool shengcai book.epub
```

## 文件与输出说明

需要文件路径的命令通常会在输入文件旁边生成结果，具体输出位置以命令帮助或运行日志为准。

## 常见问题

### 请求失败或认证错误

检查 `OPENAI_API_KEY` 是否有效，且 `OPENAI_BASE_URL` 是否包含正确的 API 路径。也可以通过命令行参数临时覆盖：

```bash
gpt-tool -k "$OPENAI_API_KEY" -H "https://example.com/v1" call "你好"
```

### 不知道某个命令有哪些参数

每个子命令都提供独立帮助：

```bash
gpt-tool trans -h
gpt-tool shengcai -h
gpt-tool call -h
gpt-tool infer -h
```

## 开发

```bash
git clone https://github.com/DracoUnion/BookerGptTool.git
cd BookerGptTool
pip install -e .
```

项目主页：<https://github.com/DracoUnion/BookerGptTool>
