# 0008 — OpenNovel V3.0 GUI 全架构（已废弃 / Superseded）

> **状态**：已废弃。V3.0 GUI（Tauri 2 + React 19 + TypeScript + FastAPI + WebSocket）已被删除，当前 OpenNovel 无 GUI，仅保留 CLI 与 MCP Server。未来若重新设计 GUI，需另起新 ADR。

Tauri 2 + React 19 + TypeScript 桌面端重写，FastAPI + WebSocket 后端，替代 PySide6 GUI。

## 背景

V2.0 GUI（PySide6）存在架构性问题：

- **信号/槽散落**：`AppState` 10 个独立信号，多个面板漏接导致主题不刷新、搜索标签反转等 bug
- **面板互斥**：`QStackedWidget` 强制 Chat/Search/Log/Draft 四者互斥，无法同时使用
- **固定布局**：三栏 QSplitter，不支持面板折叠/拖拽调整
- **大量 stub**：`_editor_find` / `_editor_replace` / `_run_diagnose` 等核心功能未实现
- **Agent 系统 bug**：`auto_runner.py:799` 变量遮蔽、工具调用 XML 泄漏到消息历史、Manager 未注入 CANON 上下文

参考项目：Vela（Electron + React）、TOKENICODE（Tauri 2 + React + Zustand 10 stores）、TOKENICODE DeepSeek Alpha（Tauri 2 + DeepSeek 魔改版）。

## 决策

### 第 1 层：通信协议

**单持久 WebSocket + task_id 多路复用**。所有 Agent 操作通过 WS 双向 JSON 通信，REST 用于 CRUD（文件/角色/大纲/会话）。

```
WebSocket: ws://host/api/v1/ws          ← 全局唯一连接
REST:      /api/v1/projects/{id}/*      ← CRUD 操作

WS 客户端 → 服务端:
  {"action":"write","task_id":"uuid-1","params":{...}}
  {"action":"accept","task_id":"uuid-1"}
  {"action":"stop","task_id":"uuid-2"}

WS 服务端 → 客户端:
  {"type":"agent.state_changed","session_id":"sess_42","task_id":"uuid-1","payload":{...}}
  {"type":"agent.stream_chunk","session_id":"sess_42","task_id":"uuid-1","payload":{...}}
  {"type":"agent.evaluation","session_id":"sess_42","task_id":"uuid-1","payload":{...}}
```

**REST 路由树**：

```
/api/v1/
├── /projects                    # 项目管理 (CRUD)
├── /projects/{id}/files         # 文件树 + 读写
├── /projects/{id}/chapters      # 章节列表 + 详情
├── /projects/{id}/characters    # 角色 CRUD
├── /projects/{id}/outlines      # 大纲读写
├── /projects/{id}/search        # 全局搜索 (FTS5+向量+RRF)
├── /projects/{id}/sessions      # 会话管理 (CRUD + 导出 + 软删除)
├── /projects/{id}/agents        # Agent 操作 (write/auto/revise/evaluate/commit/stash)
│   ├── POST /write              # 单章写作
│   ├── POST /auto               # 全自动创作
│   ├── POST /revise             # 修订指定章节
│   ├── POST /evaluate           # 仅评分
│   ├── POST /commit             # 5步审阅提交
│   ├── POST /stash              # 存入灵感
│   └── GET  /{task_id}/status   # 任务状态
├── /intent/parse                # 自然语言意图解析
├── /health                      # 健康检查
└── /system/shutdown             # 优雅退出
```

**MCP Server 保留不变**——继续服务 Claude Desktop / Cursor 等外部客户端，和 GUI 后端是独立的两条线。

### 第 2 层：桌面框架

| 决策 | 选择 | 理由 |
|:---|:---|:---|
| 框架 | **Tauri 2** | 包体积 ~5MB（vs Electron 120MB+），Rust 后端可复用，TOKENICODE DSA 已验证 Windows 打包链路 |
| 前端 | React 19 + TypeScript 5.8 | TOKENICODE 同栈，类型安全 |
| 样式 | Tailwind CSS 3.4 | 写作工作台暖色调色板，JIT 编译 |
| 编辑器 | CodeMirror 6 | 流式/差分/语法高亮成熟生态 |
| 状态管理 | Zustand 5 + immer | TOKENICODE 同方案（10 stores），轻量 |
| 打包 | pnpm + Vite 6 | 快于 Webpack，HMR 即时 |

**项目结构**（同仓库，`desktop/` 与 `opennovel/` 并列）：

```
OpenNovel/
├── opennovel/                    # Python 后端（保持不变）
│   ├── api/                      # ★新增：FastAPI 后端
│   │   ├── main.py               # FastAPI app + /ws endpoint + /health + /shutdown
│   │   ├── routes/               # projects / files / chapters / characters / outlines / search / sessions / agents
│   │   ├── ws/
│   │   │   ├── manager.py        # ConnectionManager: session↔ws映射 + TaskContext管理
│   │   │   └── handler.py        # WS action路由 → Agent事件发射
│   │   └── state_machine.py      # AgentStateMachine (7状态枚举 + 转换验证)
│   ├── agents/                   # 保持不变
│   ├── core/                     # 保持不变 + AutoRunner.run_chapter_async()
│   ├── storage/
│   │   └── sessions.py           # ★新增：SessionStore
│   └── ...
├── desktop/                      # ★新增：Tauri 2 + React 前端
│   ├── package.json / tsconfig.json / vite.config.ts / tailwind.config.ts
│   ├── src/
│   │   ├── main.tsx / App.tsx
│   │   ├── stores/              # 15 Zustand Stores + EventBus
│   │   ├── components/          # layout / editor / chat / pipeline / panels / search / modals / common
│   │   ├── hooks/               # useWebSocket / useAutoSave / useKeyboard
│   │   └── types/               # events.ts / domain.ts
│   └── src-tauri/               # Tauri 2 Rust: 文件监听 + 心跳 + 优雅退出
└── docs/adr/
```

### 第 3 层：状态管理

**EventBus + Zustand Stores，三项硬约束防退化**。

#### EventBus（约束 1-3）

```typescript
// 约束 2：判别联合 AgentEvent，handler 参数类型由 type 自动推断
type AgentEvent =
  | { type: 'agent.state_changed';    session_id: string; task_id: string; payload: AgentStateChangedPayload }
  | { type: 'agent.stream_chunk';     session_id: string; task_id: string; payload: StreamChunkPayload }
  | { type: 'agent.evaluation';       session_id: string; task_id: string; payload: EvaluationPayload }
  | { type: 'agent.chapter_complete'; session_id: string; task_id: string; payload: ChapterCompletePayload }
  | { type: 'agent.error';            session_id: string; task_id: string; payload: AgentErrorPayload }
  | { type: 'agent.token_usage';      session_id: string; task_id: string; payload: TokenUsagePayload }
  | { type: 'project.file_changed';   payload: FileChangedPayload }        // 项目级事件，无 session
  | { type: 'search.results';         payload: SearchResultsPayload };     // 项目级事件，无 session

// 约束 1：单向数据流 — Store 不 emit 事件
// 约束 2：判别联合锁死 — 新增类型不改联合 → 编译报错
// 约束 3：只导出 on，不导出 emit — 唯一 emit 调用点在 wsClient.ts + Tauri bridge

// 会话级订阅：on(type, handler, sessionId) — 仅接收该 session 的事件
// 全局订阅：on(type, handler) — 接收所有事件（pipelineStore/metricsStore/projectStore 用）
```

**数据流全景**：

```
用户点击"写作"
  → Component 调用 agentStore.startTask()
  → wsClient.send({action:'write', task_id:'uuid-1', params:{...}})
  → FastAPI 创建任务，返回 task_id
  → WS 推送 agent.state_changed {state:'thinking'}
  → EventBus → agentStore._setState() + pipelineStore._handleStateChange()
  → WS 推送 agent.stream_chunk × N
  → EventBus → editorStore._appendStreamChunk() + chatStore._appendStreamChunk()
  → WS 推送 agent.evaluation {scores:87}
  → EventBus → criticStore._setEvaluation() + chatStore._addEvaluationMessage()
  → 右侧面板自动弹出评分卡片 + Chat 流跟随
用户点击"接受"
  → wsClient.send({action:'accept', task_id:'uuid-1'})
  → 后端处理 → WS 推送 state:'idle' → 回到 chat 状态
```

#### 15 Store 全貌

| # | Store | 复杂度 | EventBus 订阅 | REST | 职责 |
|:---|:---|:---|:---|:---|:---|
| 0 | eventBus | 基础设施 | — | — | TypedEventBus + wsClient |
| 1 | uiStore | 简单 | 无 | 无 | 布局：nav/panel/theme/focusMode |
| 2 | projectStore | 简单 | `project.file_changed` | CRUD | 项目元数据 + novel.yaml |
| 3 | sessionStore | 简单 | 无 | CRUD | 会话 CRUD + 日期分组 + 软删除 |
| 4 | chapterStore | 简单 | `project.file_changed` | CRUD | 章节列表 + 元数据 |
| 5 | characterStore | 简单 | 无 | CRUD | 角色列表 + 详情（含详情缓存） |
| 6 | outlineStore | 简单 | 无 | CRUD | 大纲树（客户端解析 MD 标题） |
| 7 | **editorStore** | **复杂** | `stream_chunk`, `state_changed` | 读/写文件 | 三态流式(append/overlay/split) + diff + dirty |
| 8 | agentStore | 中等 | `state_changed`, `error` | WS | 任务追踪 + 7 状态 + 决策 |
| 9 | pipelineStore | 中等 | `state_changed` | 无 | 阶段进度 + 时间线 |
| 10 | chatStore | 中等 | `stream_chunk`, `evaluation`, `state_changed` | 无 | ChatMessage 判别联合 + 流式生命周期 |
| 11 | criticStore | 简单 | `evaluation` | 无 | 评分缓存 + 决策状态 |
| 12 | metricsStore | 简单 | `token_usage`, `chapter_complete` | 无 | Token 消耗 + 成本追踪 |
| 13 | searchStore | 简单 | `search.results` | POST | 全局搜索 |
| 14 | commandStore | 简单 | 无 | 无 | 命令注册表 + fuse.js 模糊匹配 |
| 15 | logStore | 简单 | 无（LogManager 回调） | 无 | 系统日志环形缓冲 (2000条) |

### 第 4 层：布局骨架 (AppShell)

**CSS Grid 写作工作台布局**：

```
┌────────┬────────────────────────────────┬───────────────────┐
│ 48px   │                                │                   │
│ 图标导航 │      📝 编辑器区域              │   右侧面板 (320px) │
│        │      (relative 容器)            │   ┌─────────────┐ │
│ 📁 章节 │      左侧滑出面板 overlay 在此   │   │ Pipeline    │ │
│ 👤 角色 │                                │   │ (48px条)    │ │
│ 📋 大纲 │      CodeMirror 6              │   ├─────────────┤ │
│ 💬 会话 │      流式输出 / 差异高亮         │   │ Chat /      │ │
│ ⚙ 设置 │                                │   │ Evaluation  │ │
│        │                                │   │ Card        │ │
├────────┴────────────────────────────────┴───────────────────┤
│  Ctrl+K  │  第3章 │ 2,456字 │ Think→Write ⏳ │ ¥0.03  [▾]  │
└─────────────────────────────────────────────────────────────┘
```

关键设计：
- **48px 图标条**：点击切换左侧 overlay 面板，再点同一个折叠
- **240px overlay 滑出面板**：不挤压编辑器宽度，Esc/点击外部关闭，右边缘可拖拽 (200-400px)
- **28px 底部状态栏**：左(Ctrl+K 入口) / 中(章节+字数) / 右(Pipeline+成本) / 展开终端按钮
- **底部终端**：点击状态栏展开 200px，三 Tab (搜索/日志/用量)，顶部边缘拖拽 (120-400px)
- **F11 专注模式**：隐藏所有面板，仅编辑器 + 顶部边缘悬停退出

### 第 5 层：右侧面板三态切换

根据 Agent 状态（`agentStore.agentState`）驱动右侧面板自动切换布局：

```
State 1: chat (Agent 空闲)
┌───────────────────┐
│ 💬 Chat           │  ← Chat 占满全部
└───────────────────┘

State 2: working (Agent 工作中)
┌───────────────────┐
│ Pipeline     [▾]  │  ← 48px 紧凑条 (SVG 圆环 + 连环圆点)
├───────────────────┤
│ 💬 Chat           │  ← Chat 占剩余空间
└───────────────────┘

State 3: decision (等待决策)
┌───────────────────┐
│ Pipeline  ✅ Done │
├───────────────────┤
│ 📊 Critic: 87     │  ← 评分卡片（五维 + 问题 + 操作按钮）
│ [接受][修订][放弃] │
├───────────────────┤
│ 💬 Chat (30%)     │
└───────────────────┘
```

Agent 7 原子状态 → 面板模式映射：

| Agent 状态 | 右侧面板模式 | Pipeline 条 | 评分卡片 | Chat |
|:---|:---|:---|:---|:---|
| `idle` / `error` / `fatal` | chat | 隐藏 | 隐藏 | 占满 |
| `thinking` / `writing` / `evaluating` / `revising` / `committing` | working | 置顶 | 隐藏 | 剩余空间 |
| `awaiting_decision` + 无评分 | decision | Done | 隐藏 | 剩余空间 |
| `awaiting_decision` + 有评分 | decision | Done | 滑入 | 压缩至 30% |

Pipeline 面板阶段连环圆点：`● Think ─ ● Write ─ ○ Evaluate ─ ○ Update`，运行中蓝色闪烁，完成绿色，错误红色。

### 第 6 层：对话系统

**ChatMessage 判别联合**：

```typescript
type ChatMessage =
  | { id: string; role: 'user';       content: string;                  timestamp: string }
  | { id: string; role: 'assistant';  content: string; phases: Phase[]; timestamp: string; taskId: string }
  | { id: string; role: 'system';     content: string; kind: 'info'|'warning'|'error'; timestamp: string }
  | { id: string; role: 'evaluation'; evaluation: EvaluationCard;       timestamp: string; taskId: string };
```

**消息时间线**（一次 `startWrite`）：

```
用户消息 → assistant 消息 (streaming + phases 标签) → evaluation 消息 (评分卡片嵌入) → system 消息 (决策反馈)
```

- think 过程不在 Chat 中显示（仅 Pipeline 面板展示），思考文本留在 Pipeline
- assistant 消息的 `phases` 字段渲染为消息角落小标签（"✓ Think → ✓ Write → ⏳ Eval"）
- evaluation 作为独立消息类型嵌入消息流中 assistant 消息之后

**SmartInputBar 三模式**：

| 模式 | 编辑器行为 | 使用场景 |
|:---|:---|:---|
| `replace` (默认) | AI 输出替换选中文本，diff 面板显示 | 修订、改写 |
| `append` | AI 内联追加到编辑器末尾，浅蓝高亮 | 续写 |
| `split` | 右侧分栏展示 AI 输出，原文左侧保留 | 对比审阅 |

**意图解析**（三层降级）：
1. Tier 1: 斜杠命令精确匹配 (`/write`, `/写`, `/w`, ...)
2. Tier 2: LLM 分类 → `POST /api/intent/parse`（后端执行，保护 API Key，注入项目上下文）
3. Tier 3: 关键词回退（纯客户端，无网络开销）

**灵感按钮**（混合模式）：有文本→ stash 当前输入；无文本→ 打开左侧灵感浏览面板。

### 第 7 层：命令面板 (CommandPalette)

- `Ctrl+K` 打开模态 overlay 命令面板
- `fuse.js` 模糊匹配（title ×2.0, keywords ×1.5, category ×0.5 权重）
- 空搜索：最近使用命令优先（最多 10 个）
- 键盘导航：↑↓ + Enter 执行，Esc 关闭
- 所有菜单操作 + Agent 操作统一注册为 `Command`

**Command 注册体系**：

| 分类 | 命令示例 |
|:---|:---|
| writing | 写新章、自动创作、修订、存灵感 |
| editing | 查找、替换、格式化 |
| navigation | 切换面板、跳转章节、切换会话 |
| project | 新建项目、打开项目、设置、提交 |
| view | 主题切换、专注模式、面板显隐、字体缩放 |
| agent | Write、Revise、Evaluate、Commit、Stash、Stop |

### 第 8 层：EditorStore

**React ↔ CM6 分层**：CM6 控文档/content，Zustand 控业务状态（mode/diff/dirty）。

```typescript
interface EditorState {
  filePath: string | null;
  content: string;                   // CM6 onChange 同步
  originalContent: string;           // dirty 检测基准

  // 流式三态
  streaming: boolean;
  streamingTaskId: string | null;
  streamingMode: 'inline_append' | 'overlay_diff' | 'split_diff' | 'preparing' | null;
  streamingStartPos: number;
  
  // Diff
  diffOriginal: string | null;
  diffModified: string | null;
  diffViewVisible: boolean;

  // 保存
  dirty: boolean;
  lastSavedAt: string | null;
  autoSaveEnabled: boolean;
}
```

**PREPARING 乐观态**——组件层发起请求时立即进入，编辑器显示闪烁占位符 `▌`，掩盖后端 Think 阶段的延迟：

```
PREPARING ──[WS state=thinking]──→ PREPARING（保持闪烁）
          ──[WS state=writing]────→ inline_append / overlay_diff / split_diff
          ──[WS error]────────────→ IDLE + 移除占位符
          ──[30s 超时]────────────→ IDLE + "AI 响应超时"
```

**脏状态追踪三来源**：
1. `content !== originalContent` — 用户手动编辑
2. `streaming && streamingMode === 'inline_append'` — AI 流式追加中
3. `diffViewVisible && !accepted` — 有未接受的 diff

**自动保存**：60s 定时器，仅 dirty=true 时触发 → 保存到 `.snapshots/autosave/{filename}.autosave.md`

### 第 9 层：15 Store 全貌

详见第 3 层 Store 全貌表。跨 Store 通信遵循约束 1：组件层 `useEffect` 中合并多个 Store 的读取，Store 之间不相互调用。

### 第 10 层：CodeMirror 6 编辑器

**动态 Compartment 体系**——三种 streaming mode 各用一个独立 Compartment，通过 `compartment.reconfigure()` 动态启用/禁用：

```
NovelEditor (React)
  └── EditorView (CM6)
       ├── Base Extensions (常驻): markdown() | lineNumbers() | history() | keymap() | frontmatterField | opennovelTheme
       │
       ├── Compartment: inlineStream   ← inline_append 时启用
       │    ├── streamingRangeField     (StateField: 追踪流式范围 {from, to})
       │    ├── streamingViewPlugin     (ViewPlugin: viewport-aware Decoration + 50px 阈值滚动)
       │    └── streamingTxFilter       (transactionFilter: 用户编辑 → 自动移除流式高亮)
       │
       ├── Compartment: diffOverlay    ← overlay_diff 时启用
       │    └── diffRangeField          (StateField: 高亮被修订的原文范围)
       │
       └── Compartment: splitView      ← split_diff 时启用
            └── 第二个只读 EditorView   (显示 AI 输出)
```

**inline_append 关键细节**：
- `transactionFilter` 标记 AI 追加的 transaction 为 `ai-streaming` 用户事件 → 不污染 undo history
- `ViewPlugin` 仅对 `viewport.visibleRanges` 内的区域创建 Decoration → 大文件性能
- 自动滚动仅在新内容距离视口底部 < 50px 时触发 → 防止回看时被滚走
- 流式追加不触发 `markDirty()`（通过 `Transaction.userEvent` 区分）

**overlay_diff**：CM6 仅高亮原文范围，AI 修改文本在外部 React `DiffView` 组件中展示（红删绿增 + 接受/拒绝）。

**split_diff**：创建第二个只读 `EditorView`，左右分栏对比原文和 AI 输出。

### 第 11 层：左侧面板内容

5 个面板，按复杂度排序：

| 面板 | 组件 | 数据源 | 关键交互 |
|:---|:---|:---|:---|
| **章节列表** | ChapterList → ChapterItem × N | chapterStore.chapters | 点击跳转、拖拽排序、右键菜单(重命名/删除/标记完成)、新建 |
| **会话历史** | SessionList → SessionGroup → SessionItem × N | sessionStore.sessions (REST, 按日期分组) | 点击加载历史、右键(置顶/归档/导出/删除 5s 撤销) |
| **大纲树** | OutlineTree → OutlineNode × N (递归) | outlineStore.tree (客户端正则解析 MD 标题) | 点击跳转、双击打开关联章节、编辑模式(直接编辑 MD 源码)、关联章节 |
| **角色面板** | CharacterList → CharacterDetail (单页详情) | characterStore (详情缓存 `Map<string, CharacterDetail>`) | 列表→点击→详情替换列表(带"< 返回")、SVG 五维情感雷达图、物理状态条、关系网络、发送给 Agent |
| **设置面板** | SettingsPanel (表单 + YAML 编辑器) | projectStore.currentProject.novelYaml | 表单字段编辑 → YAML 只读同步、YAML 获焦 → 表单灰显(单向同步)、保存前 yaml.parse() 校验 |

**角色面板缓存**：`detailCache: Map<string, CharacterDetail>`，订阅 `project.file_changed` 时按 path 匹配失效对应缓存。

**设置面板 YAML 同步**：`yamlEditorActive: boolean` 开关——YAML 编辑器获焦时表单灰显，失焦时表单恢复可编辑。保存时以活跃侧为准。

### 第 12 层：底部终端

28px 状态栏点击展开 → 200px 终端，三 Tab：

| Tab | 组件 | 数据源 | 功能 |
|:---|:---|:---|:---|
| **搜索结果** | SearchResults (SearchBar + FilterBar + ResultList) | searchStore (POST search) | 结果列表含匹配度百分比 + 代码片段 + "插入"按钮（需感知编辑器状态：流式/Diff 中禁用） |
| **系统日志** | LogViewer (Toolbar + LogList 虚拟滚动) | logStore (LogManager 回调直接写入，不经过 EventBus) | 级别过滤 + 模块过滤 + 暂停 + 2000 条环形缓冲 + 自动滚动 |
| **用量仪表板** | MetricsDashboard (StatCards + TokenTrend + CostBreakdown) | metricsStore (EventBus 驱动) | 三数字卡片 + 纯 CSS div 柱状图 + 按章节成本表格 |

**搜索"插入"按钮感知编辑器状态**：`canInsert = !streaming && !diffViewVisible`，禁用时提示"请先完成当前 AI 交互"。

### 第 13 层：FastAPI 后端

**WebSocket 连接管理器**：

```python
class ConnectionManager:
    _connections: dict[str, WebSocket]   # session_id → ws
    _tasks: dict[str, TaskContext]       # task_id → 任务上下文
    
    # TaskContext: task_id, session_id, action, state, decision_event(asyncio.Event), decision_value
    
    async def send_event(session_id, event)  # 向指定 session 推送
    async def wait_for_decision(task_id)      # 阻塞等待客户端决策 (asyncio.Event.wait, 5分钟超时)
    async def signal_decision(task_id, value) # WS handler 收到决策 → 唤醒 AutoRunner
```

**AutoRunner 异步生成器改造**：

Phase 1（当前实现）用 `asyncio.to_thread` 包裹每个同步阶段，Phase 2 后续逐模块原生 async。

```python
async def run_chapter_async(chapter_id, hint, session_id, task_id, manager) -> AsyncIterator[dict]:
    # Stage 1: THINKING
    yield state_changed('thinking', 'think', 0)
    outline = await asyncio.to_thread(self.writer.think, chapter_id, hint)
    yield state_changed('thinking', 'think', 100)
    
    # Stage 2: WRITING (streaming via Queue bridge)
    yield state_changed('writing', 'write', 0)
    queue = asyncio.Queue()
    asyncio.create_task(asyncio.to_thread(_sync_stream_generator, queue))
    while True:
        msg_type, payload = await queue.get()
        if msg_type == 'done': break
        yield stream_chunk(payload)
    
    # Stage 3: EVALUATING
    yield state_changed('evaluating', 'evaluate', 0)
    evaluation = await asyncio.to_thread(self.critic.evaluate, chapter_id, text, outline)
    yield evaluation_event(evaluation)
    
    # Stage 4: AWAITING_DECISION
    yield state_changed('awaiting_decision', 'done', 100)
    decision, feedback = await manager.wait_for_decision(task_id)
    # accept → write chapter → IDLE
    # accept_and_commit → write → COMMITTING → IDLE
    # revise → REVISING → loop back to WRITING
    # reject → IDLE
```

**Agent 状态机（7 原子状态）**：

```
IDLE → THINKING → WRITING → EVALUATING → AWAITING_DECISION → IDLE
                     ↑                           │
                     └── REVISING ←──────────────┘ (修订)
                                                  
                    AWAITING_DECISION ──[accept_and_commit]──→ COMMITTING → IDLE
```

COMMITTING 不在自动流转中——仅用户显式点击"接受并提交"触发 `POST /agents/commit`。

**SessionStore 数据库**：

`.novel.sessions.db`（独立于 `.novel.db` 和 `.novel.metrics.db`）：

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT DEFAULT '',
    pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
    chapter_id TEXT, message_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    deleted_at TEXT   -- 软删除标记，5s撤销窗口
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT CHECK(role IN ('user','assistant','system','evaluation')),
    content TEXT DEFAULT '', metadata TEXT,   -- JSON: phases/evaluation/task_id/kind
    created_at TEXT DEFAULT (datetime('now'))
);
```

### 第 14 层：Tauri 集成

**fs-change 事件桥接**：

```
文件系统变更 → notify crate (Rust 后台线程)
  → app.emit("fs-change", {path, kind})
    → 前端 listen("fs-change")
      → EventBus emit({type:"project.file_changed", payload:{path, kind}})
        → projectStore.refreshSummary() (500ms debounce)
        → chapterStore.loadChapters() (draft/ 变更)
        → characterStore.invalidateCache(path) (characters/ 变更)
```

过滤规则：仅 `.md` / `.yaml` / `.yml`，排除 `.snapshots/` 和 `.index/`。

**Sidecar 进程管理**：

FastAPI 不作为 Tauri sidecar（避免 30s 启动超时），由前端 `Command.create` spawn：

```
App 启动
  → 检测 FastAPI 是否已在运行 (GET /health)
  → 未运行 → 自动探测 Python 路径 (见下文) → spawn uvicorn → 30s 等待就绪
  → 心跳: Rust check_sidecar_health() 每 5s，连续 3 次失败通知前端
App 关闭
  → ExitRequested → POST /system/shutdown → 等待 3s → 强制 kill
```

**跨平台 Python 启动命令动态拼接**：

```typescript
// desktop/src/main.tsx — Python 路径探测

type Platform = 'win32' | 'darwin' | 'linux';

async function detectPythonPath(projectRoot?: string): Promise<string[]> {
  const platform = navigator.platform.toLowerCase() as Platform;
  const isWindows = platform === 'win32';
  const isMac = platform === 'darwin';
  const isLinux = !isWindows && !isMac;

  // 1. 优先探测虚拟环境（如果用户配置了 .venv）
  if (projectRoot) {
    const venvPython = isWindows
      ? `${projectRoot}/.venv/Scripts/python.exe`
      : `${projectRoot}/.venv/bin/python`;
    if (await fileExists(venvPython)) {
      return [venvPython];
    }
  }

  // 2. 系统 Python 探测
  const candidates = isWindows
    ? ['python.exe', 'py', '-3']                         // Windows: python.exe → py -3
    : isMac
      ? ['python3', 'python']                             // macOS: python3 → python (系统自带)
      : ['python3', 'python'];                            // Linux: python3 → python

  for (const cmd of candidates) {
    if (await canExecute(cmd)) {
      return isWindows
        ? ['cmd', '/c', cmd]                              // Windows 需通过 cmd /c 执行
        : [cmd];
    }
  }

  throw new Error('未找到可用的 Python 解释器。请安装 Python 3.11+ 或配置虚拟环境。');
}

async function fileExists(path: string): Promise<boolean> {
  try {
    if ('__TAURI_INTERNALS__' in window) {
      const { exists } = await import('@tauri-apps/plugin-fs');
      return await exists(path);
    }
    // Vite dev 模式：用 fetch 探测
    const res = await fetch(`file://${path}`);
    return res.ok;
  } catch { return false; }
}

async function canExecute(cmd: string): Promise<boolean> {
  try {
    const checkCmd = cmd === 'python3' || cmd === 'python'
      ? [cmd, '--version']
      : cmd.split(' ');
    // Tauri 下用 Command，dev 下用 child_process
    if ('__TAURI_INTERNALS__' in window) {
      const output = await Command.create(checkCmd[0]!, checkCmd.slice(1)).execute();
      return output.code === 0;
    } else {
      const { execSync } = await import('child_process');
      execSync(`${checkCmd.join(' ')}`, { stdio: 'ignore' });
      return true;
    }
  } catch { return false; }
}
```

**打包配置**：

- Python 分发策略：不打包 Python，要求用户已安装 3.11+，自动检测 + 引导安装
- Tauri bundle：Windows NSIS (.exe) + MSI (.msi)，macOS DMG，Linux AppImage/deb/rpm
- 自动更新：Tauri updater，更新源指向本仓库 GitHub Releases

### 第 15 层：开发工作流

```bash
# 终端 1：启动 FastAPI 后端
cd E:\Pythonproject\OpenNovel
.venv\Scripts\activate
uvicorn opennovel.api.main:app --host 127.0.0.1 --port 8765 --reload

# 终端 2：启动 Tauri 开发服务器
cd E:\Pythonproject\OpenNovel\desktop
pnpm install
pnpm tauri dev   # 同时启动 Vite dev server + Tauri 窗口

# 类型生成（后端 OpenAPI → 前端 TypeScript）
pnpm generate-types
```

## 与 V2.0 GUI 的差异

| 维度 | V2.0 (PySide6) | V3.0 (Tauri 2) |
|:---|:---|:---|
| 桌面框架 | PySide6 (Qt) | Tauri 2 + React 19 |
| 状态管理 | AppState 单例 + 10 散落信号 | 15 Zustand Stores + EventBus (约束1/2/3) |
| 面板布局 | 固定三栏 QSplitter + QStackedWidget 互斥 | CSS Grid 可调整 + 左侧 overlay + 右侧三态自适应 |
| 编辑器 | QPlainTextEdit (~200行正则高亮) | CodeMirror 6 (流式/diff/Markdown 扩展) |
| 流式输出 | append_streaming_text + 强制滚底 | inline_append + 50px 阈值防滚走 + transactionFilter undo 隔离 |
| 会话管理 | 无 | 完整 CRUD + 置顶/归档/导出/5s 软删除 |
| 搜索 | QStackedWidget 互斥面板 (有 label 反转 bug) | 底部终端 Tab + fuse.js 模糊匹配 |
| 主题 | QSS 手写 (base+light+dark)，editor 不刷新的 bug | Tailwind CSS 暖色调色板 + class 切换 |
| 命令入口 | 菜单栏 ~30 个操作 | Ctrl+K 命令面板 + fuse.js 模糊匹配 |
| AI 对接 | LiteLLM 直连 (同步阻塞) | FastAPI + WebSocket (异步生成器 + asyncio 决策等待) |
| 文件监听 | Qt 文件系统监听 | notify crate (Rust) → Tauri event → EventBus |
| 打包体积 | PyInstaller ~80MB | Tauri ~5MB + Python 环境检测 |

## 参考

- [Vela](https://github.com/heider-x/vela) — AI 小说写作 IDE，Electron + React + Zustand + 轻量向量引擎
- [TOKENICODE](https://github.com/yiliqi78/TOKENICODE) — Claude Code 桌面 GUI，Tauri 2 + React 19 + Zustand 5 (10 stores) + CodeMirror 6
- [TOKENICODE DeepSeek Alpha](https://github.com/mistydew/tokenicode-deepseek-alpha) — TOKENICODE 魔改版，Skills 面板 + MCP 扫描 + Ollama 本地模型管理
- ADR 0002 — 三层上下文策略
- ADR 0009 — 每阶段模型路由
- ADR 0007 — 混合语义-关键词检索 + 重排序架构
