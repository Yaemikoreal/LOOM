# OpenNovel Agent 协作指南

> 本文件面向 AI 编程 Agent。阅读前默认你不了解本项目，请按实际目录与代码状态操作，不要依赖外部假设。

## 项目概览

**OpenNovel** 是一个本地优先的长篇小说叙事操作系统，当前版本 **2.0.0**（`pyproject.toml` 与 `opennovel/__init__.py` 已对齐）。

- **核心定位**：CLI 驱动的 AI 自动化长篇小说创作工具。Agent 直接生成内容，人类在所有章节产出后统览审阅与回滚，AI 负责状态追踪、一致性校验、迭代优化。
- **创作语言**：以纯 Markdown 文件为人类创作层（`canon/`、`characters/`、`draft/`），可被 Git 追踪、Obsidian/VS Code 打开。
- **Machine Shadow**：AI 自动提取的结构化状态层，由 YAML Frontmatter + SQLite 事件账本 + 文件级快照组成。
- **四 Agent 自主流水线**：Writer（规划/创作/修订/变异）→ Critic（五维评分）→ Manager（状态提取/事件记录）→ Director（全局叙事分析）。
- **GUI 桌面端**：当前无 GUI。未来可能重新设计，当前 V3（Tauri + FastAPI）已删除。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端核心 | Python 3.10+，Typer，Rich，Pydantic v2，SQLModel |
| LLM 总线 | LiteLLM + tenacity（支持 OpenAI/Anthropic/DeepSeek/Ollama 等） |
| 语义检索 | LlamaIndex + `sentence-transformers`，默认 BGE-M3，可配置 Qwen3-Embedding 等本地模型 |
| 因果图分析 | NetworkX（可选 `pip install -e ".[phase2]"`） |
| 全文索引 | SQLite FTS5（unicode61） |
| 数据存储 | SQLite（事件账本 + 指标库 + FTS5 + 会话） |
| 代码质量 | Ruff，mypy，pre-commit，pytest，pytest-cov |

## 仓库结构

```
OpenNovel/
├── opennovel/              # Python 包主目录
│   ├── agents/             # Agent 人格实现（writer/critic/manager/director/actor/auditor）
│   ├── cli/                # Typer CLI 命令入口
│   ├── core/               # 核心引擎（LLM Bus、上下文组装、AutoRunner、检索、校验等）
│   ├── prompts/            # Agent Prompt 资产（Markdown）
│   ├── schemas/            # Pydantic / SQLModel 模型
│   ├── storage/            # 存储适配器（SQLite、YAML、向量、FTS5、指标库）
│   ├── mcp_server.py       # MCP Server 入口
│   └── __init__.py
├── tests/                  # pytest 测试（41 个测试文件，864+ 测试用例）
├── docs/                   # 文档与 ADR
│   ├── adr/                # 架构决策记录 0001-0011
│   ├── cli-manual.md
│   ├── mcp-integration.md
│   ├── user-guide.md
│   └── ...
├── novels/                 # 默认小说工作区
├── sample/                 # 标准项目模板
├── pyproject.toml          # Python 项目配置
├── .opennovel.yaml         # 全局配置（默认模型、工作区目录）
└── .mcp.json               # MCP 客户端预设配置
```

## 关键配置

### `pyproject.toml`

- 构建后端：`setuptools.build_meta`
- 入口脚本：
  - `novel` → `opennovel.cli.main:app`
  - `novel-mcp` → `opennovel.mcp_server:cli_main`
- 可选依赖组：
  - `dev`：pytest、pytest-cov、ruff、mypy、pre-commit
  - `local-embedding`：sentence-transformers（本地嵌入模型；默认 BGE-M3，可在 `novel.yaml` 中切换为 Qwen3-Embedding 等 Hugging Face 模型）
  - `phase2`：networkx（因果图分析）
- Ruff 目标版本 `py310`，行宽 `100`
- mypy `disallow_untyped_defs = true`（所有函数需类型注解）

### `.opennovel.yaml`

全局配置，从当前目录向上搜索最多 32 层：

```yaml
default_model: deepseek/deepseek-v4-flash
workspace_dir: novels
default_api_base: ""  # 可选
```

### `novel.yaml`（项目级）

每个小说项目的配置，示例见 `sample/novel.yaml`：

```yaml
version: "1.0.1"
model: "deepseek/deepseek-v4-flash"
token_budget: 8000
output_reserve: 2000
target_chapters: 3
words_per_chapter: 2000
outline: "outlines/story.md"
creative_direction: "..."
director_enabled: true

# 本地嵌入/重排序模型（可选，需 pip install -e ".[local-embedding]")
embedding_model: "local:Qwen/Qwen3-Embedding-0.6B"
reranker_model: "Qwen/Qwen3-Reranker-0.6B"

# Per-Agent 模型覆盖（可选）
agents:
  writer:
    think_model: "..."
    write_model: "..."
    revise_model: "..."
  critic:
    model: "..."
```

## 构建与运行命令

### 安装

```bash
# 基础安装 + 开发依赖
pip install -e ".[dev]"

# 本地嵌入
pip install -e ".[local-embedding]"

# 因果图分析
pip install -e ".[phase2]"
```

### CLI 入口

```bash
novel --help                    # 查看所有命令
novel-mcp                       # 启动 MCP Server（stdio）
```

常用命令：

```bash
novel init my-novel             # 在工作区 novels/ 下创建项目
novel init .                    # 在当前目录创建项目
novel write novels/my-novel/draft/ch_001.md
novel auto novels/my-novel      # 四 Agent 自主创作
novel commit novels/my-novel/draft/ch_001.md
novel stash "灵感片段" --tag mood
novel diff novels/my-novel/draft/ch_001.md
novel doctor novels/my-novel
novel doctor novels/my-novel --causal
novel causal novels/my-novel         # SQL 因果链查询
novel report --cost                  # Token / 成本统计
novel snapshot cleanup               # 快照归档清理
novel reindex novels/my-novel
novel list
novel config
```

## 测试命令

```bash
pytest                          # 运行全部测试（864+ 用例）
pytest -v --tb=short           # 详细输出
pytest tests/test_writer.py    # 单文件
pytest -k "test_name"          # 按名称过滤
pytest --cov=opennovel --cov-report=term-missing  # 覆盖率
```

## 代码质量

```bash
ruff check opennovel/ tests/ scripts/        # 静态检查
ruff format --check opennovel/ tests/ scripts/  # 检查格式
ruff format opennovel/ tests/ scripts/       # 自动格式化
mypy opennovel/                     # 类型检查
mypy --strict opennovel/            # 严格类型检查
pre-commit run --all-files          # 本地 pre-commit
```

### 代码风格规范

- **Python 版本**：≥ 3.10
- **行宽**：100 字符（Ruff `line-length = 100`）
- **缩进**：4 空格
- **行尾**：LF（`.editorconfig`）
- **类型注解**：mypy `disallow_untyped_defs = true`，所有函数必须有类型注解
- **Ruff 规则**：`E`, `F`, `W`, `I`, `N`, `UP`, `B`, `A`, `SIM`
  - 忽略 `B008`（Typer 默认参数需要函数调用）
  - `opennovel/cli/*.py` 忽略 `E402`（`sys.stdout.reconfigure` 必须在顶层 import 前执行）
- **命名**：遵循项目术语表，使用 `CANON`、`STATE MEMORY`、`SUBCONSCIOUS`、`Canonical ID` 等规范术语

### 当前代码质量状态（截至最近检查）

- `pytest` 全部 972 个用例可正常收集并通过核心模块测试。
- `ruff check opennovel/ tests/ scripts/` 全部通过。
- `ruff format --check opennovel/ tests/ scripts/` 全部通过。

> 修改代码时请先执行 `ruff format opennovel/ tests/ scripts/` 和 `ruff check opennovel/ tests/ scripts/` 保持提交质量。

## 模块划分与核心约定

### 四层架构

1. **Human Layer**：纯 Markdown（`canon/`、`characters/`、`draft/`）
2. **Machine Shadow**：YAML Frontmatter + SQLite（`.novel.db`）+ 快照（`.snapshots/`）
3. **Metrics Layer**：已合并到 `.novel.db`（原 `.novel.metrics.db` 废弃）
4. **Semantic Layer**：LlamaIndex + 可配置本地 Embedding 模型（默认 BGE-M3，可选 Qwen3-Embedding 等）向量索引（`.index/`）

### 四条铁律

1. **ID 即锚点**：全局强制 Canonical IDs（`char_001`、`loc_london`），禁止用角色名做内部关联。
2. **权威分级**：`CANON` > `STATE MEMORY` > `SUBCONSCIOUS`，灵感不可作为设定执行。
3. **自动化输出 + 事后审阅**：Agent 可直接生成并写入草稿，人类在所有章节产出后统览审阅；`novel commit` 作为状态固化与快照节点，支持 `novel rollback` 保证可逆。
4. **操作可逆**：破坏性写入前必须生成快照，支持 `novel rollback`。

### 核心模块速查

| 文件 | 职责 |
|------|------|
| `opennovel/core/llm.py` | LLMBus：LiteLLM 封装 + 重试 + Token 追踪 |
| `opennovel/core/context_assembler.py` | 统一上下文组装 + Token 熔断 |
| `opennovel/core/auto_runner.py` | 四 Agent 自主编排器 |
| `opennovel/core/state_manager.py` | 快照 / 回滚 / Diff |
| `opennovel/core/search_pipeline.py` | 三通道检索 + RRF + Cross-Encoder 重排序 |
| `opennovel/core/canon_checker.py` | 世界观规则校验（纯 Python，不依赖 LLM） |
| `opennovel/core/canon_auditor.py` | LLM 二次 Canon 审计（只标记，不阻断） |
| `opennovel/core/causal_graph.py` | 因果图构建 + 后台分析 + 缓存 |
| `opennovel/core/chunker.py` | 动态 Chunk 策略（按文档类型切分） |
| `opennovel/core/llm_cache.py` | LLM 输入缓存（按 prompt hash 缓存） |
| `opennovel/core/safety_fence.py` | 递归深度 / Token / 超时 / Canon 校验熔断 |
| `opennovel/core/state_projector.py` | 角色状态投影缓存 |
| `opennovel/core/tool_registry.py` | Agent 自治知识查询分发中心 |
| `opennovel/agents/writer.py` | 规划、创作、修订、变异 |
| `opennovel/agents/critic.py` | 五维评分 + 锚定反馈 |
| `opennovel/agents/manager.py` | 状态提取 + 事件记录 |
| `opennovel/agents/director.py` | 全局叙事分析 + 策略注入 |
| `opennovel/storage/sqlite.py` | EventStore 事件账本 |
| `opennovel/storage/yaml_storage.py` | Frontmatter 原子写入 + safe_merge |

## MCP Server

`novel-mcp` 通过 stdio 暴露 9 个工具：

- `init_project`、`get_status`、`write_chapter`、`auto_create`
- `commit`、`stash`、`diff`、`doctor`、`foreshadow`

配置参考 `.mcp.json`。

## CI / CD

- **CI**：`.github/workflows/ci.yml`
  - 触发：`push`/`pull_request` 到 `main` 或 `dev`，忽略 markdown/docs 变更
  - 矩阵：Python 3.10/3.11/3.12 × Ubuntu/Windows
  - 使用 `uv` 安装依赖
  - 已修复路径：`ruff format --check opennovel/ tests/ scripts/`、`ruff check opennovel/ tests/ scripts/`、`mypy opennovel/`、`pytest --cov=opennovel`。
- **Release**：`.github/workflows/release.yml`
  - 标签 `v*` 触发
  - 构建 wheel + sdist，创建 GitHub Release，可选发布到 PyPI

## 安全与隐私

- **API Key**：通过环境变量传递（`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`），避免硬编码。
- **`.opennovel.yaml`** 中可能包含 `default_api_key`，注意勿提交到公开仓库。
- **快照与回滚**：所有破坏性写入前生成增量快照；Agent 自动化输出后，人类可通过快照与 `novel rollback` 回退到任意历史节点。
- **Canon 校验**：`CanonChecker` 基于规则文件做关键词违反检测，不依赖 LLM，可在无网络时使用。
- **Safety Fence**：约束 Agent 自治的递归深度、Token 预算、超时，防止失控。

## 修改代码时的注意事项

1. **不要直接读写 Markdown 文件**：所有 Frontmatter 操作通过 `YAMLStorage`。
2. **新增 CLI 命令**：在 `opennovel/cli/` 下新建文件，并在 `opennovel/cli/main.py` 注册。
3. **新增 Agent**：在 `opennovel/agents/` 新建文件 + `opennovel/prompts/` 新建 Prompt，更新 `context_assembler.py` 与 `auto_runner.py`。
4. **新增模型/存储依赖**：先在 `pyproject.toml` 声明，再引用。
5. **保持单进程串行流水线**：长篇小说连贯性要求 `novel auto` 严格串行，不可引入多 Writer 并行。
6. **提交前**：运行 `ruff format`、`ruff check`、`mypy opennovel/`、`pytest`。

## 常用文档索引

- `CONTEXT.md` — 项目术语表与命名约定（必读）
- `docs/cli-manual.md` — CLI 完整手册
- `docs/mcp-integration.md` — MCP 集成指南
- `docs/user-guide.md` — 用户手册
- `docs/adr/0008-gui-v3-architecture.md` — V3 桌面端架构（已废弃）
- `docs/adr/0007-hybrid-search-and-reranking-architecture.md` — 混合检索架构
- `docs/adr/0011-configurable-embedding-and-reranker-models.md` — 可配置嵌入与重排序模型
- `docs/roadmap.md` — 项目发展路线图（架构/功能/性能）
