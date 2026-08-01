# OpenNovel 优化实施路线图

> 生成于 2026-06-26，基于 `docs/opennovel优化方向指导.md` 的深入技术探讨。
> 讨论过程详见项目对话记录（grill-with-docs 技能会话）。

## 概述

本文档记录了 **6 个优化方向** 从"模糊想法"到"工程可执行方案"的收敛结果，以及**8 个子任务**按依赖关系排列的实施路线图。

### 优化策略原则

- **设计冻结优先**：所有方案必须尊重现有架构约束，不做 ORM Schema 迁移、不做模块重构
- **帕累托改进**：每一改动对部分用户产生净收益时，不损害其他用户
- **先诊断后治疗**：在修改系统行为之前，先用现有数据分析是否需要修改

---

## 依赖关系图

6 个优化方向中只有 `write_model_climax` 需要等待 `chapter_utils.py` 拆分，其余全部可独立并行实施。

```
Phase 0          Phase 1              Phase 2
────────         ──────────           ──────────
detect_strategy  write_model_climax   Glass-Box Decision
│                ↑                    │
│                └── 依赖 ─────────────┤
│                chapter_utils        │
│                                     │
└── State Projector  ──────────────── Cannon Exemption
│                                     │
└── Critic Calibration ──────────────┘
（每个 Phase 内项目可并行）
```

---

## Phase 0：基础设施（1-2 天）

最简单的改动，零风险，优先执行。

### 0.1 抽取 `core/chapter_utils.py`

**目标**：将 `detect_chapter_type()` 和 `ChapterType` 枚举从 `auto_runner.py` 抽取到独立工具模块，消除未来 Writer 对 AutoRunner 的反向依赖。

**改动量**：~15 行（新建 + import 修改）

**涉及文件**：

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| 新建 | `opennovel/core/chapter_utils.py` | 函数 + 枚举定义 |
| 修改 | `opennovel/core/auto_runner.py` | 改 import，删除原定义 |
| 后续 | `opennovel/agents/writer.py` | Phase 1.1 时引入 |

**实现**：

```python
# opennovel/core/chapter_utils.py （新建）
from enum import Enum

class ChapterType(str, Enum):
    CLIMAX = "climax"        # 高潮/转折/决战
    TRANSITION = "transition"  # 过渡/日常/平静
    ROUTINE = "routine"      # 普通推进

def detect_chapter_type(chapter_hint: str) -> ChapterType:
    """根据大纲提示检测章节类型。

    Args:
        chapter_hint: 大纲中本章的描述文本

    Returns:
        章节类型枚举
    """
    hint_lower = chapter_hint.lower()

    climax_keywords = ["转折", "高潮", "climax", "决战", "大结局", "finale", "对决"]
    if any(kw in hint_lower for kw in climax_keywords):
        return ChapterType.CLIMAX

    transition_keywords = ["过渡", "日常", "平静", "transition", "calm", "平常"]
    if any(kw in hint_lower for kw in transition_keywords):
        return ChapterType.TRANSITION

    return ChapterType.ROUTINE
```

**验收标准**：
- `auto_runner.py` 中不再定义 `ChapterType` 和 `detect_chapter_type()`
- `auto_runner.py` 的 import 改为 `from opennovel.core.chapter_utils import ChapterType, detect_chapter_type`
- 现有测试全部通过

---

### 0.2 激活 `detect_strategy()`

**目标**：让 `detect_strategy()` 函数真正被 Agent 构造时调用，替换当前 Writer/Critic/Director 中硬编码的 `ContextStrategy.STANDARD`。

**背景**：`detect_strategy()` 函数已在 `context_assembler.py` 第 44-58 行实现，可根据模型窗口大小自动选择策略（FRUGAL <32K / STANDARD 32K-128K / PANORAMIC >128K），但从未在任何 Agent 中调用 —— 全部写死 STANDARD。

**改动量**：~6 行

**涉及文件**：

| 文件 | 行号 | 改动 |
|:---|:---|:---|
| `opennovel/agents/writer.py` | 145 | `strategy=ContextStrategy.STANDARD` → `strategy=self.strategy` |
| `opennovel/agents/critic.py` | 117 | 同上 |
| `opennovel/agents/director.py` | 314 | 同上 |

**实现**：

```python
# 在 Writer.__init__() 中新增
from opennovel.core.context_assembler import detect_strategy

# 根据模型窗口自动选择上下文策略
# 假设 llm_bus 的 model 字段可用于推断窗口大小
self.strategy = detect_strategy(self._get_model_window())

# 然后在 _build_context 中替换硬编码
assemble_context(
    ...,
    strategy=self.strategy,  # 原为 ContextStrategy.STANDARD
)
```

**注意**：`detect_strategy()` 需要模型上下文窗口大小作为参数。当前 `llm_bus` 持有 model 名称但不直接暴露窗口大小。实现时需要：

1. 在 `LLMBus` 中新增 `model_window` 属性或配置项
2. 或在 `ProjectConfig` 中增加模型的 context window 映射表
3. 最简单的做法：从 `novel.yaml` 读取显式配置的窗口大小，未配置则使用默认值

**收益分析**：

| 模型窗口 | 改造前 | 改造后 | 效果 |
|:---|:---:|:---:|:---|
| <32K | STANDARD(48K) — 溢出 | FRUGAL(8K) | 从"不可用"变为"可用" |
| 32K-128K | STANDARD(48K) | STANDARD(48K) | 不变 |
| >128K | STANDARD(48K) | PANORAMIC(128K) | 上下文容量提升 ~2.6 倍 |

**验收标准**：
- Writer 使用 PANORAMIC 策略时，上下文包含历史正文倒序灌注
- Writer 使用 FRUGAL 策略时，Token 消耗不超过 8K
- 不修改任何 Token 预算常量

---

## Phase 1：创作质量提升（3-5 天）

需要在 Phase 0.1 完成之后实施（依赖 `chapter_utils.py`）。

### 1.1 write_model_climax

**目标**：为 Writer 的高潮章节分配更强的创作模型，实现基于章节类型的模型路由扩展。

**改动量**：~20 行

**涉及文件**：

| 文件 | 改动 |
|:---|:---|
| `opennovel/agents/writer.py` | `write()` 方法增加 `chapter_hint` 参数；新增模型选择逻辑 |
| `opennovel/core/auto_runner.py` | `write()` 调用处传入 `chapter_hint` |
| `opennovel/mcp_server.py` | `write()` 调用处传参（兼容旧签名） |
| `novel.yaml`（用户项目） | 可选新增 `agents.writer.write_model_climax` |

**实现**：

```python
# Writer.__init__() 中读取新配置
self.write_model_climax = config.get("write_model_climax") or None

# Writer.write() 方法修改
def write(
    self,
    chapter_id: str,
    outline: ChapterOutline,
    previous_chapter_text: str = "",
    additional_knowledge: str = "",
    chapter_hint: str = "",            # ← 新增
) -> str:
    # 章节类型感知的模型选择
    model = self.write_model
    if chapter_hint and self.write_model_climax:
        from opennovel.core.chapter_utils import detect_chapter_type
        if detect_chapter_type(chapter_hint) == ChapterType.CLIMAX:
            model = self.write_model_climax
            logger.info("高潮章节 %s 使用增强模型: %s", chapter_id, model)

    # 后续调用 llm_bus.chat() 时使用 model 而非 self.write_model
    response = self.llm_bus.chat(
        messages,
        temperature=0.8,
        max_tokens=4000,
        model=model,  # ← 动态选择
    )
```

**novel.yaml 配置示例**：

```yaml
agents:
  writer:
    think_model: "deepseek/deepseek-chat"        # 思考阶段 — 廉价
    write_model: "deepseek/deepseek-chat"         # 常规创作
    write_model_climax: "deepseek/deepseek-r1"    # 高潮章节 — 更强模型（可选）
    revise_model: "deepseek/deepseek-chat"        # 修订阶段
```

**验收标准**：
- 普通章节使用 `write_model` 创作
- 高潮章节（含"转折/高潮/climax/决战"关键词）使用 `write_model_climax`
- 未配置 `write_model_climax` 时行为不变
- `Writer.write()` 签名兼容旧的调用方式（`chapter_hint` 默认为空）

---

### 1.2 State Projector（状态投影器）

**目标**：从 EventLog 事件流归约为角色在任意时间点的可信状态快照，作为 ContextAssembler 的运行时数据源注入创作上下文。

**改动量**：~80 行（最大模块）

**设计架构**：

```
AutoRunner.run_chapter(ch_050)
    │
    ├── ContextAssembler.assemble()
    │       │
    │       ├── CANON: 世界观设定
    │       ├── STATE MEMORY:
    │       │       ├── 角色 Frontmatter 状态（现有）
    │       │       └── State Projector 快照（新增）
    │       │               └── EventStore.get_events_up_to(char, ch_049)
    │       │               └── project_character_state(events) → CharacterStateSnapshot
    │       ├── SUBCONSCIOUS: 灵感碎片
    │       └── TASK: 创作指令
    │
    └── Writer.write() 收到的上下文中包含:
        [State Snapshot]
        char_001 (John): Left Arm (Broken), Mood (Depressed), Inventory (Sword)
        char_002 (Mary): Location (London), Mood (Anxious), Knowledge (Secret: True)
```

**涉及文件**：

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| 修改 | `opennovel/storage/sqlite.py` | 新增 `get_events_up_to()` 查询方法 |
| 新建 | `opennovel/core/state_projector.py` | 投影器核心逻辑 |
| 新建（或扩展） | `opennovel/schemas/state.py` | `CharacterStateSnapshot` 模型 |
| 修改 | `opennovel/core/context_assembler.py` | 集成 State Projector 为数据源 |

**实现**：

```python
# === storage/sqlite.py：新增查询 ===

def get_events_up_to(self, character_id: str, chapter_id: str) -> list[EventLog]:
    """查询指定角色截至某章节的所有事件（时间序）。

    Args:
        character_id: 角色 Canonical ID
        chapter_id: 截止章节 ID（含该章节）

    Returns:
        事件列表，按 chapter_id 升序排列
    """
    with Session(self._engine) as session:
        statement = (
            select(EventLog)
            .where(
                EventLog.character_id == character_id,
                EventLog.chapter_id <= chapter_id,
            )
            .order_by(EventLog.chapter_id.asc())
        )
        return list(session.exec(statement).all())
```

```python
# === schemas/state.py：状态快照模型 ===

from pydantic import BaseModel, Field

class CharacterStateSnapshot(BaseModel):
    """角色在某一时间点的完整状态快照。"""

    character_id: str
    physical: dict[str, str] = Field(default_factory=dict)
    """身体状态映射，如 {"左臂": "骨折", "右腿": "健康"}"""

    emotional: dict[str, float] = Field(default_factory=dict)
    """情绪向量，如 {"grief": 0.8, "anger": 0.3}"""

    inventory: list[str] = Field(default_factory=list)
    """当前持有的物品列表"""

    knowledge: list[str] = Field(default_factory=list)
    """当前已知的关键信息列表"""

    location: str | None = None
    """当前所在位置（如有 LOCATION_CHANGE 事件）"""

    relationships: dict[str, str] = Field(default_factory=dict)
    """关系状态，如 {"char_002": "allies", "char_003": "enemies"}"""

    chapter_id: str = ""
    """快照对应的章节位置"""
```

```python
# === core/state_projector.py：投影器 ===

from opennovel.storage.sqlite import EventStore
from opennovel.schemas.state import CharacterStateSnapshot

class StateProjector:
    """状态投影器：将事件流折叠为角色状态快照。"""

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store

    def project(
        self,
        character_id: str,
        up_to_chapter: str,
    ) -> CharacterStateSnapshot:
        """折叠事件流，生成角色状态快照。

        Args:
            character_id: 角色 ID
            up_to_chapter: 截至章节

        Returns:
            该时间点的状态快照
        """
        events = self._event_store.get_events_up_to(character_id, up_to_chapter)
        state = CharacterStateSnapshot(character_id=character_id)

        for evt in events:
            self._apply_event(state, evt)

        state.chapter_id = up_to_chapter
        return state

    def _apply_event(self, state: CharacterStateSnapshot, event) -> None:
        """将单个事件折叠进状态快照。"""
        if event.event_type == "INJURY":
            state.physical[event.description] = "injured"
        elif event.event_type == "HEAL":
            state.physical.pop(event.description, None)
        elif event.event_type == "ITEM_GAIN":
            if event.description not in state.inventory:
                state.inventory.append(event.description)
        elif event.event_type == "ITEM_LOSS":
            if event.description in state.inventory:
                state.inventory.remove(event.description)
        elif event.event_type == "EMOTION_SHIFT":
            state.emotional[event.description] = event.causal_pressure
        elif event.event_type == "LOCATION_CHANGE":
            state.location = event.description
        elif event.event_type == "RELATIONSHIP_CHANGE":
            state.relationships[event.description] = "changed"
```

```python
# === context_assembler.py 集成 ===

def _build_state_memory_block(projector, active_characters, chapter_id) -> str:
    """构建状态快照文本块。"""
    lines = ["## Current Character States"]
    for cid in active_characters:
        snapshot = projector.project(cid, chapter_id)
        text = _format_snapshot(snapshot)
        if text:
            lines.append(text)
    return "\n".join(lines)

def _format_snapshot(snapshot: CharacterStateSnapshot) -> str:
    """将状态快照格式化为 Prompt 可读的文本。"""
    parts = [f"[{snapshot.character_id}]"]
    if snapshot.physical:
        parts.append(f"Body: {', '.join(f'{k}({v})' for k, v in snapshot.physical.items())}")
    if snapshot.emotional:
        parts.append(f"Mood: {', '.join(f'{k}={v}' for k, v in snapshot.emotional.items())}")
    if snapshot.inventory:
        parts.append(f"Items: {', '.join(snapshot.inventory)}")
    if snapshot.location:
        parts.append(f"Location: {snapshot.location}")
    return " | ".join(parts)
```

**性能预期**：全量投影（假设 2500 事件）< 30ms，不构成创作循环瓶颈。

**验收标准**：
- 对已知测试项目（`novels/demo_novel`），投影器能正确输出角色状态
- INJURY + HEAL 序列产生正确的状态翻转（骨折 → 康复）
- EMPTY 事件列表返回空白状态
- 集成到 ContextAssembler 后，Writer Prompt 中包含 `[State Snapshot]` 区块

---

## Phase 2：观测性与审计（3-4 天）

无依赖，可与 Phase 1 并行实施。

### 2.1 Glass-Box Decision（玻璃盒决策）

**目标**：在 Agent 的高价值决策环节（Think/Evaluate）捕获结构化推理链，以 `reasoning` Schema 字段 + `_log_prompt` 拦截 + `contextvars` trace_id 实现零侵入式的决策透明化。

**核心原则**：
- **只捕获 Think 和 Evaluate 阶段**，Write 阶段保持黑盒（心流保护）
- **零侵入**：不修改 `llm_bus.chat()` 返回值，不改 Agent 函数签名
- **文件分离**：推理链存 `logs/reasoning/` 目录，与 `debug/prompts/` 并列

**数据流**：

```
AutoRunner.run_chapter()
  │ 设置 contextvars trace_id = "trace_ch_005"
  │
  ├──→ Writer.think() → llm_bus.chat() → response
  │                        │
  │                        └── _log_prompt 扩展：
  │                             1. 解析 response 是否为 JSON
  │                             2. 提取 {reasoning} 字段
  │                             3. 异步写入 logs/reasoning/{trace_id}_think.json
  │                             4. 原始 prompt 日志写入 debug/prompts/（不变）
  │
  ├──→ Critic.evaluate() → llm_bus.chat() → response
  │                        │
  │                        └── 同上，提取 {critique_reasoning}
  │
  └──→ Writer.write() → llm_bus.chat() → response
                           │
                           └── 不捕获 reasoning（纯文本输出，无 JSON Schema）
```

**涉及文件**：

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| 修改 | `opennovel/schemas/outline.py` | `ChapterOutline` 增加 `reasoning` 字段 |
| 修改 | `opennovel/schemas/evaluation.py` | `ChapterEvaluation` 增加 `critique_reasoning` 可选字段 |
| 修改 | `opennovel/core/llm.py` | `_log_prompt()` 扩展推理链提取与文件存储 |
| 修改 | `opennovel/core/auto_runner.py` | `run_chapter()` 入口设置 `contextvars` trace_id |
| 新建 | `logs/reasoning/`（运行时创建） | 推理链存储目录 |

**实现**：

```python
# === schemas/outline.py 修改 ===

class ChapterOutline(BaseModel):
    # ... 现有字段不变 ...
    reasoning: str = ""
    """生成本大纲的构思过程与依据。仅在 Glass-Box 模式下捕获。"""


# === schemas/evaluation.py 修改 ===

class ChapterEvaluation(BaseModel):
    # ... 现有字段不变 ...
    critique_reasoning: str = ""
    """评分过程的推理依据。可选字段。"""
```

```python
# === core/llm.py _log_prompt 扩展 ===

import os
import json
from contextvars import ContextVar

# 上下文变量，由 AutoRunner.run_chapter() 在入口设置
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

class LLMBus:
    # ... 现有代码 ...

    def _extract_and_save_reasoning(self, response_text: str, agent_name: str) -> None:
        """从 LLM 响应中提取 reasoning 并写入独立文件。"""
        trace_id = trace_id_var.get()
        if not trace_id:
            return

        reasoning = None
        try:
            data = json.loads(response_text)
            reasoning = data.get("reasoning") or data.get("critique_reasoning")
        except (json.JSONDecodeError, TypeError):
            return  # 非 JSON 响应（如 Write 阶段），跳过

        if not reasoning:
            return

        # logs/reasoning/{trace_id}_{agent}.json
        reasoning_dir = Path(self.prompt_log_dir.parent / "reasoning")
        reasoning_dir.mkdir(parents=True, exist_ok=True)
        path = reasoning_dir / f"{trace_id}_{agent_name}.json"
        path.write_text(
            json.dumps({
                "trace_id": trace_id,
                "agent": agent_name,
                "timestamp": datetime.now().isoformat(),
                "reasoning": reasoning,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _log_prompt(self, messages, model, response_text=""):
        # ... 现有全量日志不变 ...
        if response_text:
            self._extract_and_save_reasoning(response_text, self.agent_name)
```

```python
# === core/auto_runner.py contextvars 设置 ===

import uuid
from opennovel.core.llm import trace_id_var

class AutoRunner:
    def run_chapter(self, chapter_id, ...):
        # 在入口生成 trace_id 并设置到上下文变量
        token = trace_id_var.set(f"trace_{chapter_id}")
        try:
            # ... 现有 run_chapter 实现不变 ...
            result = self._run_chapter_inner(chapter_id, ...)
            return result
        finally:
            trace_id_var.reset(token)
```

**验收标准**：
- Writer.think() 输出含 `reasoning` 字段时，该字段出现在 `logs/reasoning/` 文件中
- Critic.evaluate() 输出含 `critique_reasoning` 时同理
- Writer.write() 的纯文本输出不触发 reasoning 捕获
- `trace_id` 在多个 Agent 调用链中保持一致
- 未启用 trace_id 时，不受影响

---

### 2.2 Canon Exemption（规则豁免）

**目标**：允许作者在特定场景下临时跳过世界观规则检查，支持行内注释（段落级）和 Frontmatter 声明（章节级）两个层级的豁免。

**改动量**：~40 行

**涉及文件**：

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| 修改 | `opennovel/core/canon_checker.py` | `check_text()` 新增豁免检测步骤 |
| 修改 | 章节 Frontmatter 处理 | Frontmatter 识别 `canon_exemptions` 字段 |

**实现**：

```python
# === canon_checker.py 修改 ===

class CanonChecker:
    # ... 现有代码不变 ...

    def check_text(self, text: str, rules: list[CanonRule]) -> list[CanonViolation]:
        """检查文本是否违反世界观规则（支持豁免标记）。"""
        if not text.strip() or not rules:
            return []

        sentences = re.split(r"[。！？\n]+", text)
        violations = []

        for rule in rules:
            # 先检查行内豁免（A1）
            if self._has_inline_exemption(text, rule.concept):
                continue

            # 现有检测逻辑
            result = self._check_rule_against_text(text, sentences, rule)
            if result:
                violations.append(result)

        return violations

    def _has_inline_exemption(self, text: str, rule_concept: str) -> bool:
        """检测是否存在行内豁免标记。

        支持格式: <!-- canon_exempt: rule_concept -->
        """
        pattern = re.escape(rule_concept)
        return bool(re.search(
            rf"<!-- canon_exempt:\s*{pattern}\s*-->",
            text,
        ))

    # A2 章节级豁免在调用方检查（check_text 不感知章节边界）
    def filter_by_chapter_exemptions(
        self,
        violations: list[CanonViolation],
        exemptions: list[str],
    ) -> list[CanonViolation]:
        """根据章节 Frontmatter 声明的豁免列表过滤违规。"""
        if not exemptions:
            return violations
        return [
            v for v in violations
            if v.rule not in exemptions
        ]
```

**使用示例**：

```markdown
# 行内豁免（A1）：段落级
宝剑发出耀眼的光芒。 <!-- canon_exempt: magic_no_glow -->

# 章节级豁免（A2）：在章节 Frontmatter 声明
---
canon_exemptions:
  - rule: "physics_gravity"
    reason: "梦境序列，物理规则失效"
---
```

**验收标准**：
- 含 `<!-- canon_exempt: rule_name -->` 的文本不触发该规则的 violation
- Frontmatter `canon_exemptions` 列表中声明的规则在当前章节被忽略
- 豁免机制不影响正常违规检测
- 多规则同时豁免正常工作

---

## Phase 3：诊断工具（1-2 天）

纯粹新增 CLI 命令，不修改核心流程。

### 3.1 Critic 校准诊断

**目标**：利用已有的 `EvaluationHistory` 数据，提供 Critic 评分的统计分析报告，帮助判断是否存在系统性偏差。

**改动量**：~50 行

**涉及文件**：

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| 新建 | `opennovel/core/evaluation_auditor.py` | 统计分析与报告生成 |
| 修改 | `opennovel/cli/main.py` | 注册 `doctor --calibration` 子命令 |

**实现**：

```python
# === core/evaluation_auditor.py ===

import statistics
from opennovel.storage.metrics import MetricsStore

class EvaluationAuditor:
    """评分审计器：分析 Critic 评分的一致性和偏差。"""

    def __init__(self, metrics_store: MetricsStore) -> None:
        self._metrics = metrics_store

    def analyze(self) -> dict:
        """对所有历史评分进行统计分析。"""
        history = self._metrics.get_evaluation_history()
        if not history:
            return {"error": "无评分数据"}

        dims = {
            "writing": [],
            "plot": [],
            "character": [],
            "rhythm": [],
            "emotion": [],
        }

        for record in history:
            dims["writing"].append(record.dimension_writing)
            dims["plot"].append(record.dimension_plot)
            dims["character"].append(record.dimension_character)
            dims["rhythm"].append(record.dimension_rhythm)
            dims["emotion"].append(record.dimension_emotion)

        report = {
            "total_evaluations": len(history),
            "dimensions": {},
            "alerts": [],
        }

        for name, scores in dims.items():
            stats = {
                "mean": round(statistics.mean(scores), 1),
                "std": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0,
                "min": min(scores),
                "max": max(scores),
                "range": max(scores) - min(scores),
            }
            report["dimensions"][name] = stats

            # 检测异常
            if stats["std"] > 2.0:
                report["alerts"].append(
                    f"{name} 标准差 {stats['std']} 偏高（>2.0），"
                    "可能存在评分漂移"
                )

        return report
```

**CLI 输出**：

```
$ novel doctor --calibration
Critic 评分校准报告 (共 12 章)
──────────────────────────────────
文笔质量: 均值 16.2/20  σ=1.1
情节逻辑: 均值 14.8/20  σ=2.3   ← 波动较大
角色一致: 均值 17.1/20  σ=0.9
节奏把控: 均值 13.5/20  σ=1.8
情感表达: 均值 15.9/20  σ=1.5

⚠ 注意：情节逻辑 σ=2.3 显著高于其他维度，
  建议检查是否因章节类型不同导致评分标准漂移。
```

**验收标准**：
- 能从 MetricsStore 读取 EvaluationHistory
- 无数据时给出友好提示
- 各维度标准差大于阈值时产生告警

---

### 3.2 Canon 解释子命令

**目标**：当 CanonChecker 检测到违规时，提供一个可选的 LLM 辅助解释模式，为违规提供合理化建议或修改方案。

**改动量**：~30 行

**涉及文件**：

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| 修改 | `opennovel/cli/main.py` | 注册 `doctor --canon-suggest` 子命令 |
| 复用 | `opennovel/core/canon_checker.py` / `opennovel/core/llm.py` | 使用 CanonChecker 检测 + LLM 生成建议 |

**注意**：此项为 P3 可选项，仅在 Canon Exemption (Phase 2.2) 完成后的增强功能。

---

## Phase 4：用户触达与工作流闭环（2-3 天）

### 4.1 CLI 体验增强（取代 GUI 客户端）

**背景**：优化文档将"桌面 GUI 客户端"列为 P0 优先级。经代码验证和架构评估，结论如下：
- 项目当前**零 GUI 基础设施**（无 Flask/FastAPI/Streamlit/Electron/Tauri），GUI 是一个完整的独立产品
- GUI 应作为独立项目基于 MCP 协议构建，不进入主仓库
- 真正影响用户体验的是 CLI 自身的粗糙点，而非缺少图形界面

**目标**：聚焦"首次使用"体验，三件事覆盖 80% 的痛点。

**改动量**：~80 行

**涉及文件**：

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| 修改 | `opennovel/cli/main.py` | `novel init` 增加交互式引导 |
| 修改 | `opennovel/core/config.py` | `novel.yaml` 加载时 Schema 校验 |
| 修改 | `opennovel/cli/main.py` | `novel doctor` 升级为项目健康面板 |

#### 4.1.1 novel init 交互式引导

**问题**：当前 `init` 只接受一个 `typer.Argument(name)`，直接创建空项目，用户不知道下一步做什么。

**实现**：

```python
# novel init 增强
@app.command()
def init(
    name: str = typer.Argument(None, help="项目名称（留空进入交互模式）"),
    template: str = typer.Option("standard", help="项目模板: standard/minimal"),
):
    """初始化新小说项目（交互式引导）。"""
    if not name:
        # 交互模式
        name = typer.prompt("📚 项目名称")
        template = typer.prompt(
            "📋 项目模板",
            default="standard",
            type=click.Choice(["standard", "minimal"]),
        )

    # 自动检测模型环境
    import os
    has_deepseek = bool(os.environ.get("DEEPSEEK_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))

    if not has_deepseek and not has_openai:
        rprint("[yellow]⚠ 未检测到 API Key[/yellow]")
        rprint("  请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY 环境变量")
        rprint("  或稍后在 novel.yaml 中配置 api_key")

    # 选择快速开始或专家模式
    mode = typer.prompt(
        "🚀 选择模式",
        default="quick",
        type=click.Choice(["quick", "expert"]),
    )
    if mode == "quick":
        # 使用默认配置直接创建
        _create_with_defaults(name, template)
        rprint(f"[green]✓ 项目 '{name}' 已创建[/green]")
        rprint("  下一步: novel write ch_001  # 开始创作第一章")
    else:
        # 进入详细配置
        _create_with_interactive_config(name, template)
```

#### 4.1.2 novel.yaml Schema 校验

**问题**：配置无 Schema 校验，YAML 缩进错误或字段拼写错误在运行时才暴露，错误消息不精确。

**实现**：在 `LoomConfig.load()` 中增加 Pydantic 校验层。

```python
# core/config.py 增强
from pydantic import BaseModel, Field, ValidationError

class NovelConfigSchema(BaseModel):
    """novel.yaml 的 Pydantic 校验 Schema。"""
    version: str = Field(default="1.0", pattern=r"^\d+\.\d+\.\d+$")
    model: str = Field(default="deepseek/deepseek-v4-flash", min_length=3)
    token_budget: int = Field(default=8000, ge=1000, le=1_000_000)
    output_reserve: int = Field(default=2000, ge=0, le=100_000)
    creative_direction: str = Field(default="", max_length=500)
    target_chapters: int | None = Field(default=None, ge=1, le=1000)
    words_per_chapter: int | None = Field(default=None, ge=100, le=50_000)

    # Agents 配置（可选）
    agents: dict[str, dict[str, str]] = Field(default_factory=dict)

class LoomConfig:
    @classmethod
    def load(cls, project_root: Path) -> "LoomConfig":
        config_path = project_root / "novel.yaml"
        if not config_path.exists():
            return cls()

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # Schema 校验
            validated = NovelConfigSchema(**data)
            # ... 后续解析使用 validated 而非原始 data
        except ValidationError as e:
            rprint(f"[red]novel.yaml 配置错误:[/red]")
            for err in e.errors():
                loc = " → ".join(str(x) for x in err["loc"])
                rprint(f"  [bold]{loc}[/bold]: {err['msg']} (字段: {err['type']})")
            raise typer.Exit(1) from e
```

#### 4.1.3 novel doctor 升级为主入口

**目标**：`novel doctor` 从"世界线健康度诊断"升级为项目级健康面板，成为用户了解项目状态的统一入口。

```python
# novel doctor 输出示例
$ novel doctor

OpenNovel 项目健康面板 — demo_novel
──────────────────────────────────────
配置
  ✅ 模型: deepseek/deepseek-v4-flash (可用)
  ✅ API Key: 已配置 (deepseek)
  ⚠  causal_pressure 未配置，使用默认值 0.5

章节进度
  📝 已完成: 3/10 (30%)
  📊 最新章节: ch_003 — 85 分 (合格)
  📈 评分趋势: [78 → 82 → 85] 持续上升

角色
  👤 活跃角色: 2 (char_001, char_002)
  ✅ 角色状态一致性: 通过

事件
  📋 已记录: 15 个事件
  🔗 因果链完整: 是

诊断
  ✅ 正文与 Shadow 一致: 通过
  ⚠ 未发现未解决的伏笔 (共 2 个已激活)
```

**验收标准**：
- 首次运行 `novel init` 的新用户能在 3 步内完成项目创建
- 配置错误提供字段级精确错误消息（行号 + 字段名 + 错误原因）
- `novel doctor` 输出包含配置/进度/评分/角色/事件/诊断六类信息

---

### 4.2 MCP 协议完善

**目标**：补齐 MCP 工具覆盖范围，从当前 4 个工具扩展到 9 个，覆盖完整创作工作流。

**背景**：MCP 是 LLM IDE（如 Cursor/Claude Desktop）控制 OpenNovel 的桥梁。当前只暴露了核心 4 个工具（init / get_status / write_chapter / auto_create），缺失 commit / stash / diff / doctor / foreshadow，导致 LLM 无法完整操控创作流程。

**原则**：MCP handler 是"薄封装"——只做参数校验 + 调用内部函数 + 格式化返回，不包含业务逻辑。

**改动量**：~100 行（5 个新 handler × ~20 行/个）

**涉及文件**：

| 文件 | 改动 |
|:---|:---|
| `opennovel/mcp_server.py` | 新增 5 个 tool 定义 + 5 个 handler |

**实现**：

```python
# 新增工具定义
@server.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = [
        # ... 原有 4 个工具 ...
        types.Tool(
            name="commit",
            description="提取章节状态变更并固化（5 步审阅流程）",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "项目名称"},
                    "chapter": {"type": "string", "description": "章节 ID，如 ch_001"},
                },
                "required": ["project", "chapter"],
            },
        ),
        types.Tool(
            name="stash",
            description="存入灵感潜意识池",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "content": {"type": "string", "description": "灵感文本"},
                },
                "required": ["project", "content"],
            },
        ),
        types.Tool(
            name="doctor",
            description="运行项目健康度诊断",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "check": {
                        "type": "string",
                        "enum": ["all", "consistency", "canon", "calibration"],
                        "description": "诊断类型",
                    },
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="diff",
            description="检查正文与 Shadow 一致性",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "chapter": {"type": "string", "description": "章节 ID（可选，默认全部）"},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="foreshadow",
            description="查看/管理伏笔追踪表",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "resolve"],
                    },
                    "description": {"type": "string", "description": "add 时的伏笔描述"},
                },
                "required": ["project", "action"],
            },
        ),
    ]
    return tools
```

**Handler 封装模式**（以 commit 为例）：

```python
async def _handle_commit(args: dict) -> str:
    """MCP handler：提交章节（薄封装，业务逻辑在 cli/commit.py）。"""
    project = args["project"]
    chapter = args["chapter"]
    project_root = _resolve_project(project)

    # 复用 CLI 内部函数（需从 cli/commit.py 导出）
    from opennovel.cli.commit import do_commit
    result = do_commit(project_root, chapter)
    return json.dumps({
        "status": "success",
        "events_committed": len(result.events),
        "chapter_id": result.chapter_id,
    }, ensure_ascii=False)
```

**验收标准**：
- 5 个新工具在 `novel-mcp` 启动后可被 MCP 客户端发现
- 每个 handler 正确调用对应的 CLI 内部函数
- 错误时返回结构化错误信息而非崩溃

---

## Phase 5：创作干预机制（1 天 + 推迟项）

### 5.1 选择性接受（P1 必做）

**目标**：修复 `novel commit` 中标注"功能开发中"的 `[y/n/edit]` 占位符，实现逐事件确认。

**背景**：当前 commit 流的第 4 步（`cli/commit.py:126-128`）中，`edit` 选项是空壳：

```python
if choice.lower() == "edit":
    rprint("[dim]手动编辑功能开发中...[/dim]")
    return  # ← 直接返回，什么都不做
```

**改动量**：~15 行

**涉及文件**：

| 文件 | 改动 |
|:---|:---|
| `opennovel/cli/commit.py` | 将"全有或全无"改为逐事件确认循环 |

**实现**：

```python
# cli/commit.py Step 4 的改造
rprint("\n[bold]Step 4/5[/bold] 人工审阅（逐事件确认）")

confirmed_events = []
for i, evt in enumerate(events, 1):
    rprint(f"\n事件 {i}/{len(events)}: [bold]{evt.event_type}[/bold] - {evt.description}")
    rprint(f"  引用: \"{evt.source_text[:60]...}\"")
    rprint(f"  压强: {evt.causal_pressure}")
    choice = typer.prompt(f"  应用? [y/n/detail]", default="y")

    if choice.lower() == "detail":
        rprint(f"  完整描述: {evt.description}")
        rprint(f"  关联角色: {evt.character_id}")
        if evt.caused_by:
            rprint(f"  前置事件: {evt.caused_by}")
        choice = typer.prompt(f"  应用? [y/n]", default="y")

    if choice.lower() == "y":
        confirmed_events.append(evt)
        rprint(f"  [green]✓ 已确认[/green]")
    else:
        rprint(f"  [yellow]— 已跳过[/yellow]")

if not confirmed_events:
    rprint("[yellow]未确认任何事件，状态未变更[/yellow]")
    return

rprint(f"\n[bold]确认: {len(confirmed_events)}/{len(events)} 个事件将写入[/bold]")
```

**验收标准**：
- 用户可逐个确认或跳过事件
- `detail` 选项展示事件完整信息
- 跳过所有事件时干净退出
- 确认的事件正确写入 EventStore

---

### 5.2 动态约束调整（推迟）

**状态**：**已推迟**。等待用户真实反馈后启动。

**理由**：
- 架构成本高：需要 AutoRunner 增加暂停/恢复信号、章节间交互式检查点、运行时状态管理
- 缺乏需求证据：当前 `novel.yaml` 静态配置 + 章节间手动重启已覆盖大多数场景
- 替代方案：用户可通过修改 `novel.yaml` 的 `creative_direction` 字段 + 重启 `novel auto` 等效实现

**触发条件**：当收到用户明确反馈"跑到第 N 章发现想改文风，只能停掉重来"时，启动设计。

---

## Phase 6：本地资源优化（已关闭）

**状态**：**已关闭。经代码验证不构成实际瓶颈。**

### 验证过程

| 声称的瓶颈 | 代码验证结果 |
|:---|:---|
| 向量索引构建 | 一次性操作（`ensure_index()` 只在首次或内容变更时运行），可选依赖（sentence-transformers 为 `local-embedding` extra） |
| BGE-M3 模型加载 | 可选依赖，未安装时 LlamaIndex 使用默认 embedding |
| `betweenness_centrality()` 计算 | **仅在 docstring 中出现**（`causal_graph.py:10`）——生产代码零调用 |
| 因果图 DAG 构建 | ~2500 节点纯内存操作，<30ms |

### 实际瓶颈分析

OpenNovel 的运行时性能瓶颈在 **LLM API 调用**（每次 1-10 秒），而非本地计算。本地操作的耗时排序：

```
LLM API 调用:   1000-10000ms  ← 真正的瓶颈
向量索引构建:    100-300000ms  ← 一次性，非热路径
State Projector: <30ms
因果图 DAG 构建: <10ms
YAML 读写:       <5ms
```

### 结论

- ❌ 不需要"资源感知降级"
- ❌ 不需要"后台计算模式"
- ❌ 不需要"降低向量索引精度"
- ✅ 如果未来有性能报告，优先检查 LLM API 延迟和 `ensure_index()` 是否被频繁触发（后者说明缓存逻辑有 bug）

---

## 完整优先级总览

### 最终优先级矩阵

| 新编号 | 项目 | 优先级 | 阶段 | 依赖 |
|:---|:---|:---:|:---:|:---:|
| 1 | `chapter_utils.py` 抽取 | P0 | Phase 0.1 | — |
| 2 | 激活 `detect_strategy()` | P0 | Phase 0.2 | — |
| 3 | CLI 体验增强（`init`/校验/`doctor`） | P0 | Phase 4.1 | — |
| 4 | MCP 协议完善 | P1 | Phase 4.2 | — |
| 5 | `write_model_climax` | P1 | Phase 1.1 | #1 |
| 6 | State Projector | P1 | Phase 1.2 | — |
| 7 | 选择性接受（commit edit） | P1 | Phase 5.1 | — |
| 8 | Glass-Box Decision | P1 | Phase 2.1 | — |
| 9 | Canon Exemption | P2 | Phase 2.2 | — |
| 10 | Critic 校准诊断 | P2 | Phase 3.1 | — |
| 11 | Canon --suggest | P3 | Phase 3.2 | #9 |
| — | 动态约束调整 | 推迟 | Phase 5.2 | 用户反馈 |
| — | 资源感知降级 | 关闭 | Phase 6 | — |
| — | GUI 客户端 | 独立项目 | — | MCP 完备后 |

### 实施排序（推荐顺序）

1. **Phase 0**（P0 基础设施）：#1 + #2 并行 → 1 天
2. **Phase 4.1**（P0 CLI 体验）：#3 → 1-2 天
3. **Phase 4.2**（P1 MCP 补齐）：#4（可与上一步并行）→ 1 天
4. **Phase 1 + 5**（P1 创作质量）：#5 → #6 → #7 → 3-4 天
5. **Phase 2**（P1/P2 观测性）：#8 → #9 → 2-3 天
6. **Phase 3**（P2/P3 诊断）：#10 → #11 → 1-2 天

### 工作量估算

```
Phase  项目                     复杂度  风险  净代码量  人天
───────────────────────────────────────────────────────────
P0.1   chapter_utils.py 抽取     低    零     +15       0.5
P0.2   激活 detect_strategy()    低    零     +6        0.5
───────────────────────────────────────────────────────────
P1.1   write_model_climax        低    低     +20       1
P1.2   State Projector          中    低     +80       2-3
───────────────────────────────────────────────────────────
P2.1   Glass-Box Decision       中    低     +60       2
P2.2   Canon Exemption           低    低     +40       1
───────────────────────────────────────────────────────────
P3.1   Critic 校准诊断           低    零     +50       1
P3.2   Canon --suggest           低    零     +30       0.5
───────────────────────────────────────────────────────────
总计                                              ~300    7-12
```

### 并行策略

| 人力配置 | 实施路径 | 最短工期 |
|:---|:---|:---:|
| **1 人** | P0.1→P0.2→P1.1→P1.2→P2.1→P2.2→P3 | 7-12 天 |
| **2 人并⾏** | A: P0.1→P0.2→P1.1（主线 2 天）<br>B: P1.2 + P2.1 + P2.2（与主线并⾏ 3-4 天）<br>汇合后：P3（1 天） | **4-5 天** |
| **3 人并⾏** | A: P0.1→P0.2→P1.1（2 天）<br>B: P1.2（3 天）<br>C: P2.1 + P2.2（3 天）<br>汇合后：P3（1 天） | **4 天** |

### 风险最低的顺序

1. **先 P0.2（3 行改动）** + **P2.2（Canon Exemption）** → 获得早期信心
2. **再 P1.2（State Projector）** → 最大模块，尽早验证
3. **P2.1 + P1.1** → 中风险模块
4. **P3** → 零风险收尾

---

## 附录：与现有架构的关系

### 已更新的术语（CONTEXT.md）

| 术语 | 所属章节 | 说明 |
|:---|:---|:---|
| Glass-Box Decision Making | 决策透明化 |  Agent 决策可观测性原则 |
| Reasoning Chain | 决策透明化 | Think/Evaluate 阶段的推理链 |
| Trace ID | 决策透明化 | contextvars 隐式传播的关联标识 |
| State Projection | 状态投影 | 从事件流归约为状态快照 |
| State Projector | 状态投影 | ContextAssembler 的运行时数据源 |
| Canon Exemption | 决策透明化 | 规则豁免机制（行内 + Frontmatter） |

### 不修改的模块

以下模块在本路线图中保持不变：

- `opennovel/core/safety_fence.py` — 安全围栏逻辑不变
- `opennovel/core/tool_registry.py` — 工具注册中心
- `opennovel/core/agent_autonomy.py` — Agent 自治引擎
- `opennovel/storage/metrics.py` — MetricsStore Schema（无 migration）
- `opennovel/schemas/metrics.py` — AgentTrace / EvaluationHistory 结构不变
- 全部 Token 预算常量 — FRUGAL=8K, STANDARD=48K, PANORAMIC=128K (128K软限)

### 涉及的测试文件

| 项目 | 测试文件（新建/修改） |
|:---|:---|
| chapter_utils | `tests/test_chapter_utils.py`（新建） |
| state_projector | `tests/test_state_projector.py`（新建） |
| canon_exemption | `tests/test_canon_checker.py`（扩展） |
| evaluation_auditor | `tests/test_evaluation_auditor.py`（新建） |
| Glass-Box Decision | `tests/test_llm.py`（扩展）+ `tests/test_auto_runner.py`（扩展） |
| write_model_climax | `tests/test_writer.py`（扩展） |
| detect_strategy | `tests/test_context_assembler.py`（扩展） |
