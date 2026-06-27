README_TMPL = '''
# {name_cn}

> 原文：[{name}]()
> 
> 译者：[飞龙](https://github.com/wizardforcel)
> 
> 协议：[CC BY-NC-SA 4.0](http://creativecommons.org/licenses/by-nc-sa/4.0/)
'''.strip()

########################################################

OCR_PMT = '''
你是一个专业的文档编辑助手。请查看给定图片，提取出其中的所有标题、段落、列表、表格、插图、引用、代码块等，并一定要忽略页眉和页脚，以给定 JSON  格式输出。注意只需要输出 JSON，不需要输出其它任何东西。

注意 BBOX 应该用相对坐标，即整个图片的一个比例，不要使用像素单位的绝对坐标！

JSON 应包含在三个反引号（```）中返回。

## 格式

```
{
	"direction": "horizonal|vertical",
	"contents": [
		{
			"type": "paragraph|title|list|table|quote|image|code",
			"markdown": "in markdown format",
			"bbox": [0.xmin, 0.ymin, 0.xmax, 0.ymax]
		},
		...
	]
}
```
'''

########################################################

MERGE_PMT = '''
你是一个专业的文档编辑助手。你将会得到两段 Markdown 文本，分别位于第一页最后一行和第二页第一行，请判断它们是否属于同一段落。如果第一段文本没有结束（例如：缺少句号、引号，或者语义明显未完），请将其与下一页的开头合并为同一段落。 如果句子已经结束，则保留为独立段落。你应该为此输出`true`或者`false`，不要输出其它选项。

## 格式示例

```
true | false
```

## 第一段文本

[content]
{prev}
[/content]

## 第二段文本

[content]
{next}
[/content]
'''

########################################################

POSTPROC_PMT = '''
你是一个专业的OCR后处理助手。下面是一份扫描文档经过OCR识别后得到的原始文本，每页以 [PAGE X] 分隔。

请你完成以下任务：

1. 纠正明显的OCR错误（如形近字混淆、标点错误、数字/字母误判）。
2. 将跨页的连续段落正确合并，不要保留分页标记。
3. 根据语义和缩进/编号特征，识别标题（用## 表示一级标题，### 表示二级标题）、列表项（用- 开头）。
4. 恢复正常的段落缩进（首行空两格不是必须，但请用空行分隔不同段落）。
5. 如果出现表格样式的文本，请将其转换为Markdown表格。

输出要求：

- 只输出处理后的最终文本，不要包含解释或额外注释，不要包含 [PAGE X] 标记。
- 保留原文的语言和所有信息，不要删减或总结。

原始OCR文本：

[content]
{text}
[/content]
'''

########################################################

TOC_PMT = '''
你是一个文档修复专家，下面是从扫描件OCR得到的目录的 Markdown。文本中可能存在错字、层级混乱等问题。

请完成以下修复任务，并严格按照 Markdown 格式输出。

## 要求

1. **重建层级**：通过行首空格数量、编号模式（如“1.”“1.1”“1.1.1”）判断章/节/小节，在输出中用行首的井号（`#`）表示。
2. **只输出变更的目录**：例如，“标题1”的层级从一级变更到二级，就输出“## 标题1”。“标题2”层级没有变更，就不输出。

## 目录

[content]
{text}
[/content]
'''

########################################################

TRANS_TITLE_PMT = '''
你是一个高级翻译，请参考示例，翻译指定的图书标题到中文。注意只需要输出翻译，不要输出其他任何东西。

## 示例

+   “with”不翻译，比如“deep learning with python”翻译为“python 深度学习”
+   “beginners guide”翻译为“初学者指南”
+   “cookbook”翻译为“秘籍”
+   “build xxx”翻译为“xxx构建指南”
+   “xxx in action”翻译为“xxx 实战”
+   “hands on xxx”翻译为“xxx 实用指南”
+   “practice xxx”翻译为“xxx 实践指南”
+   “unlock xxx”翻译为“xxx 解锁指南”
+   “xxx by example”翻译为“xxx 示例”
+   “pro xxx”翻译为“xxx 高级指南”
+   “xxx for yyy”翻译为“yyy 的 xxx”
+   “using xxx”翻译为“xxx 使用指南”
+   “introduction to xxx”或者“beginning xxx”翻译为“xxx 入门指南”
+   “quick start guide”翻译为“快速启动指南”
+   “playbook”翻译为“攻略书”

## 要翻译的标题

{text}
'''

########################################################

TRANS_BODY_PMT = '''
假设你是一个高级文档工程师和翻译员，请参考下面的注意事项了解 Markdown 文档的格式，然后参考示例，将给定英文文本翻译成中文。

## 注意事项

-   粗体（**bold**）和斜体（*itatic*）需要翻译翻译内容并保留符号。
-   内联代码（`code`）和代码块不需要翻译。
-   链接（[link](https://example.org)）需要翻译其内容，但保留网址。
-   列表，表格，引用块保留格式，翻译内容
-   原文可能有多行，不要漏掉任何一行，并且注意一定不要重复输出原文！！！
-   输出应包含在“[content]...[/content]”中

## 示例

原文：

[content]
-   [Feynman's learning method](https://wiki.example.org/feynmans_learning_method) is inspired by **Richard Feynman**, the Nobel Prize winner in physics. 

1.  With Feynman's skills, you can understand the knowledge points in depth in just `20 min`, and it is memorable and *hard to forget*. 

```
if (condVar > someVal) {console.log("xxx")}
```
[/content]

译文：

[content]
-   [费曼学习法](https://wiki.example.org/feynmans_learning_method)的灵感源于诺贝尔物理奖获得者**理查德·费曼**。

1.  运用费曼技巧，你只需花上`20 min`就能深入理解知识点，而且记忆深刻，*难以遗忘*。

```
if (condVar > someVal) {console.log("xxx")}
```
[/content]

## 以下是需要翻译的文本

[content]
{text}
[/content]
'''