from pydantic import BaseModel
from typing import Dict, Any

TOOLCALL_PMT = '''
## 工具调用指南

### 可用工具

在回答任何问题时，你可以调用一次或多次如下工具：

```
{tool_def}
```

### 返回格式

在决定调用工具时，请按照如下格式返回工具调用，确保内容包含在“[tool]...[/tool]”中，任何其它内容将会被忽略。如果不决定调用工具，不要输出任何“[tool]...[/tool]”内容。

[tool]
[{"id": "uuid", "tool": "tool name", "parameters": {"parameter name": "parameter value"}}]
[/tool]

用户调用工具后，将结果以如下格式传回：

[tool-result]
[{"id": "uuid", "result": "result"}]
[/tool-result]

### 示例

这是一个可用工具列表的示例：

```
{"tools": [{"name": "plus_one", "description": "Add one to a number", "parameters": {"type": "object","properties": {"number": {"type": "string","description": "The number that needs to be changed, for example: 1","default": "1",}},"required": ["number"]}},{"name": "minus_one", "description": "Minus one to a number", "parameters": {"type": "object","properties": {"number": {"type": "string","description": "The number that needs to be changed, for example: 1","default": "1",}},"required": ["number"]}}]}
```

如果你想计算`42 + 1`，可以返回：

[tool]
[{"id": "c3d16bba-9216-449e-8d46-d389fbca6cb5", "tool": "plus_one", "parameters": {"number": 42}}]
[/tool]

用户计算后，传回结果：

[tool-result]
[{"id": "c3d16bba-9216-449e-8d46-d389fbca6cb5", "result": 43}]
[/tool-result]

请注意，上述只是个示例，并不代表`plus_one`和`plus_minus`真实存在。
'''

class ToolCallItem(BaseModel):
    id: str
    tool: str
    parameters: Dict[str, Any]
