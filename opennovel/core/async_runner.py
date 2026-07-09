"""
AutoRunner 异步生成器封装。

将 AutoRunner.run_chapter() 的同步管线包装为 AsyncIterator 事件流，
每个阶段 yield 事件供 API 层逐项推送到 WebSocket。

不破坏 AutoRunner 原有的同步接口（CLI 路径继续使用同步 run_chapter()）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from opennovel.core.auto_runner import AutoRunner, ChapterResult
from opennovel.core.config import LoomConfig

logger = logging.getLogger("opennovel.core.async_runner")


# ── 事件类型常量 ──
EVT_STATE_CHANGED = "state_changed"
EVT_STREAM_CHUNK = "stream_chunk"
EVT_EVALUATION = "evaluation"
EVT_CHAPTER_COMPLETE = "chapter_complete"
EVT_LOG = "log"
EVT_TOKEN_USAGE = "token_usage"


async def run_chapter_async(
    project_root: Path,
    config: LoomConfig,
    chapter_id: str,
    chapter_hint: str,
    previous_summary: str = "",
    previous_text: str = "",
    results: list[ChapterResult] | None = None,
    log_callback: Callable[[str, str], None] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    异步生成器封装 AutoRunner.run_chapter()，分阶段 yield 事件。

    每个阶段 yield 的事件格式:
      {"type": "state_changed", "state": "thinking", "phase": "think", "progress": 0}
      {"type": "stream_chunk", "text": "...", "is_delta": True}
      {"type": "evaluation", ...}
      {"type": "chapter_complete", "chapter_id": "ch_001", "word_count": 1234}
      {"type": "token_usage", ...}

    Args:
        project_root: 项目根目录
        config: 项目配置
        chapter_id: 章节 ID
        chapter_hint: 大纲提示
        previous_summary: 前文摘要
        previous_text: 前文正文末尾
        results: 已完成章节的结果列表（用于评分触发）
        log_callback: 日志回调（可选，用于 GUI 端 WS 推送）

    Yields:
        事件字典
    """
    runner = AutoRunner(
        project_root=project_root,
        config=config,
        log_callback=log_callback,
    )
    loop = asyncio.get_event_loop()

    # ── Stage 1: THINKING ──
    yield {"type": EVT_STATE_CHANGED, "state": "thinking", "phase": "think", "progress": 0}

    prev_result = results[-1] if results else None
    should_multi, mode, feedback = runner._should_generate_variations(
        chapter_hint,
        prev_result,
    )

    if should_multi:
        n_variants = 3
        logger.info("结构性变异触发: %s 模式, %d 个方案", mode, n_variants)
        with runner.metrics.trace("writer", "think_variations", chapter_id):
            outlines = await loop.run_in_executor(
                None,
                runner.writer.think_variations,
                chapter_id,
                chapter_hint,
                previous_summary,
                n_variants,
                mode,
                feedback,
                prev_result.evaluation if prev_result else None,
                any(
                    kw in chapter_hint.lower()
                    for kw in ["转折", "高潮", "climax", "决战", "大结局", "finale"]
                ),
            )

        evaluations = []
        for idx, o in enumerate(outlines):
            with runner.metrics.trace("critic", "evaluate_outline", chapter_id):
                eval_result = await loop.run_in_executor(
                    None,
                    runner.critic.evaluate_outline,
                    chapter_id,
                    o,
                    previous_summary,
                )
            evaluations.append(eval_result)
            runner._log(
                f"方案 {idx + 1}: {eval_result.total_score} 分 "
                f"(情节{eval_result.dimensions[0].score} "
                f"角色{eval_result.dimensions[1].score} "
                f"节奏{eval_result.dimensions[2].score})",
                "info",
            )

        best_idx = max(
            range(len(evaluations)),
            key=lambda i: evaluations[i].total_score,
        )
        outline = outlines[best_idx]
        runner._log(
            f"选择方案 {best_idx + 1}/{n_variants} "
            f"({evaluations[best_idx].total_score} 分): {outline.title}",
            "success",
        )
    else:
        with runner.metrics.trace("writer", "think", chapter_id):
            outline = await loop.run_in_executor(
                None,
                runner.writer.think,
                chapter_id,
                chapter_hint,
                previous_summary,
            )

    logger.info("Writer 思考完成: %d 个场景", len(outline.scenes))

    # ── Stage 1.5: 知识缺口检测 ──
    additional_knowledge = ""
    if hasattr(runner, "tool_registry") and hasattr(runner.writer, "detect_knowledge_gaps"):
        needs = runner.writer.detect_knowledge_gaps(outline, previous_text)
        if needs:
            runner._log(f"知识缺口检测: 发现 {len(needs)} 个需要补充的信息", "info")
            filled_results = runner.tool_registry.fulfill(needs, agent="writer")
            filled = [r for r in filled_results if r.content and r.relevance > 0]
            if filled:
                additional_knowledge = runner.writer.format_knowledge_results(filled)
                runner._log(f"主动检索: {len(filled)}/{len(needs)} 个缺口已补充", "success")

    # ── Stage 2: WRITING（异步流式） ──
    yield {"type": EVT_STATE_CHANGED, "state": "writing", "phase": "write", "progress": 0}

    # 构建 task_message（复用 Writer 的构建逻辑）
    task_message = runner.writer._build_write_task_message(
        chapter_id,
        outline,
        previous_text,
    )
    if additional_knowledge:
        task_message += f"\n\n{additional_knowledge}"
    messages = runner.writer._build_context(task_message)

    # 章节类型感知的模型选择
    write_model = runner.writer.write_model
    if chapter_hint and runner.writer.write_model_climax:
        from opennovel.core.chapter_utils import ChapterType, detect_chapter_type

        if detect_chapter_type(chapter_hint) == ChapterType.CLIMAX:
            write_model = runner.writer.write_model_climax

    # 使用 LLMBus 的同步流式方法逐 token 产出
    full_text = ""
    # 通过 asyncio.Queue 桥接同步流式生成器到异步事件流
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _stream_write():
        """在 executor 线程中运行流式写并逐 token 放入 queue。"""
        try:
            stream = runner.writer_bus.chat_stream(
                messages,
                temperature=0.8,
                max_tokens=4000,
                model=write_model,
            )
            for chunk in stream:
                asyncio.run_coroutine_threadsafe(
                    queue.put(("chunk", chunk)),
                    loop,
                )
            asyncio.run_coroutine_threadsafe(queue.put(("done", None)), loop)
        except Exception as e:
            asyncio.run_coroutine_threadsafe(queue.put(("error", e)), loop)

    # 启动线程执行流式写
    loop.run_in_executor(None, _stream_write)

    chunk_count = 0
    while True:
        msg_type, payload = await queue.get()
        if msg_type == "done":
            break
        elif msg_type == "error":
            raise payload  # type: ignore[misc]
        elif msg_type == "chunk":
            full_text += payload
            chunk_count += 1
            yield {"type": EVT_STREAM_CHUNK, "text": payload, "is_delta": True}

    word_count = len(full_text)
    logger.info("Writer 流式创作完成: %s, %d tokens, %d 字", chapter_id, chunk_count, word_count)
    yield {"type": EVT_STATE_CHANGED, "state": "writing", "phase": "write", "progress": 100}

    # ── Stage 3: EVALUATING ──
    yield {"type": EVT_STATE_CHANGED, "state": "evaluating", "phase": "evaluate", "progress": 0}

    evaluation = await loop.run_in_executor(
        None,
        runner.critic.evaluate,
        chapter_id,
        full_text,
        outline,
    )

    d = evaluation.dimensions
    yield {
        "type": EVT_EVALUATION,
        "scores": {
            "plot": d[0].score if len(d) > 0 else 0,
            "character": d[1].score if len(d) > 1 else 0,
            "logic": d[2].score if len(d) > 2 else 0,
            "rhythm": d[3].score if len(d) > 3 else 0,
            "emotion": d[4].score if len(d) > 4 else 0,
        },
        "total": evaluation.total_score,
        "issues": [
            {
                "severity": i.severity.value if hasattr(i.severity, "value") else str(i.severity),
                "dimension": i.dimension,
                "quote": i.quote,
                "description": i.description if hasattr(i, "description") else i.problem,
                "suggestion": i.suggestion,
                "location_hint": i.location_hint,
            }
            for i in evaluation.anchored_issues
        ],
    }

    # 记录评审历史
    runner.metrics.record_evaluation(
        chapter_id=chapter_id,
        total_score=evaluation.total_score,
        dimensions=[
            d[0].score if len(d) > 0 else 0,
            d[1].score if len(d) > 1 else 0,
            d[2].score if len(d) > 2 else 0,
            d[3].score if len(d) > 3 else 0,
            d[4].score if len(d) > 4 else 0,
        ],
        is_pass=evaluation.is_pass,
        retry_count=0,
    )

    yield {"type": EVT_STATE_CHANGED, "state": "writing", "phase": "evaluate", "progress": 100}

    # ── Stage 4: 结果 ──
    yield {
        "type": EVT_CHAPTER_COMPLETE,
        "chapter_id": chapter_id,
        "word_count": word_count,
        "total_score": evaluation.total_score,
        "is_pass": evaluation.is_pass,
    }

    # 事件流结束，返回结果以供后续使用
    yield {
        "type": "result",
        "chapter_id": chapter_id,
        "chapter_text": full_text,
        "outline": outline,
        "word_count": word_count,
        "total_score": evaluation.total_score,
    }


async def run_auto_async_iterator(
    project_root: Path,
    config: LoomConfig,
    outline_text: str,
    log_callback: Callable[[str, str], None] | None = None,
    max_chapters: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    异步逐章创作循环生成器。

    每次迭代 yield 一个章节的事件流，API 层负责将事件推送到 WS。
    通过 cancel_check 回调检查取消信号。

    Args:
        project_root: 项目根目录
        config: 项目配置
        outline_text: 大纲文本
        log_callback: 日志回调
        max_chapters: 最大章节数（可选，默认使用 config.target_chapters）
        cancel_check: 取消检查回调，返回 True 表示应取消

    Yields:
        每章的事件流（state_changed, stream_chunk, evaluation, chapter_complete 等）
    """
    runner = AutoRunner(
        project_root=project_root,
        config=config,
        log_callback=log_callback,
    )
    chapters = runner._parse_outline(outline_text)
    limit = max_chapters or config.target_chapters
    if len(chapters) > limit:
        chapters = chapters[:limit]

    results: list[ChapterResult] = []
    total = len(chapters)

    for i, (chapter_id, chapter_hint) in enumerate(chapters):
        # ── 检查取消 ──
        if cancel_check and cancel_check():
            yield {"type": EVT_STATE_CHANGED, "state": "idle", "phase": "cancelled", "progress": 0}
            return

        chapter_progress = int((i + 1) / total * 100)

        previous_summary = results[-1].manager_summary if results else ""
        previous_text = results[-1].chapter_text[-2000:] if results else ""

        # 逐章生成事件
        async for event in run_chapter_async(
            project_root=project_root,
            config=config,
            chapter_id=chapter_id,
            chapter_hint=chapter_hint,
            previous_summary=previous_summary,
            previous_text=previous_text,
            results=results if results else None,
            log_callback=log_callback,
        ):
            # 捕获结果事件
            if event.get("type") == "result":
                result = ChapterResult(
                    chapter_id=event["chapter_id"],
                    chapter_text=event["chapter_text"],
                    outline=event["outline"],
                    word_count=event["word_count"],
                )
                results.append(result)
                continue  # result 事件不转发给前端

            # 添加总进度到 state_changed
            if event.get("type") == EVT_STATE_CHANGED:
                event["auto_progress"] = chapter_progress

            yield event
