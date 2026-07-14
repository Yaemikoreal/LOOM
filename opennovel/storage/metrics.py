"""指标数据库存储适配层。

已合并到 .novel.db（P0 架构简化）：
- Token 消耗追踪
- 评审历史
- Agent 执行轨迹
- State Cache

旧项目首次打开时，自动将 .novel.metrics.db 迁移到 .novel.db；
迁移失败则静默降级，使用新的空表。

详见 docs/adr/0004-independent-metrics-database.md（已标记为 Superseded）。
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, create_engine, select

from opennovel.schemas.metrics import (
    AgentTrace,
    AuditLog,
    EvaluationHistory,
    StateCacheEntry,
    TokenUsage,
)

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
            db_path: SQLite 数据库文件路径。推荐 .novel.db。
        """
        self.db_path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self._create_tables()
        # P0: 迁移旧 metrics 数据库到 .novel.db
        self._maybe_migrate_legacy_metrics()

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

    def _maybe_migrate_legacy_metrics(self) -> None:
        """迁移旧的 .novel.metrics.db 到当前 .novel.db。

        仅当当前 db_path 是 .novel.db 且同级目录存在 .novel.metrics.db 时触发。
        迁移成功后重命名旧数据库为 .novel.metrics.db.migrated。
        迁移失败则静默降级，不阻塞用户。
        """
        if self.db_path.name != ".novel.db":
            return

        legacy_path = self.db_path.parent / ".novel.metrics.db"
        if not legacy_path.exists():
            return

        try:
            legacy_engine = create_engine(f"sqlite:///{legacy_path}", echo=False)
            with Session(legacy_engine) as legacy_session:
                token_usage = list(legacy_session.exec(select(TokenUsage)).all())
                evaluations = list(legacy_session.exec(select(EvaluationHistory)).all())
                traces = list(legacy_session.exec(select(AgentTrace)).all())
                state_caches = list(legacy_session.exec(select(StateCacheEntry)).all())
            legacy_engine.dispose()

            def _copy_token_usage(record: TokenUsage) -> TokenUsage:
                return TokenUsage(
                    agent=record.agent,
                    chapter_id=record.chapter_id,
                    model=record.model,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    total_tokens=record.total_tokens,
                    call_type=record.call_type,
                    timestamp=record.timestamp,
                )

            def _copy_evaluation(record: EvaluationHistory) -> EvaluationHistory:
                return EvaluationHistory(
                    chapter_id=record.chapter_id,
                    total_score=record.total_score,
                    dimension_writing=record.dimension_writing,
                    dimension_plot=record.dimension_plot,
                    dimension_character=record.dimension_character,
                    dimension_rhythm=record.dimension_rhythm,
                    dimension_emotion=record.dimension_emotion,
                    is_pass=record.is_pass,
                    retry_count=record.retry_count,
                    mode=record.mode,
                    timestamp=record.timestamp,
                )

            def _copy_trace(record: AgentTrace) -> AgentTrace:
                return AgentTrace(
                    agent=record.agent,
                    action=record.action,
                    chapter_id=record.chapter_id,
                    duration_ms=record.duration_ms,
                    status=record.status,
                    detail=record.detail,
                    timestamp=record.timestamp,
                )

            def _copy_state_cache(record: StateCacheEntry) -> StateCacheEntry:
                return StateCacheEntry(
                    character_id=record.character_id,
                    chapter_id=record.chapter_id,
                    state_json=record.state_json,
                    digest_text=record.digest_text,
                    updated_at=record.updated_at,
                )

            with Session(self._engine) as session:
                for record in token_usage:
                    session.add(_copy_token_usage(record))
                for record in evaluations:
                    session.add(_copy_evaluation(record))
                for record in traces:
                    session.add(_copy_trace(record))
                for record in state_caches:
                    session.add(_copy_state_cache(record))
                session.commit()

            migrated_path = legacy_path.with_suffix(".db.migrated")
            legacy_path.rename(migrated_path)
            logger.info(
                "已迁移旧 metrics 数据库: %d token, %d evaluation, %d trace, %d state_cache 记录",
                len(token_usage),
                len(evaluations),
                len(traces),
                len(state_caches),
            )
        except Exception as e:
            logger.warning("迁移旧 metrics 数据库失败，已静默降级: %s", e)

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

    # ── State Cache (Phase 3) ──────────────────────────────────────────

    def set_state_cache(self, entry: StateCacheEntry) -> StateCacheEntry:
        """写入或更新状态缓存（upsert by character_id）。

        Args:
            entry: 状态缓存条目

        Returns:
            写入后的缓存条目
        """
        with Session(self._engine) as session:
            statement = select(StateCacheEntry).where(
                StateCacheEntry.character_id == entry.character_id,
            )
            existing = session.exec(statement).first()
            if existing:
                existing.chapter_id = entry.chapter_id
                existing.state_json = entry.state_json
                existing.digest_text = entry.digest_text
                existing.updated_at = datetime.now().isoformat()
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_state_cache(self, character_id: str) -> StateCacheEntry | None:
        """获取指定角色的状态缓存。

        Args:
            character_id: 角色 Canonical ID

        Returns:
            缓存条目，不存在返回 None
        """
        with Session(self._engine) as session:
            statement = select(StateCacheEntry).where(
                StateCacheEntry.character_id == character_id,
            )
            return session.exec(statement).first()

    def get_all_state_caches(self) -> list[StateCacheEntry]:
        """获取所有角色的状态缓存。"""
        with Session(self._engine) as session:
            statement = select(StateCacheEntry)
            return list(session.exec(statement).all())

    def invalidate_state_cache(self, character_id: str) -> None:
        """使指定角色的状态缓存失效。

        Args:
            character_id: 角色 Canonical ID
        """
        with Session(self._engine) as session:
            statement = select(StateCacheEntry).where(
                StateCacheEntry.character_id == character_id,
            )
            existing = session.exec(statement).first()
            if existing:
                session.delete(existing)
                session.commit()

    # ── 审计日志 (ADR 0010 Phase 3 治理基础设施) ──────────────────────────

    def record_audit_log(
        self,
        agent: str = "",
        tool_name: str = "",
        source: str = "",
        concept: str = "",
        status: str = "success",
        duration_ms: int = 0,
        detail: str = "",
    ) -> AuditLog:
        """记录工具调用审计日志。

        ADR 0010 治理模型要求：ToolRegistry.execute() 的 finally 块写入审计日志，
        用于事后追溯 Agent 自治行为。

        Args:
            agent: 发起调用的 Agent 名称
            tool_name: 工具名称
            source: 数据源 (KnowledgeSource 值)
            concept: 查询概念
            status: 执行状态 (success/denied/error)
            duration_ms: 执行耗时（毫秒）
            detail: 补充信息

        Returns:
            写入的 AuditLog 记录
        """
        entry = AuditLog(
            agent=agent,
            tool_name=tool_name,
            source=source,
            concept=concept,
            status=status,
            duration_ms=duration_ms,
            detail=detail,
        )
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            # 脱离 session 以便调用方安全读取属性
            session.expunge(entry)
        return entry

    def get_audit_logs(
        self,
        agent: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        """查询审计日志。

        Args:
            agent: 按 Agent 过滤（可选）
            tool_name: 按工具名过滤（可选）
            status: 按状态过滤（可选）
            limit: 返回条数上限

        Returns:
            审计日志列表
        """
        with Session(self._engine) as session:
            statement = select(AuditLog)
            if agent:
                statement = statement.where(AuditLog.agent == agent)
            if tool_name:
                statement = statement.where(AuditLog.tool_name == tool_name)
            if status:
                statement = statement.where(AuditLog.status == status)
            statement = statement.order_by(AuditLog.id.desc()).limit(limit)
            return list(session.exec(statement).all())

    def get_cost_report(
        self,
        model_prices: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        """按 agent / model / call_type 汇总成本。

        Args:
            model_prices: 模型单价表，格式
                {"model_name": {"prompt": 每 1k tokens 价格, "completion": 每 1k tokens 价格}}
                未提供时使用内置默认价目表。

        Returns:
            成本报告字典，包含 lines（明细列表）和 total_cost。
        """
        # 默认单价表（美元/1k tokens），仅作估算
        default_prices: dict[str, dict[str, float]] = {
            "gpt-4": {"prompt": 0.03, "completion": 0.06},
            "gpt-4o": {"prompt": 0.005, "completion": 0.015},
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
            "deepseek/deepseek-v4": {"prompt": 0.001, "completion": 0.002},
            "deepseek/deepseek-v4-flash": {"prompt": 0.0005, "completion": 0.001},
            "claude-3-5-sonnet-20240620": {"prompt": 0.003, "completion": 0.015},
            "claude-3-haiku-20240307": {"prompt": 0.00025, "completion": 0.00125},
        }
        prices = model_prices or default_prices

        with Session(self._engine) as session:
            records = list(session.exec(select(TokenUsage)).all())

        # 按 (agent, model, call_type) 分组
        groups: dict[tuple[str, str, str], dict[str, int]] = {}
        for r in records:
            key = (r.agent, r.model, r.call_type)
            if key not in groups:
                groups[key] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
            groups[key]["calls"] += 1
            groups[key]["prompt_tokens"] += r.prompt_tokens
            groups[key]["completion_tokens"] += r.completion_tokens

        lines: list[dict[str, Any]] = []
        total_cost = 0.0
        for (agent, model, call_type), stats in sorted(groups.items()):
            model_key = next((k for k in prices if k in model), None)
            price = prices.get(model_key, {"prompt": 0.0, "completion": 0.0})
            prompt_cost = stats["prompt_tokens"] * price["prompt"] / 1000
            completion_cost = stats["completion_tokens"] * price["completion"] / 1000
            cost = prompt_cost + completion_cost
            total_cost += cost
            lines.append(
                {
                    "agent": agent,
                    "model": model,
                    "call_type": call_type,
                    "calls": stats["calls"],
                    "prompt_tokens": stats["prompt_tokens"],
                    "completion_tokens": stats["completion_tokens"],
                    "cost": round(cost, 4),
                }
            )

        return {
            "lines": lines,
            "total_cost": round(total_cost, 4),
            "currency": "USD",
        }
