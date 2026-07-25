CLEAN_HEAD_PMT = '''
你是一位高级编辑和技术审校，你将得到一个 JSON 文本，其`lines`属性是 Markdown 文件的前几行， 你需要为每一行划分角色，包括书名/作者/出版社信息（`info`）、版权页（`copyright`）、目录（`toc`），前言（`preface`），关于作者/贡献者（`about`）正文（`body`）和其他（`etc`），按指定 JSON 格式输出，包含在三个反引号（```）中，其中`no`为行号，`role`该行角色，行号应从 0 开始。

## 输出格式

```
[
	{"no": 123, "role": "info|copyright|toc|preface|about|body|etc"},
	{ ... }
]
```

## 待处理文本

```
{text}
```
'''