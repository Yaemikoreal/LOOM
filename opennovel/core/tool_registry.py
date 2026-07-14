"""工具注册中心 — Agent 自治的查询基础设施。

负责注册和分发 Agent 的知识查询请求（KnowledgeNeed），
路由到对应的数据源（Retriever、EventStore、YAMLStorage）。

使用方式:
    registry = ToolRegistry(project_root, retriever, event_store, storage)
    results = registry.fulfill([KnowledgeNeed(concept="魔法", source="canon")])
    for r in results:
        print(r.content)
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeResult, KnowledgeSource

logger = logging.getLogger(__name__)

# 工具处理器类型签名
ToolHandler = Callable[[KnowledgeNeed], KnowledgeResult]


class ToolRegistry:
    """工具注册中心。

    管理所有可用的查询工具，提供统一的 fulfill() 接口
    供 AutoRunner 在检测到知识缺口时调用。
    """

    def __init__(
        self,
        project_root: Path,
        retriever: Any | None = None,
        event_store: Any | None = None,
        storage: Any | None = None,
        search_pipeline: Any | None = None,
        metrics_store: Any | None = None,
    ) -> None:
        """初始化工具注册中心。

        Args:
            project_root: 项目根目录路径
            retriever: 语义检索实例（用于 canon/subconscious 查询）
            event_store: 事件账本实例（用于 event 查询）
            storage: YAML 存储实例（用于 character 查询）
            search_pipeline: SearchPipeline 实例（ADR 0007 三通道管道），
                             提供时不使用旧 retriever 路径
            metrics_store: MetricsStore 实例（ADR 0010 审计日志）
        """
        self.project_root = project_root
        self._retriever = retriever
        self._event_store = event_store
        self._storage = storage
        self._search_pipeline = search_pipeline
        self._metrics_store = metrics_store
        self._tools: dict[KnowledgeSource, ToolHandler] = {
            KnowledgeSource.CANON: self._query_canon,
            KnowledgeSource.SUBCONSCIOUS: self._query_subconscious,
            KnowledgeSource.CHARACTER: self._query_character,
            KnowledgeSource.EVENT: self._query_event,
            KnowledgeSource.CAUSAL_CHAIN: self._query_causal_chain,
        }

    # ── 公开接口 ─────────────────────────────────────────────────────────

    def execute(
        self,
        need: KnowledgeNeed,
        safety_fence: Any | None = None,
        agent: str = "",
    ) -> KnowledgeResult:
        """带权限检查、重试降级和审计日志的工具调用入口（Phase 3 治理入口）。

        ADR 0010 治理模型四个支柱：
        1. 权限门控：Agent 越权调用时返回拒绝结果
        2. 层级化重试：单次调用失败自动重试（指数退避），全部失败降级
        3. 审计日志：finally 块写入 MetricsStore，事后可追溯
        4. 跨组件联动：AutoRunner 编排器显式调用

        Args:
            need: 单个知识需求
            safety_fence: 安全围栏实例（用于权限检查）
            agent: 发起调用的 Agent 名称

        Returns:
            查询结果（权限拒绝或查询失败时返回 relevance=0.0 的降级结果）
        """
        start_time = time.perf_counter()
        tool_name = self._need_to_tool_name(need)
        status = "success"
        detail = ""
        result: KnowledgeResult | None = None

        try:
            # 权限检查
            if (
                safety_fence
                and agent
                and not safety_fence.check_tool_permission(agent, tool_name)
            ):
                logger.warning(
                    "Agent '%s' 无权调用工具 '%s'，已拒绝",
                    agent,
                    tool_name,
                )
                status = "denied"
                detail = f"Agent '{agent}' 无权限调用 '{tool_name}'"
                result = KnowledgeResult(
                    content=f"[权限拒绝] {detail}",
                    source=need.source,
                    concept=need.concept,
                    relevance=0.0,
                )
                return result

            # 查找 handler
            handler = self._tools.get(need.source)
            if handler is None:
                status = "error"
                detail = f"未知数据源: {need.source.value}"
                result = KnowledgeResult(
                    content="",
                    source=need.source,
                    concept=need.concept,
                    relevance=0.0,
                )
                return result

            # 带重试的执行
            result = self._execute_with_retry(need, handler)
            # 检测是否为降级结果（检索失败）
            if result.content.startswith("[检索失败"):
                status = "error"
                detail = result.content
            return result

        except Exception as e:
            status = "error"
            detail = str(e)[:500]
            logger.error("工具调用异常: tool=%s, agent=%s, error=%s", tool_name, agent, e)
            result = KnowledgeResult(
                content=f"[工具执行异常: {detail}]",
                source=need.source,
                concept=need.concept,
                relevance=0.0,
            )
            return result

        finally:
            # ADR 0010 Phase 3：审计日志 — finally 块中直接写入
            if self._metrics_store is not None:
                try:
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    self._metrics_store.record_audit_log(
                        agent=agent or "unknown",
                        tool_name=tool_name,
                        source=need.source.value,
                        concept=need.concept[:200],
                        status=status,
                        duration_ms=elapsed_ms,
                        detail=detail[:500] if detail else (
                            f"relevance={result.relevance:.2f}" if result else ""
                        ),
                    )
                except Exception as audit_err:
                    # 审计日志写入失败不应影响主流程
                    logger.debug("审计日志写入失败（非阻断）: %s", audit_err)

    def fulfill(
        self,
        needs: list[KnowledgeNeed],
        safety_fence: Any | None = None,
        agent: str = "",
    ) -> list[KnowledgeResult]:
        """批量满足知识需求（保留旧接口兼容，内部调用 execute）。

        对每个 KnowledgeNeed 调用对应的工具，
        返回所有成功查询的结果。

        Args:
            needs: 知识需求列表
            safety_fence: 安全围栏实例（用于权限检查，Phase 3 治理）
            agent: 发起调用的 Agent 名称

        Returns:
            查询结果列表（仅包含成功的查询）
        """
        results: list[KnowledgeResult] = []
        for need in needs:
            try:
                result = self.execute(need, safety_fence=safety_fence, agent=agent)
                results.append(result)
            except Exception as e:
                logger.warning(
                    "知识查询失败: concept=%s, source=%s, error=%s",
                    need.concept,
                    need.source,
                    e,
                )
        return results

    def get_available_sources(self) -> list[str]:
        """获取所有已注册的数据源名称。"""
        return [s.value for s in self._tools]

    def is_source_available(self, source: KnowledgeSource) -> bool:
        """检查指定数据源是否可用。"""
        return source in self._tools

    # ── 治理方法 ─────────────────────────────────────────────────────────

    def _execute_with_retry(
        self,
        need: KnowledgeNeed,
        handler: ToolHandler,
        max_retries: int = 3,
    ) -> KnowledgeResult:
        """带重试和降级的 handler 执行包装。

        策略：重试(max_retries次，指数退避) → 全部失败 → 降级返回空结果

        Args:
            need: 知识需求
            handler: 工具处理器函数
            max_retries: 最大重试次数（默认 3）

        Returns:
            查询结果（全部失败时返回 relevance=0.0 的降级结果）
        """
        last_error = ""
        for attempt in range(max_retries):
            try:
                return handler(need)
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    backoff = 0.5 * (2**attempt)
                    logger.warning(
                        "工具 %s 执行失败 (尝试 %d/%d): %s，%.1fs 后重试",
                        need.source.value,
                        attempt + 1,
                        max_retries,
                        e,
                        backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "工具 %s 执行失败 %d 次，已降级: %s",
                        need.source.value,
                        max_retries,
                        e,
                    )

        # 全部失败：降级返回空结果，并在内容中标注“检索失败”
        return KnowledgeResult(
            content=f"[检索失败: {last_error[:200]}]",
            source=need.source,
            concept=need.concept,
            relevance=0.0,
        )

    @staticmethod
    def _need_to_tool_name(need: KnowledgeNeed) -> str:
        """将 KnowledgeNeed 映射为工具名（用于权限表查表）。

        Args:
            need: 知识需求

        Returns:
            工具名字符串
        """
        mapping = {
            KnowledgeSource.CANON: "query_canon",
            KnowledgeSource.SUBCONSCIOUS: "query_subconscious",
            KnowledgeSource.CHARACTER: "query_character",
            KnowledgeSource.EVENT: "query_event",
            KnowledgeSource.CAUSAL_CHAIN: "query_causal_chain",
        }
        return mapping.get(need.source, "unknown")

    # ── 工具实现 ─────────────────────────────────────────────────────────

    def _query_through_pipeline(
        self,
        need: KnowledgeNeed,
        source: KnowledgeSource,
        retriever_method: str,
        not_found_msg: str,
    ) -> KnowledgeResult | None:
        """通过 SearchPipeline 查询（共享方法），返回 None 时走 fallback。

        Args:
            need: 知识需求
            source: 目标来源枚举
            retriever_method: 旧 Retriever 方法名 ("query_canon" / "query_subconscious")
            not_found_msg: 无结果时的提示文案

        Returns:
            KnowledgeResult（已提取 relevance 分数）或 None（需 fallback）
        """
        # SearchPipeline 路径（ADR 0007 RRF 优先）
        if self._search_pipeline is not None:
            query = f"{need.concept} {need.context}".strip()[:500]
            try:
                result = self._search_pipeline.search_for_agent(query, top_k=2)
                if result.has_results:
                    # 提取 top-1 的 rrf_score 作为 relevance（归一化到 0~1）
                    relevance = result.chunks[0].rrf_score if result.chunks else 0.0
                    # rrf_score 通常 0.02~0.05，映射到 0.3~1.0 范围
                    relevance = min(1.0, relevance * 20)
                    content = result.format_for_context(max_chars=1000)
                    return KnowledgeResult(
                        content=content,
                        source=source,
                        concept=need.concept,
                        relevance=relevance,
                    )
            except Exception as e:
                logger.warning("SearchPipeline %s 查询失败: %s", source.value, e)

        # 旧路径 fallback
        if self._retriever is None:
            return KnowledgeResult(
                content="",
                source=source,
                concept=need.concept,
                relevance=0.0,
            )
        query = f"{need.concept} {need.context}".strip()[:500]
        retriever_fn = getattr(self._retriever, retriever_method, None)
        if retriever_fn is None:
            return KnowledgeResult(
                content="",
                source=source,
                concept=need.concept,
                relevance=0.0,
            )
        content = retriever_fn(query, top_k=2)
        return KnowledgeResult(
            content=content or not_found_msg,
            source=source,
            concept=need.concept,
            relevance=1.0 if content else 0.0,
        )

    def _query_canon(self, need: KnowledgeNeed) -> KnowledgeResult:
        """查询世界观设定文档。"""
        return self._query_through_pipeline(
            need,
            KnowledgeSource.CANON,
            "query_canon",
            "未找到相关设定",
        )

    def _query_subconscious(self, need: KnowledgeNeed) -> KnowledgeResult:
        """查询灵感潜意识池。"""
        return self._query_through_pipeline(
            need,
            KnowledgeSource.SUBCONSCIOUS,
            "query_subconscious",
            "未找到相关灵感",
        )

    def _query_character(self, need: KnowledgeNeed) -> KnowledgeResult:
        """查询角色当前状态。

        Args:
            need: 知识需求（需指定 character_id）

        Returns:
            查询结果
        """
        char_id = need.character_id or need.concept
        if self._storage is None:
            return KnowledgeResult(
                content="",
                source=KnowledgeSource.CHARACTER,
                concept=need.concept,
                relevance=0.0,
            )
        try:
            char_path = self.project_root / "characters" / f"{char_id}.md"
            if not char_path.exists():
                return KnowledgeResult(
                    content=f"角色文件不存在: {char_id}",
                    source=KnowledgeSource.CHARACTER,
                    concept=need.concept,
                    relevance=0.0,
                )
            char_data = self._storage.read_character_file(char_path)
            fm = char_data.frontmatter
            # 提取状态摘要（兼容 dict 和 Pydantic 对象的访问方式）
            if hasattr(fm, "model_dump"):
                fm_dict = fm.model_dump()
            elif hasattr(fm, "dict"):
                fm_dict = fm.dict()
            else:
                fm_dict = dict(fm) if isinstance(fm, dict) else {}
            physical = fm_dict.get("physical", {})
            emotional = fm_dict.get("emotional", {})
            injuries = physical.get("injuries", []) if isinstance(physical, dict) else []
            emotions_str = (
                ", ".join(f"{k}={v}" for k, v in emotional.items() if v and float(v) > 0)
                if isinstance(emotional, dict)
                else ""
            )
            name = fm_dict.get("name") or fm.name if hasattr(fm, "name") else char_id
            location = fm_dict.get("location") or (
                fm.location if hasattr(fm, "location") else "未知"
            )
            lines = [f"角色: {name}"]
            if injuries:
                lines.append(f"伤势: {', '.join(injuries)}")
            if emotions_str:
                lines.append(f"情绪: {emotions_str}")
            lines.append(f"位置: {location}")
            content = "\n".join(lines)

            return KnowledgeResult(
                content=content,
                source=KnowledgeSource.CHARACTER,
                concept=need.concept,
                relevance=1.0,
            )
        except Exception as e:
            logger.warning("角色查询失败 %s: %s", char_id, e)
            return KnowledgeResult(
                content=f"角色查询失败: {e}",
                source=KnowledgeSource.CHARACTER,
                concept=need.concept,
                relevance=0.0,
            )

    def _query_event(self, need: KnowledgeNeed) -> KnowledgeResult:
        """查询事件账本。

        Args:
            need: 知识需求

        Returns:
            查询结果
        """
        if self._event_store is None:
            return KnowledgeResult(
                content="",
                source=KnowledgeSource.EVENT,
                concept=need.concept,
                relevance=0.0,
            )
        try:
            char_id = need.character_id or need.concept
            if char_id.startswith("char_"):
                events = self._event_store.get_events_by_character(char_id)
                if events:
                    lines = [f"{e.event_type}: {e.description}" for e in events[-5:]]
                    content = "\n".join(lines)
                else:
                    content = f"角色 {char_id} 无事件记录"
            else:
                high_events = self._event_store.get_high_pressure_events(threshold=0.5)
                lines = [
                    f"[{e.chapter_id}] {e.event_type}: {e.description}" for e in high_events[-5:]
                ]
                content = "\n".join(lines) if lines else "无高压力事件"

            return KnowledgeResult(
                content=content,
                source=KnowledgeSource.EVENT,
                concept=need.concept,
                relevance=1.0,
            )
        except Exception as e:
            logger.warning("事件查询失败: %s", e)
            return KnowledgeResult(
                content=f"事件查询失败: {e}",
                source=KnowledgeSource.EVENT,
                concept=need.concept,
                relevance=0.0,
            )

    def _query_causal_chain(self, need: KnowledgeNeed) -> KnowledgeResult:
        """查询事件因果链（SQL 递归追溯）。

        Args:
            need: 知识需求（concept 应为 event_id）

        Returns:
            查询结果
        """
        if self._event_store is None:
            return KnowledgeResult(
                content="",
                source=KnowledgeSource.CAUSAL_CHAIN,
                concept=need.concept,
                relevance=0.0,
            )
        try:
            event_id = need.concept
            chain = self._event_store.get_causal_chain(event_id)
            if chain:
                lines = [
                    f"[{e.chapter_id}] {e.event_type}: {e.description} (压强={e.causal_pressure})"
                    for e in chain
                ]
                content = "\n".join(lines)
            else:
                content = f"事件 {event_id} 无因果前置链"

            return KnowledgeResult(
                content=content,
                source=KnowledgeSource.CAUSAL_CHAIN,
                concept=need.concept,
                relevance=1.0,
            )
        except Exception as e:
            logger.warning("因果链查询失败: %s", e)
            return KnowledgeResult(
                content=f"因果链查询失败: {e}",
                source=KnowledgeSource.CAUSAL_CHAIN,
                concept=need.concept,
                relevance=0.0,
            )
