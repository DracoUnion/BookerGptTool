# BookerGptTool

BookerGptTool 是一个基于 OpenAI 兼容 API 的命令行工具集，当前提供模型调用、批量检查、自由推理和 API 转发服务。

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

`OPENAI_BASE_URL` 可替换为其他 OpenAI 兼容服务的地址。

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
| `-r, --retry` | 重试次数 | `1000000` |
| `-tm, --temp` | temperature | `1` |
| `-mt, --max-tokens` | 最大输出 token 数 | 不限制 |
| `-ST, --no-stream` | 关闭流式输出 | 关闭 |
| `-ua, --user-agent` | HTTP User-Agent | `claude-cli/2.1.41 (external, cli)` |
| `-ct, --conn-timeout` | 连接超时秒数 | `60` |
| `-rt, --read-timeout` | 读取超时秒数 | `120` |
| `-TN, --no-think` | 关闭思考模式 | 关闭 |

## 子命令

### `call`：自定义问题调用模型

向 OpenAI 兼容 API 发送一条自定义问题，并输出模型回答：

```bash
gpt-tool call "请总结这段文字的要点"

gpt-tool --model gpt-4o-mini --retry 3 call "解释什么是向量数据库"
```

### `check-batch`：检查 YAML 中的 key

检查 YAML 文件中的键，适合在批量处理前进行结构校验：

```bash
gpt-tool check-batch questions.yaml
```

### `infer`：自由推理

读取问题文件并执行自由推理：

```bash
gpt-tool infer questions.yaml
```

使用 `gpt-tool infer -h` 查看输入格式和可用参数。

### `forward`：OpenAI API 转发服务

启动 OpenAI 兼容 API 转发服务：

```bash
gpt-tool forward keys.yaml
```

使用 `gpt-tool forward -h` 查看监听地址、密钥文件和服务配置选项。

## 常见问题

### 请求失败或认证错误

检查 `OPENAI_API_KEY` 是否有效，且 `OPENAI_BASE_URL` 是否包含正确的 API 路径。也可以通过命令行参数临时覆盖：

```bash
gpt-tool -k "$OPENAI_API_KEY" -H "https://example.com/v1" call "你好"
```

### 不知道某个命令有哪些参数

每个子命令都提供独立帮助：

```bash
gpt-tool call -h
gpt-tool check-batch -h
gpt-tool infer -h
gpt-tool forward -h
```

## 开发

```bash
git clone https://github.com/DracoUnion/BookerGptTool.git
cd BookerGptTool
pip install -e .
```

项目主页：<https://github.com/DracoUnion/BookerGptTool>
