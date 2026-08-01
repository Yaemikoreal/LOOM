<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/EN-Readme-8B5CF6?style=flat-square" alt="English"></a>
  <a href="README.zh.md"><img src="https://img.shields.io/badge/中文-文档-555555?style=flat-square" alt="中文"></a>
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
  Local-first long-form novel operating system.<br>
  Write in Markdown. The system maintains narrative consistency.
</p>

---

## Overview

**OpenNovel** is a CLI-driven narrative operating system for long-form fiction. It is not a "one-click novel generator" — it is a collaborative writing environment where the author controls the story while AI handles the mechanical complexity of state tracking, consistency verification, and iterative refinement.

The system is organized around three decoupled layers:

- **Human Layer** — Pure Markdown files (canon, characters, drafts). Editable in any text editor, trackable by git, openable in Obsidian.
- **Machine Shadow** — Structured state extracted by AI: YAML frontmatter, SQLite event ledger, and file-level snapshots.
- **Semantic Layer** — LlamaIndex-based vector retrieval for contextual memory (BGE-M3 optional).

---

## Core Features

- **Four-Agent Autonomous Pipeline** — Writer (planning + creation + revision), Critic (five-dimension scoring + anchored feedback), Manager (state extraction + event recording), Director (global narrative analysis + scheduling proposals). Full pipeline runs on a single command.
- **Agent Autonomy** — Writer can proactively query missing information mid-creation via tool-calling protocol. SafetyFence constrains recursion depth, token budget, and timeout.
- **Canon Integrity Checking** — Rule-based validation against world-building documents. Detects violations of established setting rules without LLM dependency.
- **Causal Event Graph** — NetworkX-based directed acyclic graph of narrative events. Supports path analysis, centrality computation, upstream/downstream causal tracing.
- **Automatic Foreshadowing Tracking** — Director detects planted setups, tracks their progression, and identifies resolution points automatically every 3-5 chapters via causal chain analysis. `novel foreshadow` for manual override.
- **Auto-Generated Timeline & Summaries** — Chapter summaries written on commit, event timeline converted from EventStore SQL at zero token cost. Both autonomous and interactive flows emit them automatically.
- **Blind Mutation** — Key chapters generate multiple structural directions via orthogonal mutation dimensions (narrative structure, point of view, causality, thematic arc). Corrective mode targets weak dimensions from prior evaluation.
- **Stage Model Routing** — Different LLM models per writing stage: cheap model for planning, flagship model for creation, premium model for revision.
- **Model-Agnostic LLM Bus** — LiteLLM integration supports any provider (OpenAI, Anthropic, DeepSeek, Ollama, local models). Each agent can be independently configured.
- **Three-Layer Model Fallback** — Agent-level model in novel.yaml, project-level model, global default in `.opennovel.yaml`, hardcoded default. No repeated configuration needed.
- **Human-in-the-Loop** — AI proposes, human approves. Every state change goes through `novel commit` with diff review. Full rollback support.
- **MCP Server** — Four tools exposed via Model Context Protocol for Claude Code and other MCP clients.

---

## Quick Start

### Installation

```bash
git clone https://github.com/Yaemikoreal/OpenNovel.git
cd OpenNovel
pip install -e ".[dev]"
```

### Configure API Keys

Set your LLM provider key as an environment variable:

```bash
export DEEPSEEK_API_KEY="sk-xxxx"       # DeepSeek
export OPENAI_API_KEY="sk-xxxx"         # OpenAI
export ANTHROPIC_API_KEY="sk-ant-xxxx"  # Anthropic
```

### Initialize a Project

```bash
# Create project in workspace (novels/<name>/)
novel init my-novel

# Or create at specific path
novel init .
```

The workspace is managed by `.opennovel.yaml` at the project root. Default model is `deepseek/deepseek-v4-flash`.

### Write Your First Chapter (Interactive)

```bash
# Edit characters, world rules, and outline in your editor
# Then start AI-assisted writing:
novel write novels/my-novel/draft/ch_001.md

# Store inspirations:
novel stash "A sentence fragment" --tag mood

# Review and commit state changes:
novel commit novels/my-novel/draft/ch_001.md
```

### Run Autonomous Creation

This is the primary workflow. Prepare your project, then execute:

```bash
novel auto novels/my-novel
```

The system processes all chapters sequentially through the Agent pipeline. See the [Autonomous Writing](#autonomous-writing) section for details.

---

## Autonomous Writing

The `novel auto` command orchestrates a four-agent pipeline that generates an entire novel from your outline, character files, and world rules.

### Pipeline

```
┌─────────────────────────────────────────────────────────┐
│                    Chapter Loop                         │
├─────────────────────────────────────────────────────────┤
│    Writer.think() → structured outline with scenes      │
│         ↓                                               │
│    Knowledge Gap Detection → ToolRegistry query         │
│         ↓                                               │
│    Writer.write() / write_with_autonomy()               │
│    (mid-write tool calls via ##TOOL_CALL## protocol)    │
│         ↓                                               │
│    Critic.evaluate() → five-dimension score (0-100)     │
│         ↓                                               │
│    if score < 80:                                       │
│      Writer.hot_fix() (targeted paragraph repair)       │
│      or Writer.revise() (full chapter rewrite)          │
│      → re-evaluate (max 5 retries)                      │
│         ↓                                               │
│    Manager.update() → character state + events          │
│         ↓                                               │
│    Snapshot → Diff Check → chapter written              │
│         ↓                                               │
│    Director.analyze() → strategy for next chapter       │
└─────────────────────────────────────────────────────────┘
```

### Scoring Dimensions

Critic evaluates each chapter across five dimensions, each scored 0-20:

| Dimension | Focus |
|:---|:---|
| Writing Quality | Sentence fluency, vocabulary, sensory detail |
| Plot Logic | Causal coherence, pacing, payoff setup |
| Character Consistency | Motivation, voice, emotional arc alignment |
| Rhythm Control | Scene length variation, tension modulation |
| Emotional Expression | Subtext, atmospheric resonance, reader impact |

### Agent Autonomy (Mid-Write Tool Calling)

When enabled, Writer can detect knowledge gaps during creation and autonomously query information sources. The protocol is transparent:

1. Writer's prompt includes tool-calling instructions.
2. If the LLM needs additional information, it emits a `##TOOL_CALL##` marker with the query.
3. The system intercepts the marker, executes the query through ToolRegistry, injects results, and continues the generation loop.
4. SafetyFence enforces recursion depth, token budget, and timeout constraints.

This mechanism is controlled by the `safety_fence` configuration in `novel.yaml`.

### Conditionals and Optimizations

- **High-score bypass**: Chapters scoring >= 90 skip Manager real-time update, deferred to batch processing at pipeline end.
- **Chapter type routing**: Climax chapters force Director analysis. Transition chapters skip Director. Routine chapters run Director every N chapters.
- **Scheduling proposals**: Director can propose chapter insertions, skips, or merges, applied from end to start after current chapter completes.

### Blind Mutation

For chapters detected as climax or when prior score is below 80, Writer generates multiple structural variants through `think_variations()`:

- **Exploratory mode**: Random dimension selection with varied temperature (0.5/0.7/0.9).
- **Corrective mode**: Targets weak dimensions identified by Critic, injecting negative constraints into the variant prompt.
- **Outline pre-screening**: Critic evaluates each variant outline on plot logic, character consistency, and pacing before full creation.

### Example Output

A five-chapter time-paradox short story generated by the autonomous pipeline:

| Chapter | Title | Score | Words |
|:---|:---|---:|---:|
| ch_001 | Quantum Whisper | 85 | 6,064 |
| ch_002 | Ripples | 85 | 4,110 |
| ch_003 | Second Attempt | 85 | 5,694 |
| ch_004 | Vortex | 82 | 4,306 |
| ch_005 | Causal Loop | 85 | 5,746 |
| **Total** | | **84.4 avg** | **25,920** |

Creation time: approximately 14 minutes. Token consumption: 210,503.

---

## MCP Server and Claude Code Integration

OpenNovel exposes its full creation pipeline through the Model Context Protocol (MCP), enabling Claude Code and other MCP clients to initialize projects, check status, write chapters, and run autonomous creation.

### Starting the MCP Server

```bash
# The server runs on stdio transport
novel-mcp
```

### Registering with Claude Code

Create or edit `.mcp.json` in your project root or in `~/.claude/settings.json`:

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

### Available Tools

| Tool | Description | Key Parameters |
|:---|:---|:---|
| `init_project` | Create a new novel project with standard structure | `path` (str): project directory |
| `get_status` | Read project state: config, characters, chapters | `path` (str): project directory |
| `write_chapter` | Single chapter creation with evaluation | `path`, `chapter_id`, `chapter_hint` (str) |
| `auto_create` | Full multi-chapter autonomous creation | `path`, `chapters` (int, optional) |

### Usage in Claude Code

Once configured, you can invoke OpenNovel through natural language in Claude Code:

```
Initialize a science fiction novel project at ./nova.

The setting is a generation ship where the crew discovers
the cryo-pods are slowly failing. Create 3 characters:
a pragmatic captain, a compassionate doctor, and a
mysterious passenger who shouldn't be awake.

Use auto_create to write 5 chapters.
```

Claude Code will call the MCP tools in sequence: `init_project` → edit files directly (canon, characters, outline) → `auto_create` with `chapters=5`.

You can also combine with direct file editing for finer control:

```
After init_project, I'll write the world rules myself...
```

---

## CLI Command Reference

```bash
novel --help        # View all commands
novel <command> --help  # Command-specific help
```

| Command | Description |
|:---|:---|
| `novel init <name>` | Initialize project in workspace (`novels/<name>/`). Use `.` for current directory. |
| `novel write <file>` | Interactive AI-assisted writing loop (Gen1 Actor). |
| `novel auto <path>` | Four-agent autonomous creation pipeline (recommended). |
| `novel stash <text>` | Store inspiration fragment into subconscious pool. `--tag` for labels. |
| `novel commit <file>` | Five-step review: snapshot → state extraction → diff → confirmation → persist. |
| `novel rollback <snapshot>` | Restore project to a previous snapshot. |
| `novel diff <file>` | Validate consistency between chapter text and shadow state. |
| `novel doctor <path>` | Diagnose project health: orphan characters, dangling references, dirty flags. |
| `novel reindex <path>` | Rebuild search indexes (FTS5 + vector). |
| `novel list` | List all projects in workspace with model, chapter count, word count. |
| `novel config` | View or modify global configuration (default model, workspace directory). |
| `novel foreshadow` | View or manage foreshadowing tracking table. `--add` for manual entries.

---

## Configuration

### Project Configuration (`novel.yaml`)

Each novel project has its own `novel.yaml`:

```yaml
version: "1.0.1"
model: "deepseek/deepseek-v4-flash"
token_budget: 32000
output_reserve: 4000

creative_direction: "Hard sci-fi, time paradox, tragic aesthetics"
target_chapters: 5
words_per_chapter: 3500
outline: "outlines/story.md"
director_enabled: true

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

### Global Configuration (`.opennovel.yaml`)

Located at the project root, searched upwards from current directory:

```yaml
# Global defaults for all projects
default_model: "deepseek/deepseek-v4-flash"
workspace_dir: "novels"
default_api_base: "https://api.deepseek.com/v1"
```

### Model Resolution Chain

```
Agent-level (agents.writer.model)
    → Project-level (novel.yaml model)
        → Global-level (.opennovel.yaml default_model)
            → Hardcoded default (deepseek/deepseek-v4-flash)
```

---

## Architecture

### Design Principles

- **ID as Anchor** — Global canonical IDs (`char_001`, `loc_london`). Never use character names for internal references.
- **Authority Hierarchy** — `CANON` > `STATE MEMORY` > `SUBCONSCIOUS`. Inspiration must never be executed as canon.
- **Human Review Gate** — AI proposes, human approves. `novel commit` enforces diff review before persistence.
- **Reversible Operations** — Every destructive write creates a file-level snapshot before modification. `novel rollback` for instant recovery.
- **Independent Metrics Storage** — Runtime telemetry (token usage, evaluation history, agent traces) stored in `.novel.metrics.db`, physically separate from narrative truth (`.novel.db`).

### Project Structure

```
<project>/
├── canon/               # Immutable world-building (CANON layer)
│   └── world_rules.md
├── characters/          # Character files with YAML frontmatter
│   ├── char_001.md
│   └── char_002.md
├── draft/               # Chapter drafts
│   ├── ch_001.md
│   ├── ch_002.md
│   └── ...
├── outlines/            # Story outline (##-separated chapters)
│   └── story.md
├── foreshadowing/       # Auto-detected foreshadowing tracking
│   └── foreshadowing.md
├── summaries/           # Auto-generated chapter summaries
│   ├── ch_001.md
│   └── ch_002.md
├── timeline/            # Auto-generated event timeline from SQL
│   └── events.md
├── planner_notes.md     # Director analysis record (appended)
├── subconscious/        # Inspiration fragments (SUBCONSCIOUS layer)
├── .snapshots/          # File-level incremental snapshots
├── .index/              # Vector index persistence
├── .novel.db            # SQLite event ledger (narrative truth)
├── .novel.metrics.db    # SQLite metrics database (runtime telemetry)
├── debug/prompts/       # Optional LLM prompt logs
└── novel.yaml           # Project configuration
```

### Module Map

```
opennovel/
├── cli/                  # Typer CLI commands
│   ├── main.py           # Root commands (init/rollback/diff/doctor/list/config)
│   ├── write.py          # Interactive writing (Gen1)
│   ├── auto.py           # Autonomous pipeline (Gen2)
│   ├── commit.py         # Five-step review workflow
│   ├── stash.py          # Inspiration management
│   └── reindex.py        # Search index rebuild (FTS5 + vector)
│   ├── llm.py            # LiteLLM bus + tenacity retry + token tracking
│   ├── auto_runner.py    # Autonomous four-agent orchestrator
│   ├── context_assembler.py  # Context assembly + token budgeting
│   ├── agent_autonomy.py # Tool-calling protocol + autonomous loop
│   ├── hybrid_retriever.py  # SQL + vector dual-track retrieval
│   ├── retriever.py      # Semantic retrieval routing
│   ├── chunker.py        # Recursive Markdown chunker (H1→H2→para)
│   ├── reranker.py       # Cross-Encoder re-ranker (bge-reranker-v2-m3)
│   ├── search_pipeline.py # Three-channel search + RRF + reranker
│   ├── causal_graph.py   # NetworkX causal DAG analysis
│   ├── canon_checker.py  # World-building rule validation
│   ├── safety_fence.py   # Recursion/token/timeout/canon constraints
│   ├── tool_registry.py  # Knowledge query dispatch center
│   ├── mutation_strategy.py  # Mutation dimension selection
│   ├── global_config.py  # .opennovel.yaml loader
│   ├── state_manager.py  # Snapshot + rollback + diff
│   ├── config.py         # novel.yaml management
│   ├── doctor.py         # Project health diagnosis
│   └── diff_checker.py   # Text-shadow consistency check
├── agents/               # Agent personalities
│   ├── writer.py         # Planning + creation + revision + mutation
│   ├── critic.py         # Five-dimension scoring + anchored feedback
│   ├── manager.py        # State extraction + event recording
│   ├── director.py       # Global narrative analysis + strategy
│   ├── actor.py          # Interactive writing (Gen1)
│   └── auditor.py        # State extraction with self-correction
├── storage/              # Storage adapters
│   ├── sqlite.py         # Event store (SQLModel)
│   ├── metrics.py        # Metrics store
│   ├── foreshadowing.py  # Foreshadowing Markdown read/write
│   ├── timeline.py       # Timeline generator (SQL to Markdown)
│   ├── summaries.py      # Chapter summary persistence
│   ├── yaml_storage.py   # YAML frontmatter atomic read/write
│   ├── vector.py         # LlamaIndex vector index
│   └── fts5.py           # SQLite FTS5 full-text search index
├── schemas/              # Pydantic / SQLModel models
├── prompts/              # Agent prompt assets
└── mcp_server.py         # MCP protocol server
```

---

## Development

```bash
# Setup
git clone https://github.com/Yaemikoreal/OpenNovel.git
cd OpenNovel
pip install -e ".[dev]"

# Optional dependencies
pip install -e ".[local-embedding]"  # BGE-M3 local embeddings
pip install -e ".[phase2]"           # NetworkX for causal graph

# Testing
pytest -v --tb=short                      # Run all tests
pytest tests/test_auto_runner.py          # Single file
pytest -k "test_autonomous"               # Filter by name
pytest --cov=opennovel --cov-report=term-missing  # Coverage

# Code quality
ruff check opennovel/ tests/
ruff format --check opennovel/ tests/
mypy opennovel/

# Type checking (strict)
mypy --strict opennovel/
```

### Test Status

- **850+ tests** across 41 test files
- **88% code coverage**
- Modules at or near 100% coverage: parser, state_manager, diff_checker, doctor, schemas, yaml_storage, metrics, foreshadowing

---

## License

MIT License. See [LICENSE](LICENSE) for details.
