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

FMT_PMT = '''
假设你是一个高级文档工程师，请参考下面的注意事项了解 Markdown 文档的格式，然后参考示例，将给定英文或中文文本排版。

请执行以下操作：

1. 重新划分标题层级（保留合理的# ## ###结构）
2. 优化段落格式，删除多余空行
3. 保留正文核心内容
4. 所有单独出现的变量名（`varName`），函数名（`funcName()`），类名（`ClassName`），路径名（`/path.to.xxx`），命令名（`cmdname`）以及它们的语句或表达式（`ClassName.funcName(var1 + var2, "cmd arg0 arg1"`）都需要添加反引号。如果上述东西被粗体（**）或者斜体（*）包围，去掉星号再加反引号。
5. 代码块前后加上三个反引号（```）

## 重要规则

- 不要修改正文内容的语义
- 不要删减有价值的信息
- 确保输出是标准Markdown格式
- 输出应包含在“[content]...[/content]”中

## 示例

原文：

[content]
进入 /path/to/xxx 目录，找到 xxx.json。

在表达式 cvar = avar + bvar 中，加法运算符（+）将 avar 与 bvar 相加，得到它们的和 cvar

在 List.of(arg0, arg1, arg2) 中，List 接口的工厂方法 of() 接受一系列的元素，返回包含它们的只读列表。

之后我们这样调用 cmd 命令：cmd arg0 arg1 arg2。

if (condVar > someVal) {console.log("xxx")}
[/content]

排版后：

[content]
进入`/path/to/xxx`目录，找到`xxx.json`。

在表达式`cvar = avar + bvar`中，加法运算符（`+`）将`avar`与`bvar`相加，得到它们的和`cvar`

在`List.of(arg0, arg1, arg2)`中，`List`接口的工厂方法`of()`接受一系列的元素，返回包含它们的只读列表。

之后我们这样调用`cmd`命令：`cmd arg0 arg1 arg2`。

```
if (condVar > someVal) {console.log("xxx")}
```
[/content]

## 以下是需要排版的文本

[content]
{text}
[/content]
'''


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

README_TMPL = '''
# {name_cn}

> 原文：[{name}]()
> 
> 译者：[飞龙](https://github.com/wizardforcel)
> 
> 协议：[CC BY-NC-SA 4.0](http://creativecommons.org/licenses/by-nc-sa/4.0/)
'''.strip()

TOC_EXT_PMT = '''
你是一位严谨的图书目录审核专家。你的任务是从我提供的“候选标题列表”中，精准筛选出属于这本书一级主干章的真实标题，并剔除节和子节等标题。

候选标题列表是JSON格式，包含本书所有标题信息，每个标题带有行号（`no`）、文本（`title`）、前文（`before`），后文（`after`）。

## 判断规则

+   主干层级：只认全书最高层级的大章（如“第 X 章”、“Y. ”）。如果候选是“X.Y”、“第 X 节”等子层级，直接剔除。
+   如果存在标题为“第 X 部分”，那么该书为部分-章节模式，需要找全其下层的章标题“第 Y 章”，返回部分标题和章标题，不要只保留部分标题。
+   格式特征：真正的章标题通常单独成行；标题末尾绝无句号（。.？！），且长度通常小于30字。
+   注意也可能没有章序号，这就需要依靠语义判断。
+   上下文语义：标题的后 1~3 行通常是概述段落，且后面跟着子节（例如“X.Y”）。标题的前几行是上一章末尾，通常带有总结、习题等。
+   序号连贯性：如果候选序号明显跳跃（如只有第 1 章、第 3 章，缺第 2 章），只保留序号连贯且符合全书逻辑的部分。

## 输出格式

只需要输出章标题的行号和判断原因。注意输出一定要包含在三个反引号（```）中。

```
[
	{"no": 1, "reason": "..."},
	...
]
```

## 候选标题

```
{titles}
```
'''