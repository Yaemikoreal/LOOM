"""AgentWorker — 后台 Agent 执行器（QObject + QThread 模式）。

所有 LLM 调用（Writer 创作、Critic 评分、Director 分析）
在独立线程中执行，通过 Qt Signal 回传结果到主线程。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class AgentWorker(QObject):
    """后台 Agent 执行器。运行在独立 QThread 中。

    使用方式：
        worker = AgentWorker(project_root)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.do_write)
        worker.finished.connect(thread.quit)
        thread.start()
    """

    # ── Signals ──────────────────────────────────────────────
    stream_chunk = Signal(str)  # 流式输出文本片段
    phase_changed = Signal(str)  # think / write / evaluate / update
    status_message = Signal(str)  # 实时 Agent 状态描述（如 "正在创作第 3 段... 1,245 字"）
    finished = Signal(dict)  # 完成结果 {chapter_id, score, ...}
    error_occurred = Signal(str)  # 异常信息
    progress = Signal(int, int)  # 当前进度 (current, total)

    def __init__(self, project_root: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._stopped = False
        self._char_count = 0
        self._timeout_timer: QTimer | None = None

    # ── 写章节 ────────────────────────────────────────────

    @Slot()
    def do_write(self, chapter_id: str, chapter_hint: str = "") -> None:
        """执行单章创作流水线。"""
        self._stopped = False
        self._start_timeout(120_000)
        try:
            result = self._execute_pipeline(chapter_id, chapter_hint)
            self.finished.emit(result)
        except Exception as e:
            self.error_occurred.emit(f"Writer 流水线失败: {e!s}")
        finally:
            self._stop_timeout()

    def _execute_pipeline(self, chapter_id: str, chapter_hint: str = "") -> dict:
        """内部执行 think→write→evaluate，不发射 finished。"""
        self._char_count = 0

        self.status_message.emit("正在分析章节上下文，规划大纲...")
        self.phase_changed.emit("think")
        outline = self._call_writer_think(chapter_id, chapter_hint)
        if self._stopped:
            return {"chapter_id": chapter_id, "score": 0}
        scene_count = len(outline.get("scenes", [])) if isinstance(outline, dict) else 0
        self.status_message.emit(f"大纲完成: {scene_count} 个场景")

        self.status_message.emit("正在创作正文...")
        self.phase_changed.emit("write")
        chapter_text = self._call_writer_write(chapter_id, outline, self._on_stream_chunk)
        if self._stopped:
            return {"chapter_id": chapter_id, "score": 0}
        self.status_message.emit(f"正文创作完成，共 {len(chapter_text)} 字")

        self.status_message.emit("正在评估章节质量...")
        self.phase_changed.emit("evaluate")
        evaluation = self._call_critic_evaluate(chapter_id, chapter_text)
        score = evaluation.get("score", 0) if isinstance(evaluation, dict) else 0
        self.status_message.emit(f"评估完成: 评分 {score}/100")
        return {
            "chapter_id": chapter_id,
            "word_count": len(chapter_text),
            "score": score,
            "evaluation": evaluation,
        }

    # ── Auto 创作 ──────────────────────────────────────────

    @Slot()
    def do_auto(self, chapter_ids: list[str]) -> None:
        """全自动创作，逐章执行，全部完成后统一发射 finished。"""
        self._stopped = False
        self._start_timeout(300_000)  # 5 分钟超时（保护章与章之间的间隔）
        results: list[dict] = []

        try:
            for i, cid in enumerate(chapter_ids):
                if self._stopped:
                    break
                self.progress.emit(i + 1, len(chapter_ids))
                result = self._execute_pipeline(cid)
                results.append(result)
                if self._stopped:
                    break
            self.finished.emit({"chapters": results})
        except Exception as e:
            self.error_occurred.emit(f"Auto 流水线失败: {e!s}")
        finally:
            self._stop_timeout()

    # ── 停止 ──────────────────────────────────────────────

    @Slot()
    def stop(self) -> None:
        """请求优雅停止。Worker 会在当前阶段完成后退出。"""
        self._stopped = True

    # ── 内部调用（lazy import opennovel core）────────────

    def _call_writer_think(self, chapter_id: str, hint: str) -> dict:
        """调用 Writer.think() 生成大纲。"""
        from opennovel.agents.writer import Writer  # noqa: PLC0415
        from opennovel.core.config import LoomConfig  # noqa: PLC0415
        from opennovel.core.llm import LLMBus  # noqa: PLC0415
        from opennovel.core.retriever import Retriever  # noqa: PLC0415

        project_root = Path(self._project_root)
        config = LoomConfig.load(project_root)
        writer_cfg = config.get_agent_llm_config("writer")
        bus = LLMBus(
            model=writer_cfg.get("model") or config.model,
            api_base=config.api_base,
            api_key=config.api_key,
            agent_name="writer",
        )
        retriever = Retriever(project_root)
        writer = Writer(llm_bus=bus, retriever=retriever, project_root=project_root)
        outline = writer.think(chapter_id=chapter_id, chapter_hint=hint)
        return outline.model_dump() if hasattr(outline, "model_dump") else {"title": ""}

    def _call_writer_write(self, chapter_id: str, outline: dict, stream_cb) -> str:
        """调用 Writer.write() 创作正文，流式回调。"""
        from opennovel.agents.writer import Writer  # noqa: PLC0415
        from opennovel.core.config import LoomConfig  # noqa: PLC0415
        from opennovel.core.llm import LLMBus  # noqa: PLC0415
        from opennovel.core.retriever import Retriever  # noqa: PLC0415

        project_root = Path(self._project_root)
        config = LoomConfig.load(project_root)
        writer_cfg = config.get_agent_llm_config("writer")
        bus = LLMBus(
            model=writer_cfg.get("model") or config.model,
            api_base=config.api_base,
            api_key=config.api_key,
            agent_name="writer",
        )
        retriever = Retriever(project_root)
        writer = Writer(llm_bus=bus, retriever=retriever, project_root=project_root)
        result = writer.write(
            chapter_id=chapter_id,
            outline=outline,
            stream_callback=stream_cb,
        )
        return result if isinstance(result, str) else ""

    def _call_critic_evaluate(self, chapter_id: str, text: str) -> dict:
        """调用 Critic.evaluate() 评分。"""
        from opennovel.agents.critic import Critic  # noqa: PLC0415
        from opennovel.core.config import LoomConfig  # noqa: PLC0415
        from opennovel.core.llm import LLMBus  # noqa: PLC0415
        from opennovel.core.retriever import Retriever  # noqa: PLC0415

        project_root = Path(self._project_root)
        config = LoomConfig.load(project_root)
        critic_cfg = config.get_agent_llm_config("critic")
        bus = LLMBus(
            model=critic_cfg.get("model") or config.model,
            api_base=config.api_base,
            api_key=config.api_key,
            agent_name="critic",
        )
        retriever = Retriever(project_root)
        critic = Critic(llm_bus=bus, retriever=retriever, project_root=project_root)
        evaluation = critic.evaluate(chapter_id=chapter_id, chapter_text=text)
        return evaluation.model_dump() if hasattr(evaluation, "model_dump") else {}

    def _on_stream_chunk(self, chunk: str) -> None:
        """流式输出回调 → 转发为主线程 Signal。"""
        if not self._stopped:
            self._char_count += len(chunk)
            self.stream_chunk.emit(chunk)
            # 每 64 字符更新一次状态（避免信号过载）
            if self._char_count % 64 < len(chunk):
                self.status_message.emit(f"正在创作... {self._char_count:,} 字")

    # ── 超时熔断 ──────────────────────────────────────────

    def _start_timeout(self, ms: int) -> None:
        """启动超时计时器，超时则标记停止。"""
        if self._timeout_timer is None:
            self._timeout_timer = QTimer()
            self._timeout_timer.setSingleShot(True)
            self._timeout_timer.timeout.connect(self._on_timeout)
        self._timeout_timer.start(ms)

    def _stop_timeout(self) -> None:
        """停止超时计时器。"""
        if self._timeout_timer and self._timeout_timer.isActive():
            self._timeout_timer.stop()

    def _on_timeout(self) -> None:
        """超时回调。"""
        self._stopped = True
        self.error_occurred.emit("操作超时（120s），已自动停止")
