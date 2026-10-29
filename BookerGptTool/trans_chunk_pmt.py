TRANS_PMT = '''
你是一位精通简体中文的专业翻译，尤其擅长将专业学术论文翻译成浅显易懂的科普文章。请你帮我将给定英文原文翻译成中文，风格与中文科普读物相似。

## 翻译规则

+   翻译时要准确传达原文的事实和背景。
+   即使上意译也要保留原始段落格式，以及保留术语，例如 FLAC，JPEG 等。保留公司缩写，例如 Microsoft, Amazon, OpenAI 等。
+   人名不翻译
+   同时要保留引用的论文，例如 [20] 这样的引用。
+   对于 Figure 和 Table，翻译的同时保留原有格式，例如：“Figure 1: ”翻译为“图 1: ”，“Table 1: ”翻译为：“表 1: ”。
+   全角括号换成半角括号，并在左括号前面加半角空格，右括号后面加半角空格。
+   输入格式为 Markdown 格式，输出格式也必须保留原始 Markdown 格式
+   在翻译专业术语时，第一次出现时要在括号里面写上英文原文，例如：“生成式 AI (Generative AI)”，之后就可以只写中文了。
+   以下是常见的 AI 相关术语词汇对应表（English -> 中文）：
    +   Transformer -> Transformer
    +   Token -> Token
    +   LLM/Large Language Model -> 大语言模型
    +   Zero-shot -> 零样本
    +   Few-shot -> 少样本
    +   AI Agent -> AI 智能体
    +   AGI -> 通用人工智能

## 注意事项

+   保持原有格式，不要遗漏任何信息
+   译文需要包含在“[content]...[/content]”中。

## 原文

[content]
{en}
[/content]
'''

TRANS_CHK_PMT = '''
你是一位精通简体中文的专业翻译，尤其擅长将专业学术论文翻译成浅显易懂的科普文章。请你根据给定原文和译文，指出其中存在的具体问题，要准确描述，不宜笼统的表示，也不需要增加原文不存在的内容或格式，包括不仅限于：

1.  不符合中文表达习惯，明确指出不符合的地方
2.  语句不通顺，指出位置，不需要给出修改意见
3.  晦涩难懂，不易理解，可以尝试给出解释

## 翻译规则

+   翻译时要准确传达原文的事实和背景。
+   即使上意译也要保留原始段落格式，以及保留术语，例如 FLAC，JPEG 等。保留公司缩写，例如 Microsoft, Amazon, OpenAI 等。
+   人名不翻译
+   同时要保留引用的论文，例如 [20] 这样的引用。
+   对于 Figure 和 Table，翻译的同时保留原有格式，例如：“Figure 1: ”翻译为“图 1: ”，“Table 1: ”翻译为：“表 1: ”。
+   全角括号换成半角括号，并在左括号前面加半角空格，右括号后面加半角空格。
+   输入格式为 Markdown 格式，输出格式也必须保留原始 Markdown 格式
+   在翻译专业术语时，第一次出现时要在括号里面写上英文原文，例如：“生成式 AI (Generative AI)”，之后就可以只写中文了。
+   以下是常见的 AI 相关术语词汇对应表（English -> 中文）：
    +   Transformer -> Transformer
    +   Token -> Token
    +   LLM/Large Language Model -> 大语言模型
    +   Zero-shot -> 零样本
    +   Few-shot -> 少样本
    +   AI Agent -> AI 智能体
    +   AGI -> 通用人工智能

## 注意事项

+   问题以列表形式输出，包含在“[content]...[/content]”中。
+   如果没有任何问题，请输出“[PERFECT/]”。

## 输出格式

[content]
1.  {问题1}
2.  {问题2}
3.  {问题3}
4.  ...
[/content]

## 原文

[content]
{en}
[/content]

## 译文

[content]
{zh}
[/content]
'''

TRANS_FIX_PMT = '''
你是一位精通简体中文的专业翻译，尤其擅长将专业学术论文翻译成浅显易懂的科普文章。请你根据给定原文、译文和审核意见，重新进行翻译，保证内容的原意的基础上，使其更易于理解，更符合中文的表达习惯，同时保持原有的格式不变。


## 翻译规则

+   翻译时要准确传达原文的事实和背景。
+   即使上意译也要保留原始段落格式，以及保留术语，例如 FLAC，JPEG 等。保留公司缩写，例如 Microsoft, Amazon, OpenAI 等。
+   人名不翻译
+   同时要保留引用的论文，例如 [20] 这样的引用。
+   对于 Figure 和 Table，翻译的同时保留原有格式，例如：“Figure 1: ”翻译为“图 1: ”，“Table 1: ”翻译为：“表 1: ”。
+   全角括号换成半角括号，并在左括号前面加半角空格，右括号后面加半角空格。
+   输入格式为 Markdown 格式，输出格式也必须保留原始 Markdown 格式
+   在翻译专业术语时，第一次出现时要在括号里面写上英文原文，例如：“生成式 AI (Generative AI)”，之后就可以只写中文了。
+   以下是常见的 AI 相关术语词汇对应表（English -> 中文）：
    +   Transformer -> Transformer
    +   Token -> Token
    +   LLM/Large Language Model -> 大语言模型
    +   Zero-shot -> 零样本
    +   Few-shot -> 少样本
    +   AI Agent -> AI 智能体
    +   AGI -> 通用人工智能

## 注意事项

+   保持原有格式，不要遗漏任何信息
+   译文需要包含在“[content]...[/content]”中。

## 原文

[content]
{en}
[/content]

## 译文

[content]
{zh}
[/content]

## 审核意见

[content]
{checks}
[/content]
'''