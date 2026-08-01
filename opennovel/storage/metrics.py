"""指标数据库存储适配层。

独立于 EventStore（叙事真相），记录运行时遥测数据：
- Token 消耗追踪
- 评审历史
- Agent 执行轨迹

数据库路径: .novel.metrics.db（与 .novel.db 分离）

详见 docs/adr/0004-independent-metrics-database.md。
"""

import logging
import time
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from opennovel.schemas.metrics import AgentTrace, EvaluationHistory, TokenUsage

logger = logging.getLogger(__name__)


class MetricsStore:
    """指标数据库存储，管理运行时遥测数据。

    使用方式:
        with MetricsStore(db_path) as store:
            store.record_token_usage("writer", "ch_001", "gpt-4", 100, 200)
    """

    def __init__(self, db_path: Path) -> None:
        """初始化指标存储。

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self._create_tables()

    def close(self) -> None:
        """关闭数据库引擎。"""
        self._engine.dispose()

    def __enter__(self) -> "MetricsStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _create_tables(self) -> None:
        """创建数据库表结构。"""
        SQLModel.metadata.create_all(self._engine)

    # ── Token 消耗 ──────────────────────────────────────────────────

    def record_token_usage(
        self,
        agent: str,
        chapter_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        call_type: str = "chat",
    ) -> TokenUsage:
        """记录一次 LLM 调用的 token 消耗。

        Args:
            agent: 调用 Agent 名称
            chapter_id: 关联章节 ID
            model: 使用的模型名称
            prompt_tokens: 输入 token 数
            completion_tokens: 输出 token 数
            call_type: 调用类型

        Returns:
            写入的 TokenUsage 记录
        """
        record = TokenUsage(
            agent=agent,
            chapter_id=chapter_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            call_type=call_type,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def get_total_usage(
        self,
        agent: str | None = None,
        chapter_id: str | None = None,
    ) -> dict[str, int]:
        """汇总 token 消耗。

        Args:
            agent: 按 Agent 过滤（可选）
            chapter_id: 按章节过滤（可选）

        Returns:
            {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
        """
        with Session(self._engine) as session:
            statement = select(TokenUsage)
            if agent:
                statement = statement.where(TokenUsage.agent == agent)
            if chapter_id:
                statement = statement.where(TokenUsage.chapter_id == chapter_id)
            records = list(session.exec(statement).all())

        return {
            "prompt_tokens": sum(r.prompt_tokens for r in records),
            "completion_tokens": sum(r.completion_tokens for r in records),
            "total_tokens": sum(r.total_tokens for r in records),
        }

    def get_usage_by_agent(self) -> dict[str, dict[str, int]]:
        """按 Agent 分组汇总 token 消耗。

        Returns:
            {"writer": {"prompt_tokens": N, ...}, "critic": {...}, ...}
        """
        with Session(self._engine) as session:
            records = list(session.exec(select(TokenUsage)).all())

        result: dict[str, dict[str, int]] = {}
        for r in records:
            if r.agent not in result:
                result[r.agent] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            result[r.agent]["prompt_tokens"] += r.prompt_tokens
            result[r.agent]["completion_tokens"] += r.completion_tokens
            result[r.agent]["total_tokens"] += r.total_tokens
        return result

    # ── 评审历史 ────────────────────────────────────────────────────

    def record_evaluation(
        self,
        chapter_id: str,
        total_score: int,
        dimensions: list[int],
        is_pass: bool,
        retry_count: int = 0,
        mode: str = "evaluate",
    ) -> EvaluationHistory:
        """记录一次评审结果。

        Args:
            chapter_id: 章节 ID
            total_score: 总分
            dimensions: 五维分数列表 [文笔, 情节, 角色, 节奏, 情感]
            is_pass: 是否合格
            retry_count: 重试次数
            mode: 评审模式

        Returns:
            写入的 EvaluationHistory 记录
        """
        # 补齐维度到 5 个
        while len(dimensions) < 5:
            dimensions.append(0)

        record = EvaluationHistory(
            chapter_id=chapter_id,
            total_score=total_score,
            dimension_writing=dimensions[0],
            dimension_plot=dimensions[1],
            dimension_character=dimensions[2],
            dimension_rhythm=dimensions[3],
            dimension_emotion=dimensions[4],
            is_pass=is_pass,
            retry_count=retry_count,
            mode=mode,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def get_evaluation_history(
        self,
        chapter_id: str | None = None,
    ) -> list[EvaluationHistory]:
        """查询评审历史。

        Args:
            chapter_id: 按章节过滤（可选）

        Returns:
            评审历史记录列表
        """
        with Session(self._engine) as session:
            statement = select(EvaluationHistory)
            if chapter_id:
                statement = statement.where(EvaluationHistory.chapter_id == chapter_id)
            return list(session.exec(statement).all())

    def get_average_scores(self) -> dict[str, float]:
        """汇总平均分。

        Returns:
            {"total": avg, "writing": avg, "plot": avg, ...}
        """
        with Session(self._engine) as session:
            records = list(session.exec(select(EvaluationHistory)).all())

        if not records:
            return {}

        n = len(records)
        return {
            "total": sum(r.total_score for r in records) / n,
            "writing": sum(r.dimension_writing for r in records) / n,
            "plot": sum(r.dimension_plot for r in records) / n,
            "character": sum(r.dimension_character for r in records) / n,
            "rhythm": sum(r.dimension_rhythm for r in records) / n,
            "emotion": sum(r.dimension_emotion for r in records) / n,
        }

    # ── Agent 轨迹 ──────────────────────────────────────────────────

    def record_trace(
        self,
        agent: str,
        action: str,
        chapter_id: str = "",
        duration_ms: int = 0,
        status: str = "success",
        detail: str = "",
    ) -> AgentTrace:
        """记录一次 Agent 执行轨迹。

        Args:
            agent: Agent 名称
            action: 执行动作
            chapter_id: 关联章节 ID
            duration_ms: 执行耗时（毫秒）
            status: 执行状态
            detail: 补充信息

        Returns:
            写入的 AgentTrace 记录
        """
        record = AgentTrace(
            agent=agent,
            action=action,
            chapter_id=chapter_id,
            duration_ms=duration_ms,
            status=status,
            detail=detail,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def get_traces(
        self,
        agent: str | None = None,
        chapter_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentTrace]:
        """查询 Agent 执行轨迹。

        Args:
            agent: 按 Agent 过滤（可选）
            chapter_id: 按章节过滤（可选）
            limit: 返回数量上限

        Returns:
            轨迹记录列表
        """
        with Session(self._engine) as session:
            statement = select(AgentTrace)
            if agent:
                statement = statement.where(AgentTrace.agent == agent)
            if chapter_id:
                statement = statement.where(AgentTrace.chapter_id == chapter_id)
            statement = statement.order_by(AgentTrace.id.desc()).limit(limit)
            return list(session.exec(statement).all())

    @contextmanager
    def trace(self, agent: str, action: str, chapter_id: str = ""):
        """上下文管理器，自动记录执行耗时和状态。

        使用方式:
            with store.trace("writer", "write", "ch_001"):
                text = writer.write(...)
        """
        start = time.monotonic()
        status = "success"
        detail = ""
        try:
            yield
        except Exception as e:
            status = "error"
            detail = str(e)[:500]
            raise
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self.record_trace(agent, action, chapter_id, elapsed_ms, status, detail)
