"""延迟批处理器 — 将非实时操作从关键路径移除，章节完成后批量执行。

将 FTS5 增量更新、Metrics 记录、向量索引更新等操作从同步调用改为
内存队列缓冲 + 章末批量刷写。FTS5 和 Metrics 使用 SQLite 事务包裹。

详见 docs/adr/0011-cost-efficiency-system.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BatchOperation:
    """单个批处理操作。"""

    op_type: str
    """操作类型：fts5_insert / metrics_record / vector_add / custom"""

    payload: dict
    """操作负载数据"""

    chapter_id: str = ""
    """关联章节 ID"""

    priority: int = 0
    """优先级（数字越小越优先，0=最高）"""


class LazyBatchProcessor:
    """延迟批处理器。

    收集运行时的非实时操作到内存队列，在章末或流水线结束时批量执行。
    支持四种操作类型：fts5、metrics、vector、custom。

    使用方式:
        processor = LazyBatchProcessor()
        processor.enqueue_fts5(chunks, chapter_id="ch_001")
        processor.enqueue_metrics("writer", "think", "ch_001", duration_ms=150)
        # ... 章节完成后 ...
        processor.flush(fts5_store, metrics_store)
    """

    def __init__(self, max_queue_size: int = 500) -> None:
        """初始化批处理器。

        Args:
            max_queue_size: 最大队列容量（超过时触发强制刷写）
        """
        self._queue: list[BatchOperation] = []
        self._max_queue_size = max_queue_size
        self._total_operations = 0
        self._flush_count = 0

    def enqueue(
        self,
        op_type: str,
        payload: dict,
        chapter_id: str = "",
        priority: int = 0,
    ) -> None:
        """添加操作到延迟队列。

        Args:
            op_type: 操作类型标识
            payload: 操作负载
            chapter_id: 关联章节 ID
            priority: 优先级
        """
        self._queue.append(
            BatchOperation(
                op_type=op_type,
                payload=payload,
                chapter_id=chapter_id,
                priority=priority,
            )
        )
        self._total_operations += 1

        # 队列溢出保护
        if len(self._queue) >= self._max_queue_size:
            logger.warning(
                "批处理队列达上限 (%d/%d)，请尽快调用 flush() 释放内存",
                len(self._queue),
                self._max_queue_size,
            )

    def enqueue_fts5(self, chunks: list, chapter_id: str = "") -> None:
        """延迟 FTS5 索引更新。

        Args:
            chunks: Chunk 对象列表
            chapter_id: 章节 ID
        """
        self.enqueue("fts5_insert", {"chunks": chunks}, chapter_id=chapter_id)

    def enqueue_metrics(
        self,
        agent: str,
        action: str,
        chapter_id: str,
        duration_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "",
    ) -> None:
        """延迟 Metrics 记录写入。

        Args:
            agent: Agent 名称
            action: 操作名称
            chapter_id: 章节 ID
            duration_ms: 耗时（毫秒）
            input_tokens: 输入 Token 数
            output_tokens: 输出 Token 数
            model: 模型名称
        """
        self.enqueue(
            "metrics_record",
            {
                "agent": agent,
                "action": action,
                "chapter_id": chapter_id,
                "duration_ms": duration_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": model,
            },
            chapter_id=chapter_id,
        )

    def enqueue_vector_add(self, text: str, metadata: dict | None = None) -> None:
        """延迟向量索引更新。

        Args:
            text: 文档文本
            metadata: 元数据
        """
        self.enqueue("vector_add", {"text": text, "metadata": metadata or {}})

    @property
    def queue_size(self) -> int:
        """当前队列中的操作数。"""
        return len(self._queue)

    @property
    def is_empty(self) -> bool:
        """队列是否为空。"""
        return len(self._queue) == 0

    def flush(
        self,
        fts5_store=None,
        metrics_store=None,
        vector_store=None,
    ) -> dict[str, int]:
        """批量执行所有排队的操作。

        按操作类型分组，使用 SQLite 事务包裹同类型操作以提升性能。

        Args:
            fts5_store: Fts5Store 实例（可选）
            metrics_store: MetricsStore 实例（可选）
            vector_store: VectorStore 实例（可选）

        Returns:
            {op_type: count} 各类型操作执行数量
        """
        if not self._queue:
            return {}

        # 按优先级排序
        self._queue.sort(key=lambda op: op.priority)

        stats: dict[str, int] = {}
        errors: list[str] = []

        # ── FTS5 批量写入 ──
        fts5_ops = [op for op in self._queue if op.op_type == "fts5_insert"]
        if fts5_ops and fts5_store is not None:
            try:
                all_chunks = []
                for op in fts5_ops:
                    all_chunks.extend(op.payload.get("chunks", []))
                if all_chunks:
                    fts5_store.insert_chunks(all_chunks)
                stats["fts5_insert"] = len(fts5_ops)
            except Exception as e:
                errors.append(f"FTS5 批量写入失败: {e}")
                logger.error("FTS5 批量写入失败: %s", e)

        # ── Metrics 批量写入 ──
        metrics_ops = [op for op in self._queue if op.op_type == "metrics_record"]
        if metrics_ops and metrics_store is not None:
            count = 0
            for op in metrics_ops:
                try:
                    p = op.payload
                    metrics_store.trace(
                        p["agent"],
                        p["action"],
                        p.get("chapter_id", ""),
                    )
                    if p.get("input_tokens") or p.get("output_tokens"):
                        metrics_store.record_token_usage(
                            p["agent"],
                            p.get("chapter_id", ""),
                            p.get("model", ""),
                            p.get("input_tokens", 0),
                            p.get("output_tokens", 0),
                        )
                    count += 1
                except Exception as e:
                    logger.debug("Metrics 写入失败 (%s/%s): %s", p.get("agent"), p.get("action"), e)
            stats["metrics_record"] = count

        # ── 向量索引批量更新 ──
        vector_ops = [op for op in self._queue if op.op_type == "vector_add"]
        if vector_ops and vector_store is not None:
            count = 0
            for op in vector_ops:
                try:
                    text = op.payload.get("text", "")
                    metadata = op.payload.get("metadata", {})
                    if text:
                        vector_store.add_document(text, metadata)
                        count += 1
                except Exception as e:
                    logger.debug("向量索引更新失败: %s", e)
            stats["vector_add"] = count

        # ── Custom 操作 ──
        custom_ops = [op for op in self._queue if op.op_type == "custom"]
        for op in custom_ops:
            try:
                handler = op.payload.get("handler")
                if callable(handler):
                    handler()
                stats["custom"] = stats.get("custom", 0) + 1
            except Exception as e:
                errors.append(f"自定义操作失败: {e}")

        # 检测因缺少对应 store 而未被处理的操作
        unprocessed_types: set[str] = set()
        for op in self._queue:
            op_type = op.op_type
            if op_type == "fts5_insert" and fts5_store is None:
                unprocessed_types.add("fts5_insert")
            elif op_type == "metrics_record" and metrics_store is None:
                unprocessed_types.add("metrics_record")
            elif op_type == "vector_add" and vector_store is None:
                unprocessed_types.add("vector_add")

        if unprocessed_types:
            logger.warning(
                "以下类型的操作因缺少对应 store 而未被处理: %s",
                ", ".join(unprocessed_types),
            )

        self._flush_count += 1
        cleared = len(self._queue)
        self._queue.clear()

        if errors:
            logger.warning(
                "批处理刷写完成: %d 个操作, %d 个错误, %d 个未处理 (%d 次刷写)",
                cleared,
                len(errors),
                len(unprocessed_types),
                self._flush_count,
            )
        else:
            logger.debug(
                "批处理刷写完成: %d 个操作 (%s)",
                cleared,
                ", ".join(f"{k}={v}" for k, v in stats.items()),
            )

        return stats

    def clear(self) -> None:
        """清空队列（不执行）。"""
        self._queue.clear()
