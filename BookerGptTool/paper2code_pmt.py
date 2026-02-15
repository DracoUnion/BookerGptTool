PLAN_PMT = '''
你是一位经验丰富的研究员和战略规划专家，深刻理解科学实验中的实验设计与可重复性。你将收到一篇研究论文。你的任务是制定一个详细且高效的方案，以复现论文中描述的实验和方法。该方案必须严格遵循论文中的方法、实验设置和评估指标。

## 指令

1.  **与论文保持一致**：你的方案必须严格遵循论文中描述的方法、数据集、模型配置、超参数和实验设置。
2.  **清晰且结构化**：以组织良好且易于遵循的格式呈现方案，将其分解为可操作的步骤。
3.  **注重效率**：在确保忠实于原始实验的前提下，优化方案的清晰度和实用可操作性。

## 任务

1.  我们希望复现附件论文中描述的方法。
2.  作者没有发布任何官方代码，因此我们必须自行规划实现方案。
3.  在编写任何 Python 代码之前，请先概述一个全面的计划，该计划应包含：
    -   论文**方法论**部分的关键细节。
    -   **实验**部分的重要方面，包括数据集要求、实验设置、超参数或评估指标。
4.  该计划应尽可能**详细且信息丰富**，以帮助我们后续编写最终代码。

## 要求
-   目前还不需要提供实际代码；专注于一个**全面、清晰的策略**。
-   如果论文中有不清楚的地方，请明确指出来。
-   回复应为我们提供一个强有力的路线图，以便我们后续更容易地编写代码。

## 论文

{paper}
'''

##############################################

FLIST_PMT = '''
你是一位经验丰富的研究员和战略规划专家，深刻理解科学实验中的实验设计与可重复性。你将收到一篇研究论文。你的任务是制定一个详细且高效的方案，以复现论文中描述的实验和方法。该方案必须严格遵循论文中的方法、实验设置和评估指标。

## 指令

1.  **与论文保持一致**：你的方案必须严格遵循论文中描述的方法、数据集、模型配置、超参数和实验设置。
2.  **清晰且结构化**：以组织良好且易于遵循的格式呈现方案，将其分解为可操作的步骤。
3.  **注重效率**：在确保忠实于原始实验的前提下，优化方案的清晰度和实用可操作性。

## 任务

你的目标是设计一个简洁、可用且完整的软件系统，用于复现论文中的方法。请选用合适的开源库，并保持整体架构简洁。

根据复现论文主要方法的计划，请设计一个简洁、可用且完整的软件系统。请保持架构简洁，并有效利用开源库。

## 格式

```
{
    "implementation_approach": "We will ... ,
    "file_list": [
        "main.py",  
        "dataset_loader.py", 
        "model.py",  
        "trainer.py",
        "evaluation.py" 
    ],
    "data_structures_and_interfaces": "\nclassDiagram\n    class Main {\n        +__init__()\n        +run_experiment()\n    }\n    class DatasetLoader {\n        +__init__(config: dict)\n        +load_data() -> Any\n    }\n    class Model {\n        +__init__(params: dict)\n        +forward(x: Tensor) -> Tensor\n    }\n    class Trainer {\n        +__init__(model: Model, data: Any)\n        +train() -> None\n    }\n    class Evaluation {\n        +__init__(model: Model, data: Any)\n        +evaluate() -> dict\n    }\n    Main --> DatasetLoader\n    Main --> Trainer\n    Main --> Evaluation\n    Trainer --> Model\n",
    "program_call_flow": "\nsequenceDiagram\n    participant M as Main\n    participant DL as DatasetLoader\n    participant MD as Model\n    participant TR as Trainer\n    participant EV as Evaluation\n    M->>DL: load_data()\n    DL-->>M: return dataset\n    M->>MD: initialize model()\n    M->>TR: train(model, dataset)\n    TR->>MD: forward(x)\n    MD-->>TR: predictions\n    TR-->>M: training complete\n    M->>EV: evaluate(model, dataset)\n    EV->>MD: forward(x)\n    MD-->>EV: predictions\n    EV-->>M: metrics\n",
    "anything_unclear": "Need clarification on the exact dataset format and any specialized hyperparameters."
}
```

## 格式说明

- `implementation_approach`：`str` ，总结所选的解决方案策略。
- `file_list`：`List[str]` ，只需要相对路径。请务必在此处写入 main.py 或 app.py。
- `data_structures_and_interfaces`：`Optional[str]` ，使用 mermaid classDiagram 代码语法，包括类、方法（__init__ 等）和带有类型注解的函数，清晰标注类之间的关系，并符合 PEP8 标准。数据结构应非常详细，API 应全面且设计完整。
- `program_call_flow`：`Optional[str]`，使用 sequenceDiagram 代码语法，完整且非常详细，准确使用上述定义的类和 API，涵盖每个对象的增删改查（CRUD）和初始化，语法必须正确。
- `anything_unclear`：`str` ，指出模糊之处并请求澄清。

## 约束

格式：输出内容包裹在三个反引号（```）内，就像格式示例一样，不要有其他内容。

## 操作

遵循节点的指示，生成输出，并确保其符合格式示例。

## 论文

{paper}

## 规划

[content]
{plan}
[/content]
'''

TASKS_PMT = '''
你是一位经验丰富的研究员和战略规划专家，深刻理解科学实验中的实验设计与可重复性。你将收到一篇研究论文。你的任务是制定一个详细且高效的方案，以复现论文中描述的实验和方法。该方案必须严格遵循论文中的方法、实验设置和评估指标。

## 指令

1.  **与论文保持一致**：你的方案必须严格遵循论文中描述的方法、数据集、模型配置、超参数和实验设置。
2.  **清晰且结构化**：以组织良好且易于遵循的格式呈现方案，将其分解为可操作的步骤。
3.  **注重效率**：在确保忠实于原始实验的前提下，优化方案的清晰度和实用可操作性。

## 任务

你的目标是根据PRD/技术设计方案拆解任务，生成任务清单，并分析任务间的依赖关系。
你将负责拆解任务并分析依赖关系。

你已为复现论文方法与实验制定了清晰的PRD/技术设计方案。

现在，请根据PRD/技术设计方案拆解任务，生成任务清单，并分析任务间的依赖关系。逻辑分析不仅要考虑文件间的依赖关系，还需提供详细描述，以辅助编写复现论文所需的代码。

## 格式

```
{
    "required_packages": [
        "numpy==1.21.0",
        "torch==1.9.0"  
    ],
    "required_other_language_third_party_packages": [
        "No third-party dependencies required"
    ],
    "logic_analysis": [
        [
            "data_preprocessing.py",
            "DataPreprocessing class ........"
        ],
        [
            "trainer.py",
            "Trainer ....... "
        ],
        [
            "dataset_loader.py",
            "Handles loading and ........"
        ],
        [
            "model.py",
            "Defines the model ......."
        ],
        [
            "evaluation.py",
            "Evaluation class ........ "
        ],
        [
            "main.py",
            "Entry point  ......."
        ]
    ],
    "task_list": [
        "dataset_loader.py", 
        "model.py",  
        "trainer.py", 
        "evaluation.py",
        "main.py"  
    ],
    "full_api_spec": "openapi: 3.0.0 ...",
    "shared_knowledge": "Both data_preprocessing.py and trainer.py share ........",
    "anything_uncliear": "Clarification needed on recommended hardware configuration for large-scale experiments."
}
```

## 格式说明

-   `required_packages`：`Optional[List[str]]`，以`requirements.txt`格式提供所需的第三方包（例如：'numpy==1.21.0'）
-   `required_other_language_third_party_packages`：`List[str]`，列出非Python语言所需的软件包，若无则填写"无第三方依赖要求"
-   `logic_analysis`：`List[List[str]]`，提供待实现的类/方法/函数文件列表，包含依赖关系分析和导入语句，尽可能包含详细描述
-   `task_list`：`List[str]`，按依赖优先级将任务拆解为文件清单，任务清单必须包含之前生成的文件列表
-   `full_api_spec`：`str`，使用OpenAPI 3.0规范描述前后端可能使用的所有API，如无需前后端通信则留空
-   `shared_knowledge`：`str`，详细说明共享知识，如通用工具函数或配置变量
-   `anything_uncliear`：`str`，列出论文或项目范围中需要解决的未决问题或待确认事项

## 约束条件

输出格式：需按照示例格式将内容包裹在三个反引号（```）内，不得包含其他内容

## 执行指令

请遵循上述节点说明生成相应输出，并确保符合给定的格式示例。

## 论文

{paper}

## 规划

[content]
{plan}
[/content]

## 文件列表

```
{flist}
```
'''

CFG_PMT = '''
你是一位经验丰富的研究员和战略规划专家，深刻理解科学实验中的实验设计与可重复性。你将收到一篇研究论文。你的任务是制定一个详细且高效的方案，以复现论文中描述的实验和方法。该方案必须严格遵循论文中的方法、实验设置和评估指标。

## 指令

1.  **与论文保持一致**：你的方案必须严格遵循论文中描述的方法、数据集、模型配置、超参数和实验设置。
2.  **清晰且结构化**：以组织良好且易于遵循的格式呈现方案，将其分解为可操作的步骤。
3.  **注重效率**：在确保忠实于原始实验的前提下，优化方案的清晰度和实用可操作性。

## 任务

你编写的代码应优雅、模块化且易于维护，并遵循谷歌代码风格规范。

请根据前述论文、计划和设计方案，按照"格式示例"生成代码。

从上述论文中提取训练细节（如学习率、批处理大小、训练轮次等），并严格遵循"格式示例"生成代码。

切勿编造细节——仅使用论文提供的信息。

必须编写 config.yaml 配置文件。

注意：请使用 '##' 分隔不同章节，而非 '#'。输出格式必须严格遵循以下示例。

## 格式示例

## 代码：`config.yaml`

```yaml
## config.yaml
training:
  learning_rate: ...
  batch_size: ...
  epochs: ...
...
```

## 论文

{paper}

## 规划

[content]
{plan}
[/content]

## 文件列表

```
{flist}
```

## 任务列表

```
{tasks}
```
'''

ANLS_PMT = '''
你是一位具有深度实验设计和科学研究可重复性专业知识的资深研究员、战略分析师兼软件工程师。
你将收到一份研究论文、一份方案概述、一个由"实现方法"、"文件清单"、"数据结构与接口"及"程序调用流程"组成的JSON格式设计方案，以及包含"所需软件包"、"其他语言第三方依赖"、"逻辑分析"和"任务清单"的JSON格式任务文件，同时附带名为"config.yaml"的配置文件。

你的任务是基于上述材料进行全面逻辑分析，精准复现研究论文所述的实验与方法。

此分析必须严格遵循论文中的方法论、实验设置及评估标准。

1. 严格遵循论文：分析过程必须完全依据论文所述的方法、数据集、模型配置、超参数及实验设置。
2. 清晰结构化：以逻辑清晰、组织有序且具备可操作性的格式呈现分析内容，便于后续实施。
3. 效率优先：在确保忠实于原始实验的前提下，优化分析的清晰度和实践可行性。
4. 遵循设计方案：必须严格遵循"数据结构与接口"的设计规范，不得更改任何设计细节。禁止使用设计中未定义的公有成员函数。
5. 参照配置文件：所有配置参数均需引用config.yaml文件中的设定。严禁自行编造或假设参数值，仅可使用明确提供的配置项。

## 论文

{paper}

## 规划

[content]
{plan}
[/content]

## 文件列表

```
{flist}
```

## 任务列表

```
{tasks}
```

## 指令

基于论文、方案、设计、任务及先前指定的配置文件（config.yaml），进行逻辑分析以辅助代码编写。
现阶段无需提供实际代码，重点在于进行全面、清晰的分析。

将逻辑分析写入 '{todo_file_name}'，该文件用于 '{todo_file_desc}'
'''

CODE_PMT = '''
你是一位对科学研究的实验设计与可重复性具有深刻理解的专家研究员兼软件工程师。你将收到一份研究论文、一份方案概述、一个包含"实现方法"、"文件列表"、"数据结构与接口"和"程序调用流程"的JSON格式设计方案，以及一份包含"所需包"、"所需其他语言第三方包"、"逻辑分析"和"任务列表"的JSON格式任务说明，同时还有一个名为"config.yaml"的配置文件。你的任务是根据论文中描述的实验和方法编写可复现的代码。

编写的代码必须优雅、模块化且易于维护，遵循谷歌代码风格规范。代码必须严格符合论文中的方法、实验设置和评估指标。请使用三重引号编写代码。

## 论文

{paper}

## 规划

[content]
{plan}
[/content]

## 文件列表

```
{flist}
```

## 任务列表

```
{tasks}
```

# 指令

基于先前指定的论文、方案、设计、任务和配置文件（config.yaml），编写代码。

我们已完成 {done_file_lst}。接下来，你只需编写 "{todo_file_name}"。

1. 仅此一个文件：请尽力实现这唯一的一个文件。
2. 完整代码：你的代码将成为整个项目的一部分，因此请实现完整、可靠、可复用的代码片段。
3. 设置默认值：如有任何设置，务必设定默认值，始终使用强类型和明确的变量。避免循环导入。
4. 遵循设计：必须遵循"数据结构与接口"。不得更改任何设计。不要使用设计中不存在的公有成员函数。
5. 仔细检查：确保在此文件中不遗漏任何必要的类/函数。
6. 导入检查：使用外部变量/模块前，务必先导入。
7. 详尽的代码细节：写出每个代码细节，不留待办事项。
8. 参照配置：必须使用"config.yaml"中的配置。不得编造任何配置值。

## 逻辑分析

[content]
{logic_analysis}
[/content]
'''