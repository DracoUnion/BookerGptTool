# BookerGptTool

BookerGptTool 是一个基于 OpenAI 兼容 API 的命令行工具集，用于翻译、文档处理、代码分析、PDF/EPUB 处理、知识库构建以及内容生成等任务。

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

### 代码与文档

```bash
# 为代码文件或目录添加注释
gpt-tool code2doc path/to/source.py

# 将项目整理为书籍
gpt-tool code2book path/to/project

# 清理 Markdown 标题
gpt-tool clean-heading document.md

# 将 Markdown 转换为知识图谱数据
gpt-tool md2kg document.md

# 将 Markdown 转换为技能定义或 Wiki
gpt-tool md2skill document.md
gpt-tool md2wiki document.md
```

### PDF、EPUB 与论文

```bash
# PDF OCR，生成可处理的文本/Markdown
gpt-tool pdf-ocr document.pdf

# OCR 后翻译并清理标题
gpt-tool pdf-ocr document.pdf --trans --clean

# 翻译 EPUB
gpt-tool trans-epub book.epub

# 格式化或修复文本块
gpt-tool fmt-chunk book.epub

# 分析 EPUB 小说
gpt-tool novel-anls book.epub --book-title "书名" --author "作者"

# 总结 arXiv 论文（ID 示例）
gpt-tool arxiv 2301.00001

# 批量总结 arXiv 论文
gpt-tool arxiv-batch arxiv_ids.txt

# 从论文、Markdown、TEX 或 TXT 生成代码方案
gpt-tool paper2code paper.pdf -o output

# 生成财务报告
gpt-tool fin-report report.pdf
```

### 内容创作与其他工具

```bash
# 生成小红书/公众号等风格的内容
gpt-tool erchuang article.txt --style xhs

# 生成笔记
gpt-tool note article.md

# 生成小说
gpt-tool gts-fiction "一个关于时间旅行的悬疑故事"

# 解析 EPUB 中的生词/词汇分享表
gpt-tool shengcai book.epub

# 启动 OpenAI API 转发服务
gpt-tool forward keys.yaml
```

小红书信息图卡片生成器：

```bash
gpt-tool xhs-img outline.md --preset <预设名称> --count 5 -o output
```

可用选项包括 `--style`、`--layout`、`--palette`、`--preset`、`--count`、`--output-dir` 和 `--strategy`。输入文件使用 `-` 时可从标准输入读取：

```bash
cat outline.md | gpt-tool xhs-img - --count 4
```

## 文件与输出说明

- 需要文件路径的命令通常会在输入文件旁边生成结果，具体输出位置以命令帮助或运行日志为准。
- `code2doc`、`code2book`、`pdf-ocr`、`trans-epub` 等命令可能递归处理目录或生成多个文件；处理大量文件时可通过 `--threads`、`--file-threads`、`--page-threads` 调整并发度。
- 首次使用 Embedding 相关功能时，模型路径可能触发下载，请确保网络和磁盘空间可用。

## 常见问题

### 请求失败或认证错误

检查 `OPENAI_API_KEY` 是否有效，且 `OPENAI_BASE_URL` 是否包含正确的 API 路径。也可以通过命令行参数临时覆盖：

```bash
gpt-tool -k "$OPENAI_API_KEY" -H "https://example.com/v1" call "你好"
```

### 不知道某个命令有哪些参数

每个子命令都提供独立帮助：

```bash
gpt-tool pdf-ocr -h
gpt-tool trans-epub -h
gpt-tool xhs-img -h
```

## 开发

```bash
git clone https://github.com/DracoUnion/BookerGptTool.git
cd BookerGptTool
pip install -e .
```

项目主页：<https://github.com/DracoUnion/BookerGptTool>
