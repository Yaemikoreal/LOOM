"""指标数据库数据模型 - 运行时遥测的 SQLModel 定义。

独立于 EventStore（叙事真相），记录运行时性能指标：
- token_usage: 每次 LLM 调用的 token 消耗
- evaluation_history: Critic 评审历史
- agent_trace: Agent 执行轨迹

详见 docs/adr/0004-independent-metrics-database.md。
"""

from datetime import datetime

from sqlmodel import Field as SQLField
from sqlmodel import SQLModel


class TokenUsage(SQLModel, table=True):
    """Token 消耗记录，每次 LLM 调用写入一行。"""

    __tablename__ = "token_usage"

    id: int | None = SQLField(default=None, primary_key=True)
    timestamp: str = SQLField(
        default_factory=lambda: datetime.now().isoformat(),
        description="记录时间",
    )
    agent: str = SQLField(description="调用 Agent: writer/critic/manager/director/actor")
    chapter_id: str = SQLField(default="", description="关联章节 ID")
    model: str = SQLField(description="使用的模型名称")
    prompt_tokens: int = SQLField(default=0, description="输入 token 数")
    completion_tokens: int = SQLField(default=0, description="输出 token 数")
    total_tokens: int = SQLField(default=0, description="总 token 数")
    call_type: str = SQLField(
        default="chat",
        description="调用类型: chat/write/revise/evaluate/think/outline",
    )


class EvaluationHistory(SQLModel, table=True):
    """评审历史记录，每次 Critic 评分写入一行。"""

    __tablename__ = "evaluation_history"

    id: int | None = SQLField(default=None, primary_key=True)
    timestamp: str = SQLField(
        default_factory=lambda: datetime.now().isoformat(),
        description="记录时间",
    )
    chapter_id: str = SQLField(description="章节 ID")
    total_score: int = SQLField(description="总分 (0-100)")
    dimension_writing: int = SQLField(default=0, description="文笔质量 (0-20)")
    dimension_plot: int = SQLField(default=0, description="情节逻辑 (0-20)")
    dimension_character: int = SQLField(default=0, description="角色一致性 (0-20)")
    dimension_rhythm: int = SQLField(default=0, description="节奏把控 (0-20)")
    dimension_emotion: int = SQLField(default=0, description="情感表达 (0-20)")
    is_pass: bool = SQLField(description="是否合格 (>=80)")
    retry_count: int = SQLField(default=0, description="第几次重试")
    mode: str = SQLField(
        default="evaluate",
        description="评审模式: evaluate/outline",
    )


class AgentTrace(SQLModel, table=True):
    """Agent 执行轨迹，记录每次 Agent 调用的元数据。"""

    __tablename__ = "agent_trace"

    id: int | None = SQLField(default=None, primary_key=True)
    timestamp: str = SQLField(
        default_factory=lambda: datetime.now().isoformat(),
        description="记录时间",
    )
    agent: str = SQLField(description="Agent 名称: writer/critic/manager/director/actor")
    action: str = SQLField(
        description="执行动作: think/write/revise/evaluate/update/analyze/think_variations"
    )
    chapter_id: str = SQLField(default="", description="关联章节 ID")
    duration_ms: int = SQLField(default=0, description="执行耗时（毫秒）")
    status: str = SQLField(default="success", description="执行状态: success/error")
    detail: str = SQLField(default="", description="补充信息（如错误消息）")


class AuditLog(SQLModel, table=True):
    """工具调用审计日志 — ADR 0010 治理基础设施。

    记录每次 ToolRegistry.execute() 的调用详情，
    用于事后审计 Agent 自治行为。
    """

    __tablename__ = "audit_log"

    id: int | None = SQLField(default=None, primary_key=True)
    timestamp: str = SQLField(
        default_factory=lambda: datetime.now().isoformat(),
        description="记录时间",
    )
    agent: str = SQLField(default="", description="发起调用的 Agent 名称")
    tool_name: str = SQLField(default="", description="工具名称")
    source: str = SQLField(default="", description="数据源 (KnowledgeSource)")
    concept: str = SQLField(default="", description="查询概念")
    status: str = SQLField(default="success", description="执行状态: success/denied/error")
    duration_ms: int = SQLField(default=0, description="执行耗时（毫秒）")
    detail: str = SQLField(default="", description="补充信息（错误消息或权限拒绝原因）")


class StateCacheEntry(SQLModel, table=True):
    """State Projector 缓存条目 — EventLog 折叠后的角色状态快照。

    与 EventStore（叙事真相）独立，仅为运行时性能优化缓存。
    可通过 invalidate 后重新折叠恢复。

    详见 CONTEXT.md 的 State Digest 条目。
    """

    __tablename__ = "state_cache"

    id: int | None = SQLField(default=None, primary_key=True)
    character_id: str = SQLField(index=True, description="角色 Canonical ID")
    chapter_id: str = SQLField(description="最新缓存的章节 ID")
    state_json: str = SQLField(description="CharacterStateSnapshot 的 JSON 序列化")
    digest_text: str = SQLField(default="", description="渲染后的自然语言摘要文本")
    updated_at: str = SQLField(
        default_factory=lambda: datetime.now().isoformat(),
        description="最后更新时间",
    )
