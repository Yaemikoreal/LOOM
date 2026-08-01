# OpenNovel

本地优先的长篇小说叙事操作系统。作者只写纯文本 Markdown，由系统在底层维护世界观的一致性。

## Language

### 架构层

**Context Strategy** (三级上下文策略):
LLMBus 根据模型上下文窗口自动映射的策略：
- **FRUGAL** (<32K)：RAG + 摘要，8K 预算。适用于小窗口模型（<32K），当前已基本淘汰——多 Agent 系统在 <16K 窗口下不可用。
- **STANDARD** (32K–128K)：均衡型，48K 预算，当前章全量 + 全部活跃角色状态。大部分 Agent 的实际工作策略。
- **PANORAMIC** (>128K)：全景沉浸，全量设定+全量潜意识+历史正文倒序灌注。128K 软限，防延迟失控和注意力漫游（ADR 0002）。
_Avoid_: 固定 Token 预算、一刀切的天花板
_注意_: `detect_strategy()` 已在 `context_assembler.py` 实现（第 44-58 行），但当前未被任何 Agent 调用——Writer/Critic/Director 全部硬编码 `STANDARD`。激活该函数是零风险的帕累托改进方向。

### 权威体系

**Human Layer**:
作者直接操作的纯 Markdown 文件层（`canon/`, `characters/`, `draft/`）。永远可被 Git 追踪、Obsidian/VSCode 打开。
_Avoid_: 创作层、输入层

**Machine Shadow**:
AI 自动提取的结构化数据**存储层**（容器概念），由 YAML Frontmatter（局部缓存）+ SQLite（全局事件账本）+ Snapshots（时间机器）组成。只关心数据怎么存、存在哪，不关心 LLM 怎么用。
_Avoid_: 状态层、影子层、STATE MEMORY（这是上下文视图，不是存储层）

**Semantic Layer**:
基于 LlamaIndex + BGE-M3 的向量检索引擎，将历史文本转化为可召回的语义记忆。

### 权威体系

**Authority Level**:
上下文消息的权威优先级标签，决定冲突时谁覆盖谁。从高到低：`CANON` > `STATE MEMORY` > `SUBCONSCIOUS`。

**CANON**:
不可变世界观设定。最高权威，LLM 绝对不可违反。例如"魔法消耗寿命"。
_Avoid_: 设定、世界观规则

**STATE MEMORY**:
Machine Shadow 中记录的角色/世界当前状态。中等权威，LLM 必须尊重。例如"左臂骨折"。
_Avoid_: Shadow、状态缓存

**SUBCONSCIOUS**:
灵感碎片池（`subconscious/lines.md`）。最低权威，仅作文风参考，绝不可作为事实执行。
_Avoid_: 潜意识、灵感池

**Dirty Flag**:
当 `novel commit` 中 Auditor 三次提取均失败且用户选择脏提交时，在章节 Frontmatter 中强制写入 `dirty_flag: extraction_failed`，标记该章节状态不可信。
_Avoid_: 静默跳过

### 流程

**Commit 审阅流**:
`novel commit` 的 5 步流程：①快照生成 → ②Auditor 提取（含最多 3 次自省纠偏）→ ③Diff 展示 → ④人工确认 → ⑤写入固化。若 Auditor 连续 3 次失败则进入人类急救模式：编辑残次 JSON / 脏提交 / 终止。

**Rescue Mode**:
Auditor 三次提取均失败后的 fallback。提供三个选项：[E]dit（手动修补 JSON）、[S]kip（脏提交，打 dirty_flag）、[A]bort（终止 commit）。

### 标识与数据

**Canonical ID**:
系统内部关联的全局稳定标识符。格式：`char_001`（角色）、`loc_london`（地点）、`item_sword`（物品）。
_Avoid_: 角色名、自然语言标识

**EmotionVector**:
角色情绪状态，由一组命名维度组成。核心维度：grief, anger, fear, joy, determination（0.0~1.0）。支持自由扩展额外情绪字段。
_Avoid_: EmotionalState, emotion_vector

**Causal Pressure**:
事件对后续叙事影响力的量化指标（0.0~1.0）。>0.7 为高因果压强事件，通常关联关键剧情转折。

**Event Log**:
SQLite 中存储的全局因果事件账本，记录所有经人工确认的状态变更事件。

**Snapshot**:
`novel commit` 前自动生成的文件级增量快照（`.snapshots/*.snapshot.json`），仅记录被本次 commit 涉及的文件的 `fm_before` 和 `fm_after`。回滚时按文件逐条覆写，覆写前校验当前文件与 `fm_after` 是否一致（防止覆盖人类在间隙中的手动修改）。不涉及的文件绝不触碰。
_Avoid_: 全局全量 dump、field_path 级 JSON Patch

### 自主创作系统（Gen2）

**AutoRunner**:
`novel auto` 的编排器（非 Agent），负责解析大纲、按序执行章节流水线（think → write → evaluate → revise → update/skip）、管理重试、条件路由和日志。包含条件跳转逻辑（高分章节跳过 Manager 实时更新、按章节类型路由 Director）。
_Avoid_: 导演、Orchestrator

**Director Agent**:
创作总监 Agent，从全局视角分析已完成章节的叙事状态（评分趋势、因果压力曲线、角色弧线），输出策略指导注入下一章的 `chapter_hint`。实现于 `agents/director.py`。通过 `novel.yaml` 的 `director_enabled` 控制。

**Anchored Issue**:
Critic 反馈的结构化升级。从 `list[str]` 升级为包含文本定位信息的结构化对象：`dimension`（维度）、`severity`（critical/major/minor）、`quote`（原文引用，20-50 字）、`problem`（问题描述）、`suggestion`（修改建议）、`location_hint`（位置提示）。让反馈从"评价意见"变成"可执行的补丁"。实现于 `schemas/evaluation.py` 的 `AnchoredIssue` 模型。

**Exploratory Variation**:
盲目变异的探索模式。在高潮关键词（转折/高潮/climax/决战/大结局/finale）触发，不同 temperature（0.5/0.7/0.9）生成多样化方向。与 Corrective Variation 对应。实现于 `agents/writer.py` 的 `think_variations()` 方法。

**Corrective Variation**:
盲目变异的纠错模式。在前章评分 <80 时触发，将 Critic 的低分原因作为负向约束注入 Writer 的多方案生成 Prompt，每个方案尝试不同的修复策略。实现于 `agents/writer.py` 的 `think_variations(variation_mode="corrective")`。

**Outline Evaluation**:
Critic 对大纲方案的三维评审（情节逻辑/角色一致性/节奏设计），每维 20 分，满分 60 分。用于盲目变异流程中的多方案预审选择。实现于 `agents/critic.py` 的 `evaluate_outline()` 方法和 `schemas/outline_evaluation.py`。

**Director Agent**:
创作总监 Agent，从全局视角分析已完成章节的叙事状态（评分趋势、因果压力曲线、角色弧线），输出策略指导注入下一章的 `chapter_hint`，并可提议大纲结构调整（插入/跳过/合并章节）。实现于 `agents/director.py`。通过 `novel.yaml` 的 `director_enabled` 配置开关控制。

**Scheduling Proposal** (调度提议):
Director 对大纲结构调整的建议。支持三种动作：`INSERT`（在目标章节前插入补充章节，需提供 `new_chapter_hint`）、`SKIP`（跳过目标章节）、`MERGE`（将两章合并为一章，暂未实现）。AutoRunner 的 `_apply_scheduling_proposals()` 从后往前应用提议，自动过滤已完成的章节，并生成不重复的新章节 ID。实现于 `schemas/director.py` 的 `SchedulingProposal` 模型和 `core/auto_runner.py` 的调度执行方法。

**Checkpoint**:
`novel auto` 的事后保护机制。每章写入前自动创建快照（`StateManager.create_snapshot()`），写入后完成快照并运行 `DiffChecker.check_chapter()` 进行一致性校验。校验结果记入 `run_log.md`，支持 `novel rollback` 回滚到任意章节。实现于 `core/auto_runner.py`。

### 认知框架与 Prompt 差异化

**Cognitive Framework** (认知框架层):
每个 Agent 的强制思维脚手架，区别于自由发挥的角色扮演。通过强制 LLM 先回答特定问题集来调整注意力分配。例如 Writer 的"意图驱动框架"（角色渴望 → 阻碍设置 → 情感节拍 → 感官锚点），Critic 的"逆向工程框架"（结构拆解 → Canon 比对 → 动机审查 → 信息密度）。与风格层（语气拟人化）的本质区别：认知框架改变推理路径，风格层只改变措辞。
_Avoid_: 角色扮演、Prompt 人格化

**Model-Layer Adaptation** (模型层适配):
不同 Agent 使用不同模型并为其量身定制 Prompt。Writer 选用创意性强的模型（长上下文），Critic 选用逻辑严密的模型（可更便宜），Manager 选用擅长结构化输出的模型。Prompt 需针对目标模型的特性优化（如 Claude 用 XML 标签，GPT-4o 用口语化 few-shot）。已通过 `novel.yaml` 的 per-agent model override 支持。

**Stage Model Routing** (阶段模型路由 / 成本优化器):
ADR 0005 执行层成本优化。在同一 Agent 内，不同阶段使用不同模型。Writer 的 `think()` 阶段使用廉价模型（如 `gpt-4o-mini`）生成大纲，`write()` 阶段切换主力模型（如 `gpt-4`）创作正文，`revise()` 阶段使用主力模型修订。通过 `novel.yaml` 的 `agents.writer.think_model / write_model / revise_model` 字段配置。未设置的阶段继承默认模型。实现于 `agents/writer.py` 的 `Writer.__init__` 和每个方法的 `model=` 参数。

### 深层变异系统

**Mutation Dimension** (变异维度):
深层变异的四个正交维度：叙事结构（线性/倒叙/双线并行）、视点与声音（感知者切换）、因果与时间线（重构根本原因）、弧光与主题（反转成长轨迹）。每次变异仅在单维度上进行，避免组合爆炸。
_Avoid_: 概率采样、temperature 变异

**Three-Layer Mutation Control** (三层变异控制):
变异的分层控制机制。战略层（人类作者）设定变异边界与否决权；战术层（策略引擎）智能选择 1-2 个最相关维度，限制组合爆炸；执行层（成本优化器）通过模型路由、上下文裁剪、轻量级预筛控制 Token 成本。核心原则：作者控边界，策略控维度，执行控成本。

**Structural Template** (结构模板):
预定义的叙事结构骨架（三幕剧、倒叙、网状叙事等），用于指导 Writer 的 think 阶段。与 temperature 变异的区别：结构模板是符号规则驱动的宏观结构切换，不是概率采样。

### 动态路由架构

**Scheduler** (调度器):
动态路由的中枢，基于章节类型和健康度诊断决定 Agent 出场顺序。例如高潮章节强制前置 Director 分析，日常章节跳过 Director。实现为 AutoRunner 内的路由逻辑，而非独立 Agent。
_Avoid_: 编排器、Orchestrator

**Agent Autonomy** (Agent 自治):
Agent 在安全围栏内的微观自决权。当前已实现：Writer 在 think→write 之间自动检测知识缺口（`detect_knowledge_gaps()`），通过 ToolRegistry 主动查询缺失设定和角色状态，结果注入 write() 的 additional_knowledge。Critic 可触发 Writer 的局部热修复（见 [[#Local Hot-fix]]）。安全围栏（[[#Safety Fence]]）约束递归深度和 Token 预算。

**Knowledge Gap Detection** (知识缺口检测):
Writer 在思考完成后、创作开始前，自动扫描大纲中的角色引用和设定关键词，与已注入的上下文比对，找出未覆盖的信息点。输出 `KnowledgeNeed[]` 列表供 ToolRegistry 查询。实现于 `agents/writer.py` 的 `detect_knowledge_gaps()` 方法。检测规则：场景和角色弧线中出现的 `char_*` ID → CHARACTER 来源缺口；场景描述中出现的设定关键词（魔法/规则/诅咒等）→ CANON 来源缺口。

**ToolRegistry** (工具注册中心):
知识查询的分发中枢。接收 `KnowledgeNeed[]`，按 `KnowledgeSource` 路由到对应数据源：`CANON`→ Retriever.query_canon()、`SUBCONSCIOUS`→ Retriever.query_subconscious()、`CHARACTER`→ YAMLStorage.read_character_file()、`EVENT`→ EventStore 查询。查询结果以 `KnowledgeResult[]` 返回，包含内容和相关性评分。实现于 `core/tool_registry.py`。

**KnowledgeNeed** (知识需求):
Agent 自治的查询协议。包含 `concept`（查询概念）、`source`（数据源枚举：canon/subconscious/character/event）、`context`（查询上下文）、`character_id`（角色查询时指定）。实现于 `schemas/knowledge.py`。

**Safety Fence** (安全围栏):
对 Agent 自治行为的约束边界。实现于 `core/safety_fence.py` 的 `SafetyFence` 类。提供四个维度的检查：递归深度防护（`check_recursion_depth`，默认上限 3 层嵌套）、Token 预算追踪（`check_token_budget`，默认每调用 4000 tokens）、超时熔断（`check_timeout`，默认 120s）、Canon 不可违背（预留）。通过 `SafetyFenceConfig` 配置，可通过 `novel.yaml` 的 `safety_fence` 字段覆盖或禁用。提供 `autonomous_call()` 上下文管理器自动管理深度计数和预算覆盖，违规记录于 `SafetyViolation` 列表。当前集成于 hot_fix 自治调用和 Director 分析调用。

**Conditional Jump** (条件跳转):
AutoRunner 中的效率优化分支。例如 Critic 评分 > 90 时跳过 Manager 即时状态提取，改为批处理。实现于 `auto_runner.py` 的 `run_chapter()` 和 `_process_deferred_manager_updates()`。

**Local Hot-fix** (局部热修复):
Agent 自治的子特性，Critic 发现局部硬伤时触发 Writer 的段落级精确修改而非全章重写。Writer 的 `hot_fix()` 方法接收 `AnchoredIssue[]`（含 quote 原文引用定位），通过 `_find_paragraph_around()` 在正文中定位问题段落，仅将问题段落 + 上下文发送给 LLM 进行针对性改写，通过 `_apply_hot_fix()` 将修复段落合并回原文。hot_fix 失败（无法定位原文/LLM 返回异常/结果长度异常）时自动回退到全章 `revise()`。实现于 `agents/writer.py` 的 `hot_fix()`，AutoRunner 的 retry 循环优先使用 hot_fix。

**Chapter Type** (章节类型):
调度器路由决策的依据。通过 `detect_chapter_type()` 从大纲 hint 检测三种类型：`CLIMAX`（高潮/转折/决战，强制运行 Director）、`TRANSITION`（过渡/日常/平静，跳过 Director）、`ROUTINE`（普通推进，每 N 章运行一次 Director）。实现于 `auto_runner.py`。

**Batch Manager Update** (批处理 Manager 更新):
条件跳转的配套机制。被跳过的 Manager 实时更新暂存于 `_deferred_manager_updates` 队列，在 `run()` 末尾通过 `_process_deferred_manager_updates()` 批量处理。处理结果回填到对应的 `ChapterResult`，并清除 `manager_skipped` 标志。批处理失败不中断整体流程。

### 人机交互层

**Human-AI Co-creation Cockpit** (人机共创驾驶舱):
PySide6 单窗口桌面应用，同仓库独立包 `opennovel_desktop/`，CLI 入口 `novel-desktop`。单窗口多面板布局：左侧导航（按钮切换文件树/角色卡片/大纲概览）→ 中央 NovelEditor（QPlainTextEdit 基座）→ 右侧标签面板（Critic 反馈/Agent 时间线/评分趋势/检索/设置/Commit Diff）。底部状态栏显示 Token 余量、API 状态、章节进度等。核心交互：右侧 Critic 报警时左侧编辑器自动高亮相关段落；左侧修改后右侧自动触发状态重算。目标不是更好的编辑器，而是让 AI 创作过程"可见、可控、可干预"。

**NovelEditor** (编辑器组件):
继承 `QPlainTextEdit` + `QSyntaxHighlighter` 的轻量 Markdown 编辑器。通过 ~200 行正则实现基础语法高亮（标题/加粗/对话引用/分割线）。预留 `appendStreamingText(text)` 和 `highlightDiff(start, end, color)` 接口用于 Agent 流式输出和差异高亮。右键菜单集成"发送给 Agent 润色/续写/扩写"。不含实时 Markdown 渲染预览。

**NovelDesktop MainWindow** (主窗口):
驾驶舱的骨架，QMainWindow 单窗口。左侧 `QStackedWidget` 按钮切换导航面板，中央 `NovelEditor` 标签页体系（所有可编辑文件都在编辑器区以标签页打开），右侧 `QTabWidget` 多标签面板。底栏 `QStatusBar` 监控信息。主工具栏纯图标 32x32 固定尺寸，无文字标签，QToolBar `movable=false` 锁定在顶部禁止拖拽/浮动。所有 icon 通过专业 SVG 资源实现，不使用 emoji 或 Unicode 符号做 UI 装饰。主题系统：跟随系统亮/暗双主题，QSS + 变量文件实现，语法高亮颜色随主题切换。

**Main Toolbar** (主工具栏):
6 枚纯图标按钮，固定间距水平排列：写章节、Auto（全自动创作）、停止（运行时动态激活，灰色→红色高亮）、Commit（提交状态）、灵感（存入灵感碎片）、保存草稿。每个按钮通过 `setToolTip()` 提供 HTML 格式提示（含名称 + 功能说明 + 快捷键）。搜索、回滚、诊断等低频操作移入菜单栏或面板内。

**Menu Bar** (菜单栏):
四个顶级菜单：

文件：新建项目 `Ctrl+N` / 打开项目 `Ctrl+O` / 保存 `Ctrl+S` / 另存为 / 偏好设置 `Ctrl+,` / 退出 `Ctrl+Q`
编辑：撤销 `Ctrl+Z` / 重做 `Ctrl+Y` / 查找（编辑器内）`Ctrl+F` / 替换 `Ctrl+H` / 全局搜索 `Ctrl+Shift+F`（唤出右侧搜索标签）/ 发送给 Agent ▶（子菜单：润色/续写/扩写/视角重写）
视图：显示左侧导航 `Ctrl+Shift+1` / 显示右侧面板 `Ctrl+Shift+2` / 显示状态栏 / 专注模式 `F11`（隐藏所有外围面板仅留编辑器）/ 重新加载样式 `Ctrl+Shift+R`
工具：项目诊断 / 重写索引 / 校准 Critic / 历史回滚（打开 Diff 面板选择历史 Commit）/ 清除缓存
帮助：关于 OpenNovel / 检查更新 / 打开日志目录

**Pipeline View** (流水线视图):
Agent 创作循环在右侧面板的默认展现模式。按阶段显示流水线进度条（Writer.think → Writer.write → Critic.evaluate → Manager.update），每阶段显示耗时和状态（排队中/运行中/已完成/有问题）。每阶段可点击展开详情查看当前阶段的推理链（Reasoning Chain），本质是推理链模式 B（Think Aloud）的按需透出。

**Pipeline Phase Indicator** (流水线阶段指示器):
阶段状态的视觉编码。每个阶段带颜色标识：蓝色=运行中、绿色=通过、黄色=有问题（Critic 评分 <80 但可接受）、红色=失败（需干预）、灰色=未开始。阶段之间的连接线表示依赖关系。

**Agent Reasoning Panel** (推理链面板):
右侧标签页之一，展现 Agent 在高价值决策环节的结构化思考过程。与流水线视图联动——点击 Pipeline View 中的任一阶段时，该面板自动切换到对应阶段的推理链内容。内容来自 `logs/reasoning/{trace_id}.json`。

**AppState** (应用状态管理层):
PySide6 GUI 中的 `QObject` 单例，维护当前项目的内存级缓存和运行状态。持有 `current_project`、`current_file`、`project_summary`、`agent_status`、`recent_scores` 等状态字段。各面板通过 Qt Signal/Slot 订阅感兴趣的状态变更（`file_changed`、`scores_updated`、`agent_status_changed` 等）。用缓存避免每次面板切换都读文件/SQLite，通过信号体系保证状态变更的实时广播。

**AgentWorker** (后台 Agent 工作线程):
`QObject` + `QThread` 模式的后台执行器。所有 LLM 调用（Writer 创作、Critic 评分、Director 分析）在独立线程中执行。Worker 通过 Qt Signal 回传流式文本片段、进度更新、完成结果。主线程通过 `appendStreamingText(text)` 将流式输出实时追加到 NovelEditor。`QTimer` 120s 超时熔断防止 LLM 挂死。

**Desktop Startup Flow** (桌面端启动流程):
启动时先读取 `last_session.yaml`：
- 首次运行（无 `.opennovel.yaml`）→ 弹出初始设置向导（QWizard，4 步：欢迎→工作区目录→API Key→默认模型），完成后生成 `.opennovel.yaml`
- 有 `last_session.yaml` 且上次项目仍存在 → 直接打开进入驾驶舱
- 其余情况 → 显示工作区项目选择器（IDE 风格，列出 `novel list` 的工作区项目 + 最近打开历史）
驾驶舱内支持"关闭项目"回到选择器，不退出进程，AppState 清空 + 页面栈切换。

**Project File Panel** (项目文件面板):
左侧导航面板之一，按钮切换唤醒。将项目物理目录按语义分组展示：**正文**（draft/）、**设定**（canon/ + characters/）、**蓝图**（outlines/ + foreshadowing/ + timeline/）、**灵感**（subconscious/）。底层通过配置映射到物理目录，本质是语义层对文件系统的投影。

**Character Card Panel** (角色卡片面板):
左侧导航面板之二，按钮切换唤醒。左侧角色名列表（QListWidget），选中后右侧展示完整卡片详情（QTextBrowser 渲染 Markdown + YAML Frontmatter）。卡片底部提供"发送给 Agent"快捷按钮。

**Outline Tree Panel** (大纲树面板):
左侧导航面板之三，按钮切换唤醒。解析 outlines/story.md 的 Markdown 标题层级，生成只读 QTreeWidget。单击跳转主编辑器到对应位置，双击打开大纲源文件进行文本级编辑。初期不实现拖拽排序。

**Editor Context Menu** (编辑器右键菜单):
NovelEditor 右键菜单的 Agent 操作区。三级行为：**即时执行**（润色/续写/扩写，预设 Prompt 直接调后台 AgentWorker，流式结果以内联 Diff 形式展示）、**填入 Agent 面板**（视角重写/Critic 检查等复杂操作，将选中文本发送到右侧 Agent 面板输入框，用户补充指令后执行）、**工具操作**（查询相关知识/存入灵感池，调 ToolRegistry 或 stash 接口）。

**Critic Feedback Panel** (Critic 反馈面板):
右侧固定标签之一。顶部五维评分条形图，中部 AnchoredIssue 可勾选列表（每条带 severity 标签 + quote 原文引用 + 问题描述），底部"要求修订 / 跳过 / 全部接受"操作按钮。每条 AnchoredIssue 可点击——点击后主编辑器自动滚动到 `location_hint` 对应行并以高亮色标记目标段落。Critic → Editor 反馈定位联动是该交互环节的刚性规格。

**Pipeline View Panel** (流水线面板):
右侧固定标签之一。Agent 创作循环的实时进度展示（Writer.think → Writer.write → Critic.evaluate → Manager.update），每阶段带颜色编码状态指示器。Agent 运行时自动激活该标签。

**Diff Review Panel** (Diff 审阅面板):
右侧固定标签之一。展示 commit 流程中的变更对比，以及 Inline Diff 的"接受/拒绝"批注汇总。在 Commit 流程进入审阅步骤时自动激活。面板分两区：文件变更区（每个文件增删行数 + 查看 Diff 链接）和提取事件区（checkbox 勾选列表）。用户勾选确认后点底部"确认提交"一次性写入，将 CLI 的串行问答转化为 GUI 的并行勾选。每项事件支持悬停预览或点击跳转——在左侧编辑器高亮对应正文出处行，实现"设定-正文"双向联动。

**Global Settings Dialog** (全局设置对话框):
`文件 → 设置` 触发的 QDialog 模式对话框。集中管理跨项目配置：API Key、全局默认模型、工作区目录、默认 Token 预算等。低频操作，模态窗口锁定焦点确保修改有确认/取消。

**Project Settings Panel** (项目设置面板):
右侧 ⚙️ 上下文标签的内联配置面板。表单仅封装高频核心字段：模型选择（下拉框）、temperature（滑条 0.0~1.0）、Token 上限（数字输入）、创作方向（文本框），可随时微调不阻断创作流。底部提供"编辑原始 YAML"逃生舱按钮，点击在同面板内展开 YAML 纯文本编辑框，覆盖全部 20+ 配置字段。

**Search Panel** (全局搜索面板):
右侧上下文标签，`Ctrl+Shift+F` 唤出。顶部搜索框 + 右侧切换按钮（🔤 精确 / 🧠 语义双模式），筛选区三个常驻复选框（canon / draft / subconscious）。结果列表每项显示来源标签 + 匹配度 + 摘要。点击结果跳转编辑器并高亮段落；结果右侧单一 ➕ 按钮将引用文本插入编辑器光标位置。精确模式仅 FTS5 通道 ~0.01s 响应，语义模式全管道 ~0.3s。

**Agent Error Notification** (Agent 错误通知):
Toast 轻提示 + Pipeline View 红色标记的组合策略。Agent 后台任务失败时：右下角滑入 Toast 告知"Writer 超时"（3s 自动消失，若失败导致流水线卡死则转为常驻不消失直至用户点击处理）；同时 Pipeline View 对应阶段显示红色 ❌ 标记 + 错误摘要，用户点击可查看详情和重试。全程无模态弹窗打断心流。

**Commit Conflict Resolution** (Commit 冲突解决):
Diff 面板内完成，无模态弹窗。冲突区域以红色标记，提供"保留我的 / 保留 AI 的 / 手动编辑"三按钮。解决并保存后自动提示"继续流水线"让 Agent 恢复工作。

**Commit Rescue Mode** (Commit 救援模式):
Auditor 提取事件失败时，在 Diff 面板顶部显示红色警告条，提供三选一按钮：[重试] 调后台 Worker 重新提取 / [脏提交] 打 dirty_flag 跳过 / [取消] 回到编辑器。全程无模态弹窗，无需用户在 GUI 中手搓 JSON。

**Auto Creation Panel** (全自动创作面板):
右侧上下文标签，Auto 运行期间激活。上半部分总进度条 + 平均分 + 总字数；下半部分章节列表（每行显示章节 ID、评分、字数、状态图标）。已完成章节可点击跳转编辑器查看，正在运行的显示实时 Pipeline View 状态，排队中的显示灰色 ⏳。

**Auto Flow** (全自动创作流):
默认逐章审阅（Progressive Mode）：每章完成后自动暂停，编辑器自动滚动到该章结尾，右侧 Critic 面板展示评分 + AnchoredIssue，用户审阅后点"继续下一章"或"停止"。Pipeline View 中提供"全部自动"按钮切换到连续模式（Headless Mode）。连续模式下启用 Critic 熔断机制：连续 2 章评分 < 60 分或出现 critical 级 AnchoredIssue 时自动暂停并强制用户介入。

**Status Bar** (状态栏):
QStatusBar 三区布局。**左区**（addWidget）：当前文件名 + 字数统计 + 光标位置 `行 N, 列 M`（与 NovelEditor 的 cursorPositionChanged 信号联动）。**中区**（addWidget）：模型名 + `Critic N`（最近评分）+ `Token N.Nk/总k`。**右区**（addPermanentWidget）：API 状态指示灯（●绿=可用 / ●红=失败 / ●灰=未配置，点击查看最近 LLM 调用日志）+ Agent 后台运行状态文字（Agent 运行时显示"Agent 运行中..."及微小旋转图标，空闲时隐藏）+ 保存状态（"已保存" / "未保存"，与 modificationChanged 信号联动）+ ⚙ 图标打开设置。

**Session Persistence** (会话持久化):
窗口几何与面板状态通过 `QSettings + INI` 格式持久化（`saveGeometry()` / `restoreGeometry()` + `saveState()` / `restoreState()`），存入 `%APPDATA%/OpenNovel/`。编辑状态（打开的文件列表、各文件光标位置、当前活动标签页）单独序列化为 `%APPDATA%/OpenNovel/session.json`，下次启动时恢复。项目切换或退出时自动保存。

**Crash Recovery** (崩溃恢复):
NovelEditor 每 60s 自动保存编辑器草稿态到 `.snapshots/autosave/`，与 StateManager 机制复用。下次启动时检测到未提交的备份则弹出非模态恢复提示条（"检测到未保存的崩溃草稿，是否恢复？"），用户确认后草稿填入编辑器等待手动保存或触发 Agent。不自动 Commit。

**Inline Diff** (内联 Diff):
Agent 修改建议的展示方式。原文以红色删除线标记保留，修改后文本以绿色背景追加在原文下方，提供"接受/拒绝"按钮。用户确认后才修改真实文本。避免弹窗打断心流的不可逆覆盖。

**Intent Routing** (意图路由):
自然语言指令的结构触发器。将作者的自然语言映射到深层变异和工作流。例如"从反派视角重写"直接触发视点变异路由；"跳到三年后"触发时间线变异路由。

**Dialogue Collaboration** (对话式协作):
自然语言指令的意图澄清器。处理模糊指令时，AI 先调用 Director 生成多个方案及因果链推演，作者选择并补充约束后，系统锁定参数交由 Writer 执行。需要对话管理器维护多轮协作状态。

**Hint Enhancement** (Hint 增强):
自然语言指令的基础底座。所有自然语言首先被解析为结构化参数（如 `{"tension": "crisis", "technique": "environmental_subtext"}`），注入 Writer 的任务消息。是意图路由和对话式协作的共同基础。

### 指标与遥测

**Metrics Database** (指标数据库):
独立于 EventStore 的运行遥测存储（`.novel.metrics.db`）。三张核心表：`token_usage`（Agent/章节/Token 用量）、`evaluation_history`（章节/评分/五维分数）、`agent_trace`（Agent/动作/耗时/输入输出哈希）。与 EventStore 物理隔离，因为叙事真相与运行遥测的生命周期和访问模式完全不同。
_Avoid_: 指标表、遥测表

### 决策透明化

**Glass-Box Decision Making** (玻璃盒决策):
Agent 决策过程可观测、可追溯、可审计的设计原则。区别于"黑盒"（只看结果）和"白盒"（暴露全部内部状态）——玻璃盒只暴露高价值的创意决策环节（Think/Evaluate 阶段的推理链），而不暴露执行环节（Write 阶段的正文生成过程）的内部运转。核心取舍：创意决策需要可解释性，正文生成需要心流保护。

**Reasoning Chain** (推理链):
Agent 在高价值决策环节的结构化思考过程。由扩展后的 JSON Schema 的 `reasoning` 字段携带——Writer 的 `ChapterOutline` 输出中的推理依据，Critic 评分中的 `critique_reasoning` 字段。存储于 `logs/reasoning/{trace_id}.json`，与正文产出物理分离。不在纯文本输出阶段（Write）捕获，避免污染核心资产。
_Avoid_: 思考日志、思维链（与 LLM 内部的 Chain-of-Thought 不同，这是决策层级的结构化记录）

**Trace ID** (追踪标识):
跨模块隐式关联的运行标识。采用 `contextvars` 机制在 `AutoRunner.run_chapter()` 入口生成，通过 Python 上下文变量隐式传播至 `llm_bus.chat()` 和 EventLog 存储，无需修改模块函数签名。用于将 Reasoning Chain、AgentTrace、EventLog 串联为完整的决策链路。与 Agent 角色的关系：一键生成，全程透明，在推理链文件中记录；通过 `novel debug --trace <trace_id>` 查看完整决策路径。
_Avoid_: 线程 ID、request_id（强调叙事上下文的关联而非技术层面的线程或请求）

### 状态投影

**State Projection** (状态投影):
从 EventLog 事件流归约为角色在任意时间点的可信状态快照。核心操作是"时间轴折叠"：按 chapter_id ASC 遍历指定角色的所有事件，依次应用 `INJURY`→身体状态变更、`HEAL`→恢复、`ITEM_GAIN/LOSS`→物品清单变更、`EMOTION_SHIFT`→情绪维度更新，最终输出该时间点的状态向量。区别于 CausalGraphAnalyzer（回答"发生了什么事件"）——State Projection 回答"事件导致了什么状态"。
_Avoid_: 因果图分析（功能互补但查询模式不同：图分析遍历关联，投影折叠时间线）

**State Projector** (状态投影器):
`ContextAssembler` 的运行时数据源（拟建）。在创作循环中自动为 Writer/Critic 注入截止当前章节的角色状态快照，作为 STATE MEMORY 权威层上下文的一部分。利用 `character_id` + `chapter_id` 的 SQLite 复合索引，全量投影（~2500 事件）预期耗时 <30ms，不构成创作循环的瓶颈。注入格式：`[State Snapshot] John: Left Arm (Broken), Mood (Depressed), Inventory (Sword)`。

### 搜索管道（SearchPipeline）

**SearchPipeline** (搜索管道):
构建于混合检索之上的统一检索入口。将用户查询路由到三个并行通道（VectorStore 语义 / FTS5 关键词 / EventStore 事件），经 RRF 融合后由 Cross-Encoder 重排序，返回精排后的 Chunk 列表。对外提供三个核心方法：`search()`（检索）、`rebuild_index()`（全量重建）、`incremental_update()`（增量更新）。实现于 `core/search_pipeline.py`。

**RRF 融合** (Reciprocal Rank Fusion):
多通道检索结果的排名融合算法。公式 `score(d) = Σ wᵢ / (k + rankᵢ(d))`，其中 `k=30`，EventStore 通道权重 `w=1.5`，向量和 FTS5 通道权重 `w=1.0`。不依赖原始分数，仅使用排名。在 SearchPipeline 中作为独立函数实现，位于 rrf_fusion()，用于将三通道的 top 15 结果融合为 top 50 候选列表。

**Cross-Encoder 重排序** (交叉编码重排序):
在 RRF 粗排后，对 (query, candidate_text) 对进行深度语义匹配精排。使用 `BAAI/bge-reranker-v2-m3` 模型，类级懒加载（`Reranker.get_model()`），自动设备检测（cuda > mps > cpu）。返回原始索引而非文本，便于 SearchPipeline 操作。最多处理 50 个候选对，取 top 5 注入 LLM 上下文。实现于 `core/reranker.py`。

**两级精度策略**:
检索系统的双模式设计：
1. **Agent 自治路径**（ToolRegistry 查询）：仅 RRF 融合、跳过 Cross-Encoder，延迟 <10ms，适用于实时知识填充
2. **ContextAssembler 路径**（每章首次上下文组装）：RRF + Cross-Encoder 完整管道，延迟 ~200ms，最高精度
同一 SearchPipeline 通过 `search(use_reranker=True/False)` 参数切换。

**Dirty Index 策略** (脏索引策略):
FTS5 实时增量更新（commit/stash 时立即同步），VectorStore 最终一致性（仅 `novel reindex` 时全量重建）。VectorStore 的脏数据由 Cross-Encoder 精排防线兜底——旧向量即使被召回，重排序阶段也会因与当前查询语义不匹配而降权。实现于 `SearchPipeline.incremental_update()`。

### 分块系统

**MarkdownChunker** (Markdown 分块器):
按 Markdown 标题层级的递归分块器。分层策略：`#` 一级标题 → 顶级边界 → 超 512 tokens 则按 `##` 切 → 超限则在空行处切 → 最终按句号/问号/感叹号切。默认 `max_chunk_tokens=512`，`overlap_tokens=64`。分离 YAML Frontmatter 存入 metadata。返回 `Chunk` 对象列表，每个 chunk 拥有全局唯一的 `chunk_id`。实现于 `core/chunker.py`。

**Chunk ID** (分块标识):
分块的全局统一键，格式 `{source}_{doc_stem}_p{chunk_index}`（例 `canon_world_rules_p0`），确定性生成（不含 hash），增量更新时直接覆盖。在三存储间充当关联桥：VectorStore 用 chunk_id 做 `ref_doc_id`，FTS5 用 chunk_id 做 `chunks` 表主键，Cross-Encoder 通过 chunk_id 取原文。实现于 `schemas/search.py` 的 `Chunk` 模型。

**ChunkSource** (分块来源):
分块来源枚举：`CANON`（世界观设定）、`SUBCONSCIOUS`（灵感碎片）、`CHARACTER`（角色卡片）、`DRAFT`（章节正文）。每个 chunk 携带 source 标签，在 ContextAssembler 中据此分配权威层级。实现于 `schemas/search.py`。

### FTS5 全文索引

**Fts5Store** (FTS5 全文索引管理器):
基于 SQLite FTS5 扩展的全文索引。使用 unicode61 分词器（不引入 jieba），中文按单字 tokenize。双表结构：`chunks`（分块元数据 + 原文，给 Cross-Encoder 读取） + `chunks_fts`（FTS5 虚拟表，external content 模式关联 chunks）。查询端可选停用词过滤（20 行函数）。FTS5 对复杂长句空结果是正常行为，由 VectorStore 语义通道补位。实现于 `storage/fts5.py`。数据库文件 `.novel.fts5.db` 独立于事件账本和指标库，生命周期与搜索索引绑定。

**FTS5 预分词**:
未在 FTS5 层面对中文进行预分词。unicode61 的分词单位是单个汉字。查询端也保持原样，不做 jieba 分词。接受 FTS5 在长查询时的低召回——这是特性：精确匹配保证专有名词（角色名、地名）的检索精度，语义补位由 VectorStore 处理。

### 混合检索

**Hybrid Retrieval** (混合检索):
双轨并行的检索策略。精确事实召回：EventStore 直接 SQL 查询（按 character_id + event_type + chapter_id 范围）；语义关联召回：向量搜索 canon/subconscious 索引。两者在 ContextAssembler 中合并注入。核心原则：结构化数据走 SQL，非结构化数据走向量。
_Avoid_: BM25 混合、统一检索

**Causal Chain** (事件因果链):
EventStore 中事件之间的因果关联。通过 `caused_by` 外键和 `related_event_ids` 字段建立事件 DAG（有向无环图）。支持回答"这个伤口是怎么来的"、"这个知识是从哪获得的"等因果追溯问题。

**Causal Graph Analysis** (因果图分析):
基于 `networkx` 从 EventStore 构建因果 DAG 图，提供图分析能力。`CausalGraphAnalyzer` 类（`core/causal_graph.py`）支持：`get_causal_path()` 最短因果路径、`get_central_events()` 介数中心性分析（识别叙事关键节点）、`get_character_subgraph()` 角色事件子图、`get_upstream_chain()` / `get_downstream_chain()` 因果追溯、`get_high_impact_events()` 高压强事件筛选。依赖可选的 `networkx` 库（`pip install opennovel[phase2]`）。

**CanonChecker** (世界观规则校验器):
	基于 ADR 0006 安全围栏"Canon 不可违背"原则的实现。`CanonChecker` 类（`core/canon_checker.py`）从 `canon/` 目录的 Markdown 文件中解析 `## 规则` 章节的编号列表和关键约束陈述，提取规则并按类型（negation / exclusive / positive）分类。`check_text()` 使用关键词匹配 + 肯定/否定语境检测 Agent 文本是否违反规则。`SafetyFence.check_canon_integrity()` 集成该方法，violation 级别阻断，suggestion 级别提醒。不依赖 LLM，纯 Python 标准库实现。测试位于 `tests/test_canon_checker.py`。

**Canon Exemption** (规则豁免):
允许作者在特定场景下临时跳过世界观规则检查的机制。支持两个层级：行内豁免（`<!-- canon_exempt: rule_id -->` Markdown 注释，精确到段落，优先级高）和章节豁免（章节 Frontmatter 的 `canon_exemptions` 字段，作用于整章）。豁免标记在 `CanonChecker.check_text()` 检测到违规时被二次校验——如果原文包含匹配的豁免标记，违规降级忽略或降为 INFO。不依赖 LLM。实现于 `core/canon_checker.py` 的 `_check_exemptions()` 方法。

**Agent Autonomy** (Agent 自治引擎):
	基于 ADR 0006 的 Mid-Write 工具调用实现。`ToolCallParser` 从 LLM 输出中解析 `##TOOL_CALL##` 标记（格式：工具名|查询内容|查询原因），`ToolCallExecutor` 将请求路由到 ToolRegistry 执行，`AutonomousWriteLoop` 管理多轮交互循环（含 SafetyFence 约束）。Writer.`write_with_autonomy()` 整合该能力，创作 Prompt 末尾自动注入工具调用协议说明。AutoRunner 在 safety_fence 启用时自动使用自治模式。测试位于 `tests/test_agent_autonomy.py`。

**Global Config** (全局配置):
	跨项目的 OpenNovel 默认设置。`GlobalConfig` 类（`core/global_config.py`）从项目根目录逐级向上搜索 `.opennovel.yaml` 配置文件，提供三层模型路由（novel.yaml > .opennovel.yaml > 硬编码）。默认模型 `deepseek/deepseek-v4-flash`。配置文件位于项目根 `.opennovel.yaml`。

**Workspace** (小说工作区):
	所有小说项目的统一存放目录。默认位置为 `E:\Pythonproject\OpenNovel\novels/`。`novel init <项目名>` 在 workspace 下创建项目，`novel init .` 在当前目录创建。`novel list` 列出工作区中所有项目及统计信息，`novel config` 查看或修改全局配置。