## 添加子命令

（当用户要求添加子命令时使用。）

首先将用户提供的子命令名称`sub-cmd`，转换为下划线模式`sub_cmd`。

提示词：

- 将提示词保存到`<sub_cmd>_pmt.py`。
- 所有提示词模板定义为全局多行字符串常量，名称`{XXX}_PMT`，带有`{...}`占位符，尽可能减少拼接。
- 如果输出是 JSON，应强调使用三个反引号包裹，否则强调使用`[content]...[/content]`包裹。
- 所有提示词应该是中文，如果不是将其翻译成中文。
- 具体参见`code2book_pmt.py`。

Pydantic 模型：

- 将模型保存到`<sub_cmd>_models.py`。
- 所有模型应该具有字符描述`Field(..., desciption='...')`。
- 具体参见`code2book_models.py`。

智能体：

- 智能体类命令为`{SubCmd}Agent`，保存到`<sub_cmd>_agent.py`。
- 智能体应接受命令行参数对象`args`
- 每个大模型的调用实现为其中的一个方法
- 使用`util.py`里面的`render_prompt`渲染提示词，`json_dump_model`将输入模型编程 JSON 字符串。
- 使用`openai.py`里面的`call_llm_retry`或者`ask_llm_retry`，调用大模型，传入`parse_output`回调函数来解析内容
- `parse_output`回调函数可以调用`util.py`里面的`ext_cont_block`和`ext_code_block`来解析大模型输出，调用`parse_obj_as`来转换 Pydantic 模型。
- 应当复用上述提示词和 Pydantic 模型。
- 具体参见`code2book_agent.py`。

编排器：

- 保存到`<sub_cmd>.py`，编排器类命名为`{SubCmd}Orchestrator`,应包含智能体类.
- 工作流的每个步骤定义为`step_xxx(...)`方法，调用智能体类的方法，`run`方法是主流程，调用这些步骤。
- 子命令入口函数实例化编排器并调用`run()`方法
- 具体参见`code2book.py`。


子命令解析器：

- 在`<sub_cmd>.py`中的`reg_subparser(subparsers)`函数中注册子命令。
- 然后在`__main__.py`中调用该函数来注册。