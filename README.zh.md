<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/EN-Readme-555555?style=flat-square" alt="English"></a>
  <a href="#"><img src="https://img.shields.io/badge/中文-文档-8B5CF6?style=flat-square" alt="中文"></a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/OpenNovel-2.0.0-8B5CF6?style=for-the-badge&logo=markdown&logoColor=white&labelColor=1a1a2e">
    <img alt="OpenNovel" src="https://img.shields.io/badge/OpenNovel-2.0.0-8B5CF6?style=for-the-badge&logo=markdown&logoColor=white&labelColor=1a1a2e">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Yaemikoreal/OpenNovel/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/Yaemikoreal/OpenNovel?color=8B5CF6&style=flat-square" alt="MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=ffd343" alt="Python 3.10+">
  </a>
  <a href="https://github.com/Yaemikoreal/OpenNovel/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/Yaemikoreal/OpenNovel/ci.yml?branch=main&style=flat-square&label=CI" alt="CI">
  </a>
  <a href="https://github.com/astral-sh/ruff">
    <img src="https://img.shields.io/badge/code_style-ruff-261230?style=flat-square" alt="Ruff">
  </a>
  <a href="https://github.com/Yaemikoreal/OpenNovel/releases">
    <img src="https://img.shields.io/github/v/release/Yaemikoreal/OpenNovel?style=flat-square&color=8B5CF6" alt="Release">
  </a>
</p>

<p align="center">
  本地优先的长篇小说叙事操作系统。<br>
  作者写 Markdown，系统维护世界观的一致性与连贯性。
</p>

---

## 概述

**OpenNovel** 是一个 CLI 驱动的长篇小说叙事操作系统。它不是"一键生成小说"的工具，而是一个协作式写作环境——作者掌控故事方向，AI 处理状态追踪、一致性校验和迭代优化的机械性工作。

系统围绕三个解耦层级设计：

- **Human Layer（创作层）** — 纯 Markdown 文件（设定、角色、正文）。任意编辑器均可处理，可被 Git 追踪，可在 Obsidian 中打开。
- **Machine Shadow（状态层）** — AI 自动提取的结构化状态：YAML Frontmatter、SQLite 事件账本、文件级快照。
- **Semantic Layer（语义层）** — 基于 LlamaIndex 的向量检索引擎，将历史文本转化为可召回的语义记忆（BGE-M3 可选）。

---

## 核心特性

- **四 Agent 自主创作流水线** — Writer（规划 + 创作 + 修订）、Critic（五维评分 + 锚定反馈）、Manager（状态提取 + 事件记录）、Director（全局叙事分析 + 调度提议 + 伏笔检测）。单命令启动完整流水线。
- **Agent 自治** — Writer 可在创作过程中通过工具调用协议主动查询缺失信息。安全围栏约束递归深度、Token 预算和超时时间。
- **世界观规则校验** — 基于关键词匹配的规则验证引擎，检测生成文本是否违反已建立的设定规则，不依赖 LLM。
- **事件因果图** — 基于 NetworkX 的事件有向无环图，支持路径分析、中心性计算、上下游因果追溯。
- **自动伏笔追踪** — Director 每 3-5 章基于因果链分析自动检测新伏笔、跟踪推进状态、标记收束节点。`novel foreshadow` 支持手动补充。
- **时间线与历史摘要** — 每次 commit 自动生成章节摘要和时间线。时间线从 SQL 零成本转换，摘要复用 Manager 输出。
- **盲目变异** — 关键章节通过正交变异维度（叙事结构、视点、因果、主题弧线）生成多个结构方案。纠错模式针对评分薄弱维度定向变异。
- **阶段级模型路由** — 同一 Agent 在不同阶段使用不同模型：规划阶段用廉价模型，创作阶段用主力模型，修订阶段用旗舰模型。
- **模型无关的 LLM 总线** — LiteLLM 集成支持任意提供商（OpenAI、Anthropic、DeepSeek、Ollama、本地模型）。每个 Agent 可独立配置。
- **三层模型路由** — Agent 级 → 项目级 → 全局默认 → 硬编码默认。无需重复配置。
- **人工审核门控** — AI 提议，人类确认。每次状态变更经 `novel commit` Diff 审阅后方可写入。完整快照回滚支持。
- **MCP Server** — 通过 Model Context Protocol 暴露四个工具，供 Claude Code 等 MCP 客户端调用。

---

## 快速开始

### 安装

```bash
git clone https://github.com/Yaemikoreal/OpenNovel.git
cd OpenNovel
pip install -e ".[dev]"
```

### 配置 API 密钥

通过环境变量设置 LLM 提供商密钥：

```bash
export DEEPSEEK_API_KEY="sk-xxxx"       # DeepSeek
export OPENAI_API_KEY="sk-xxxx"         # OpenAI
export ANTHROPIC_API_KEY="sk-ant-xxxx"  # Anthropic
```

### 初始化项目

```bash
# 在工作区创建项目（novels/<name>/）
novel init my-novel

# 或在当前目录创建
novel init .
```

工作区由项目根目录的 `.opennovel.yaml` 管理。默认模型为 `deepseek/deepseek-v4-flash`。

### 交互式创作

```bash
# 编辑角色、世界观、大纲
# 然后开始 AI 辅助写作：
novel write novels/my-novel/draft/ch_001.md

# 存入灵感片段：
novel stash "深渊不会主动吞噬你——它只是让你自己跳下去。" --tag 金句

# 审阅并固化状态变更：
novel commit novels/my-novel/draft/ch_001.md
```

### 全自动创作（推荐）

准备好人设、设定和大纲后，一条命令即可生成完整小说：

```bash
novel auto novels/my-novel
```

系统将按章节顺序执行四 Agent 流水线。详见[全自动写作](#全自动写作)章节。

---

## 全自动写作

`novel auto` 命令编排四 Agent 流水线，从大纲、角色档案和世界观规则生成完整小说。

### 流水线

```
┌─────────────────────────────────────────────────────────┐
│                      章节循环                            │
├─────────────────────────────────────────────────────────┤
│    Writer.think() → 结构化大纲（场景拆分）               │
│         ↓                                               │
│    知识缺口检测 → ToolRegistry 主动检索                  │
│         ↓                                               │
│    Writer.write() / write_with_autonomy()               │
│    （创作中通过 ##TOOL_CALL## 协议自主查询）              │
│         ↓                                               │
│    Critic.evaluate() → 五维评分（0-100）                 │
│         ↓                                               │
│    若分数 < 80：                                         │
│      Writer.hot_fix()（段落级精确修复）                  │
│      或 Writer.revise()（全章重写）                      │
│      → 重新评分（最多 5 次重试）                         │
│         ↓                                               │
│    Manager.update() → 角色状态变更 + 事件记录            │
│         ↓                                               │
│    快照 → 一致性校验 → 章节写入                          │
│         ↓                                               │
│    Director.analyze() → 下一章策略指导                   │
└─────────────────────────────────────────────────────────┘
```

### 评分维度

Critic 从五个维度对每章进行评分，每项 0-20 分：

| 维度 | 评估内容 |
|:---|:---|
| 文笔质量 | 语句流畅度、词汇丰富度、感官细节 |
| 情节逻辑 | 因果连贯性、节奏把控、伏笔回收 |
| 角色一致性 | 动机可信度、声音一致性、情感弧线 |
| 节奏把控 | 场景长度变化、张力起伏、信息密度 |
| 情感表达 | 潜台词运用、氛围渲染、读者代入感 |

### Agent 自治（创作中工具调用）

启用后，Writer 可在创作过程中检测知识缺口并主动查询：

1. 创作 Prompt 中注入工具调用协议说明。
2. 若 LLM 需要额外信息，输出 `##TOOL_CALL##` 标记。
3. 系统拦截标记，通过 ToolRegistry 执行查询，注入结果，继续生成。
4. SafetyFence 约束递归深度、Token 预算和超时时间。

此机制通过 `novel.yaml` 的 `safety_fence` 配置控制。

### 条件优化

- **高分跳过**：评分 >= 90 的章节跳过 Manager 实时更新，延后批处理。
- **章节类型路由**：高潮章节强制运行 Director。过渡章节跳过 Director。日常章节每 N 章运行一次。
- **调度提议**：Director 可提议插入、跳过或合并章节，从后往前应用。

### 盲目变异

当检测到高潮章节或前章评分低于 80 时，Writer 通过 `think_variations()` 生成多个结构方案：

- **探索型**：随机选择维度 + 差异化 temperature（0.5/0.7/0.9）。
- **纠错型**：针对 Critic 指出的薄弱维度，在变异 Prompt 中注入负向约束。
- **大纲预审**：Critic 对每个变异方案从情节逻辑、角色一致性、节奏设计三维评分后选择最佳。

### 实际输出示例

由自主创作流水线生成的五章时空悖论短篇小说：

| 章节 | 标题 | 评分 | 字数 |
|:---|:---|---:|---:|
| ch_001 | 量子低语 | 85 | 6,064 |
| ch_002 | 涟漪 | 85 | 4,110 |
| ch_003 | 第二次尝试 | 85 | 5,694 |
| ch_004 | 漩涡 | 82 | 4,306 |
| ch_005 | 因果闭环 | 85 | 5,746 |
| **合计** | | **84.4 均分** | **25,920** |

创作耗时约 14 分钟。Token 消耗：210,503。

---

## MCP Server 与 Claude Code 集成

OpenNovel 通过 Model Context Protocol（MCP）暴露完整创作能力，使 Claude Code 等 MCP 客户端能够初始化项目、查看状态、创作章节和运行全自动创作。

### 启动 MCP Server

```bash
novel-mcp
```

Server 使用 stdio 传输协议。

### 注册到 Claude Code

在项目根目录或 `~/.claude/settings.json` 中创建或编辑 `.mcp.json`：

```json
{
  "mcpServers": {
    "opennovel": {
      "command": "novel-mcp",
      "args": [],
      "env": {
        "DEEPSEEK_API_KEY": "sk-xxxx",
        "OPENAI_API_KEY": "sk-xxxx"
      }
    }
  }
}
```

### 可用工具

| 工具 | 功能 | 关键参数 |
|:---|:---|:---|
| `init_project` | 创建标准小说项目结构 | `path`（str）：项目目录 |
| `get_status` | 读取项目配置、角色、章节状态 | `path`（str）：项目目录 |
| `write_chapter` | 单章创作 + 评分 | `path`、`chapter_id`、`chapter_hint`（str） |
| `auto_create` | 多章全自动创作 | `path`、`chapters`（int，可选） |

### 在 Claude Code 中使用

配置完成后，可直接通过自然语言调用：

> 初始化一个科幻小说项目到 ./nova。
> 设定是世代飞船，船员发现冬眠舱正在缓慢失效。
> 创建三个角色：务实的船长、有同情心的医生、一个不该醒着的神秘乘客。
> 用 auto_create 写 5 章。

Claude Code 将依次调用 `init_project` → 直接编辑文件（设定、角色、大纲）→ 以 `chapters=5` 调用 `auto_create`。

也可结合文件编辑进行更精细的控制：

> init_project 之后，我自己来写世界观设定……

---

## CLI 命令参考

```bash
novel --help             # 查看所有命令
novel <command> --help   # 查看具体命令帮助
```

| 命令 | 功能说明 |
|:---|:---|
| `novel init <name>` | 在工作区创建项目。使用 `.` 在当前目录创建。 |
| `novel write <file>` | 交互式 AI 辅助写作循环（Gen1 Actor）。 |
| `novel auto <path>` | 四 Agent 全自动创作流水线（推荐）。 |
| `novel stash <text>` | 存入灵感片段到潜意识池。`--tag` 添加标签。 |
| `novel commit <file>` | 五步审阅流程：快照 → 提取 → Diff → 确认 → 固化。 |
| `novel rollback <snapshot>` | 回滚到指定快照。 |
| `novel diff <file>` | 校验章节正文与 Shadow 状态的一致性。 |
| `novel doctor <path>` | 诊断项目健康度：孤立角色、悬空引用、脏标记。 |
| `novel reindex <path>` | 重建搜索索引（FTS5 + 向量）。 |
| `novel list` | 列出工作区所有项目（模型、章节数、字数）。 |
| `novel config` | 查看或修改全局配置（默认模型、工作区路径）。 |
| `novel foreshadow` | 查看伏笔追踪表。`--add` 手动补充伏笔。 |

---

## 配置

### 项目配置（`novel.yaml`）

每个小说项目拥有独立的 `novel.yaml`：

```yaml
version: "1.0.1"
model: "deepseek/deepseek-v4-flash"
token_budget: 32000
output_reserve: 4000

creative_direction: "硬科幻时空悖论，悲剧美学"
target_chapters: 5
words_per_chapter: 3500
outline: "outlines/story.md"
director_enabled: true

# 搜索配置（ADR 0007 混合语义-关键词检索 + 重排序，可选）
reranker_enabled: true
reranker_model: "BAAI/bge-reranker-v2-m3"
search_top_k: 5

agents:
  writer:
    think_model: "deepseek/deepseek-v4-flash"
    write_model: "deepseek/deepseek-v4-flash"
    revise_model: "deepseek/deepseek-v4-flash"
  critic:
    model: "deepseek/deepseek-v4-flash"
  manager:
    model: "deepseek/deepseek-v4-flash"
  director:
    model: "deepseek/deepseek-v4-flash"
```

### 全局配置（`.opennovel.yaml`）

位于项目根目录，从当前目录向上搜索：

```yaml
# 所有项目的全局默认值
default_model: "deepseek/deepseek-v4-flash"
workspace_dir: "novels"
default_api_base: "https://api.deepseek.com/v1"
```

### 模型解析链

```
Agent 级（agents.writer.model）
    → 项目级（novel.yaml model）
        → 全局级（.opennovel.yaml default_model）
            → 硬编码默认（deepseek/deepseek-v4-flash）
```

---

## 架构

### 设计原则

- **ID 即锚点** — 全局规范 ID（`char_001`、`loc_london`），严禁用角色名做内部关联。
- **权威分级** — `CANON` > `STATE MEMORY` > `SUBCONSCIOUS`，灵感不可作为设定执行。
- **人工审核门控** — AI 提议，人类确认。`novel commit` 强制 Diff 审阅后方可写入。
- **操作可逆** — 破坏性写入前创建文件级快照，`novel rollback` 秒级恢复。
- **独立指标存储** — 运行时遥测（Token 用量、评分历史、Agent 轨迹）存储在独立的 `.novel.metrics.db` 中，与叙事真相（`.novel.db`）物理隔离。

### 项目结构

```
<project>/
├── canon/               # 不可变世界观设定（CANON 层）
├── characters/          # 角色档案（Markdown + YAML Frontmatter）
├── draft/               # 章节正文
├── outlines/            # 故事大纲
├── foreshadowing/       # 自动伏笔追踪
│   └── foreshadowing.md
├── summaries/           # 自动生成章节摘要
│   ├── ch_001.md
│   └── ch_002.md
├── timeline/            # 自动生成事件时间线
│   └── events.md
├── planner_notes.md     # Director 分析记录（追加式）
├── subconscious/        # 灵感碎片池（SUBCONSCIOUS 层）
├── .snapshots/          # 文件级增量快照
├── .index/              # 向量索引持久化
├── .novel.db            # SQLite 事件账本（叙事真相）
├── .novel.metrics.db    # SQLite 指标数据库（运行时遥测）
├── debug/prompts/       # LLM Prompt 日志（可选）
└── novel.yaml           # 项目配置
```

### 模块结构

```
opennovel/
├── cli/                  # Typer CLI 命令入口
├── core/                 # 核心引擎
│   ├── llm.py            # LiteLLM 总线 + tenacity 重试 + Token 追踪
│   ├── auto_runner.py    # 四 Agent 自主编排器
│   ├── context_assembler.py  # 上下文组装 + Token 熔断
│   ├── agent_autonomy.py # 工具调用协议 + 自治循环
│   ├── hybrid_retriever.py  # SQL + 向量双轨检索
│   ├── retriever.py      # 语义检索路由
│   ├── chunker.py        # 递归 Markdown 分块（H1→H2→段落）
│   ├── reranker.py       # Cross-Encoder 重排序（bge-reranker-v2-m3）
│   ├── search_pipeline.py # 三通道搜索 + RRF + Reranker
│   ├── causal_graph.py   # NetworkX 因果 DAG 分析
│   ├── canon_checker.py  # 世界观规则校验
│   ├── safety_fence.py   # 递归/Token/超时/Canon 约束
│   ├── tool_registry.py  # 知识查询分发中心
│   ├── mutation_strategy.py  # 变异维度选择引擎
│   ├── global_config.py  # 全局配置加载器
│   ├── state_manager.py  # 快照/回滚/Diff
│   ├── config.py         # 项目配置管理
│   ├── doctor.py         # 项目健康度诊断
│   └── diff_checker.py   # 正文-Shadow 一致性校验
├── agents/               # 代理人格
│   ├── writer.py         # 规划 + 创作 + 修订 + 变异
│   ├── critic.py         # 五维评分 + 锚定反馈 + 大纲评审
│   ├── manager.py        # 状态提取 + 事件记录
│   ├── director.py       # 全局叙事分析 + 策略指导
│   ├── actor.py          # 交互式写作（Gen1）
│   └── auditor.py        # 状态提取（含自纠偏）
├── storage/              # 存储适配层
│   ├── sqlite.py         # 事件账本（SQLModel）
│   ├── metrics.py        # 指标数据库
│   ├── foreshadowing.py  # 伏笔 Markdown 读写
│   ├── timeline.py       # 时间线生成器（SQL → Markdown）
│   ├── summaries.py      # 章节摘要持久化
│   ├── yaml_storage.py   # YAML Frontmatter 原子读写
│   ├── vector.py         # LlamaIndex 向量索引
│   └── fts5.py           # SQLite FTS5 全文索引
├── schemas/              # Pydantic / SQLModel 数据模型
├── prompts/              # Agent Prompt 资产
└── mcp_server.py         # MCP 协议服务器
```

---

## 开发

```bash
# 环境搭建
git clone https://github.com/Yaemikoreal/OpenNovel.git
cd OpenNovel
pip install -e ".[dev]"

# 可选依赖
pip install -e ".[local-embedding]"  # BGE-M3 本地嵌入
pip install -e ".[phase2]"           # NetworkX 因果图

# 测试
pytest -v --tb=short                              # 运行全部测试
pytest tests/test_auto_runner.py                  # 单文件
pytest -k "test_autonomous"                       # 按名称过滤
pytest --cov=opennovel --cov-report=term-missing   # 覆盖率

# 代码质量
ruff check opennovel/ tests/
ruff format --check opennovel/ tests/
mypy opennovel/
```

### 测试状态

- **850+ 项测试**，覆盖 41 个测试文件
- **88% 代码覆盖率**
- 核心模块覆盖率接近或达到 100%

---

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE)。
