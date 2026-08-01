"""Director Agent - 创作总监代理。

从全局视角分析已完成章节的叙事状态，
输出策略指导用于调整后续章节的创作方向。
集成伏笔检测（foreshadowing）和规划笔记（planner_notes）自动落盘。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from opennovel.core.context_assembler import (
    ContextStrategy,
    assemble_context,
    detect_strategy,
    get_model_window,
)
from opennovel.core.llm import LLMBus
from opennovel.schemas.director import DirectorAnalysis
from opennovel.schemas.foreshadowing import ForeshadowItem
from opennovel.storage.foreshadowing import ForeshadowStore
from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class Director:
    """Director Agent — 创作总监。

    使用方式:
        director = Director(llm_bus=bus, project_root=root, event_store=es)
        analysis = director.analyze(results, "下一章大纲提示")
        print(analysis.strategic_guidance)

    新增功能:
        - 伏笔检测: 分析时自动检测新伏笔并更新已有伏笔状态
        - 规划笔记: 分析结果自动追加到 planner_notes.md
    """

    def __init__(
        self,
        llm_bus: LLMBus,
        project_root: Path,
        event_store: EventStore | None = None,
        prompt_path: Path | None = None,
    ) -> None:
        self.llm_bus = llm_bus
        self.project_root = project_root
        self.event_store = event_store
        self.prompt_path = prompt_path or (
            Path(__file__).parent.parent / "prompts" / "director.v1.md"
        )
        self._foreshadow_store: ForeshadowStore | None = None
        self._planner_notes_path = project_root / "planner_notes.md"

    def _get_foreshadow_store(self) -> ForeshadowStore:
        """延迟获取 ForeshadowStore 实例。"""
        if self._foreshadow_store is None:
            from opennovel.storage.foreshadowing import ForeshadowStore

            self._foreshadow_store = ForeshadowStore(self.project_root)
        return self._foreshadow_store

    def _append_to_planner_notes(self, analysis: DirectorAnalysis) -> None:
        """将 Director 分析结果追加到 planner_notes.md。

        Args:
            analysis: Director 分析结果
        """
        if not analysis.strategic_guidance and not analysis.warnings:
            return

        lines = []
        lines.append(
            f"\n---\n## 分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        )
        lines.append(f"- **节奏评估**: {analysis.pacing_assessment}")
        lines.append(f"- **张力曲线**: {analysis.tension_curve}")
        if analysis.character_arc_status:
            lines.append("- **角色弧线**:")
            for cid, status in analysis.character_arc_status.items():
                lines.append(f"  - {cid}: {status}")
        lines.append(f"- **策略指导**: {analysis.strategic_guidance}")
        if analysis.creative_direction_adjustment:
            lines.append(f"- **方向调整**: {analysis.creative_direction_adjustment}")
        if analysis.warnings:
            lines.append("- **警告**:")
            for w in analysis.warnings:
                lines.append(f"  - {w}")
        if analysis.foreshadowing_items:
            lines.append(f"- **伏笔更新**: {len(analysis.foreshadowing_items)} 条")
            closed = sum(1 for f in analysis.foreshadowing_items if f.status.value == "closed")
            new_buried = sum(1 for f in analysis.foreshadowing_items if f.status.value == "buried")
            if new_buried:
                lines.append(f"  - 新增埋设: {new_buried} 条")
            if closed:
                lines.append(f"  - 已收束: {closed} 条")
        if analysis.scheduling_proposals:
            lines.append("- **调度提议**:")
            for p in analysis.scheduling_proposals:
                lines.append(f"  - [{p.action}] {p.target_chapter_id}: {p.rationale}")
        lines.append("")

        content = "\n".join(lines)
        self._planner_notes_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._planner_notes_path, "a", encoding="utf-8") as f:
                f.write(content)
            logger.info("planner_notes.md 已更新")
        except Exception as e:
            logger.warning("写入 planner_notes.md 失败: %s", e)

    def _update_foreshadowing(self, analysis: DirectorAnalysis) -> None:
        """将 Director 的伏笔检测结果合并到 foreshadowing.md。

        Args:
            analysis: Director 分析结果（含 foreshadowing_items）
        """
        if not analysis.foreshadowing_items:
            return

        store = self._get_foreshadow_store()
        current_state = store.load()
        merged = store.merge_updates(current_state, analysis.foreshadowing_items)
        store.save(merged)

        buried = sum(1 for i in merged.items if i.status.value == "buried")
        progress = sum(1 for i in merged.items if i.status.value == "in_progress")
        closed = sum(1 for i in merged.items if i.status.value == "closed")
        logger.info(
            "伏笔已更新: 共 %d 条 (已埋设 %d / 推进中 %d / 已收束 %d)",
            len(merged.items),
            buried,
            progress,
            closed,
        )

    def _load_prompt(self) -> str:
        """加载 Director Prompt。"""
        if not self.prompt_path.exists():
            logger.warning("Director Prompt 文件不存在: %s", self.prompt_path)
            return "你是创作总监。分析叙事状态，输出策略指导。"
        return self.prompt_path.read_text(encoding="utf-8")

    def _build_analysis_data(self, results: list) -> str:
        """从已完成章节结果中提取分析数据。"""
        lines = []

        # 评分趋势
        lines.append("### 评分趋势")
        for i, r in enumerate(results):
            score = r.evaluation.total_score
            status = "优秀" if score >= 90 else "合格" if score >= 80 else "待审"
            lines.append(
                f"- 第{i + 1}章 ({r.chapter_id}): {r.evaluation.total_score}分 {status}, "
                f"{r.word_count}字, 重试{r.retry_count}次"
            )

        # 维度详情（最近 3 章）
        lines.append("\n### 维度评分详情（最近章节）")
        for r in results[-3:]:
            dims = r.evaluation.dimensions
            lines.append(
                f"- {r.chapter_id}: 文笔{dims[0].score} 情节{dims[1].score} "
                f"角色{dims[2].score} 节奏{dims[3].score} 情感{dims[4].score}"
            )

        # 章节摘要
        lines.append("\n### 章节摘要")
        for r in results:
            if r.manager_summary:
                lines.append(f"- {r.chapter_id}: {r.manager_summary}")

        # 一致性问题汇总
        all_mismatches = [m for r in results for m in r.mismatches]
        if all_mismatches:
            lines.append(f"\n### 一致性问题 ({len(all_mismatches)} 个)")
            for m in all_mismatches[-5:]:
                lines.append(f"- [{m.severity}] {m.message}")

        return "\n".join(lines)

    def _build_event_data(self) -> str:
        """从 EventStore 提取事件分析数据。"""
        if not self.event_store:
            return "无事件数据"

        lines = []

        # 高压力事件
        high_events = self.event_store.get_high_pressure_events(threshold=0.7)
        if high_events:
            lines.append("### 高因果压力事件 (≥0.7)")
            for e in high_events[-10:]:
                lines.append(
                    f"- [{e.event_type}] {e.description} "
                    f"(pressure={e.causal_pressure}, chapter={e.chapter_id})"
                )
        else:
            lines.append("### 高因果压力事件: 无")

        # 全部事件统计
        all_events = self.event_store.get_all_events()
        lines.append(f"\n### 事件总数: {len(all_events)}")

        # 按章节统计
        chapter_event_counts: dict[str, int] = {}
        for e in all_events:
            chapter_event_counts[e.chapter_id] = chapter_event_counts.get(e.chapter_id, 0) + 1
        if chapter_event_counts:
            lines.append("### 每章事件数")
            for ch, count in sorted(chapter_event_counts.items()):
                lines.append(f"- {ch}: {count} 个事件")

        return "\n".join(lines)

    def _build_foreshadowing_data(self) -> str:
        """从 ForeshadowStore 加载已有伏笔状态，供 Director 分析上下文。

        Returns:
            已有伏笔的 Markdown 描述
        """
        store = self._get_foreshadow_store()
        state = store.load()

        if not state.items:
            return "\n### 已有伏笔\n当前无已有伏笔。"

        lines = ["\n### 已有伏笔"]
        for item in state.items:
            lines.append(
                f"- {item.foreshadow_id} [{item.type.value}] ({item.status.value}): "
                f"{item.description} — 埋设于 {item.buried_chapter}"
            )
        return "\n".join(lines)

    def _build_task_message(
        self,
        results: list,
        upcoming_chapter_hint: str,
        remaining_chapters: list[tuple[str, str]] | None = None,
    ) -> str:
        """构建 Director 分析任务消息。"""
        analysis_data = self._build_analysis_data(results)
        event_data = self._build_event_data()
        foreshadowing_data = self._build_foreshadowing_data()

        # 剩余章节信息（供调度决策参考）
        remaining_section = ""
        if remaining_chapters:
            lines = ["\n### 剩余大纲章节"]
            for cid, hint in remaining_chapters:
                preview = hint[:80].replace("\n", " ")
                lines.append(f"- {cid}: {preview}...")
            remaining_section = "\n".join(lines)

        return f"""## 全局叙事分析任务

请分析已完成章节的叙事状态，为下一章提供策略指导。

### 已完成章节数据
{analysis_data}

### 事件时间线
{event_data}
{foreshadowing_data}

### 下一章大纲
{upcoming_chapter_hint}{remaining_section}

请输出合法的 JSON 对象，包含以下字段：
pacing_assessment、tension_curve、character_arc_status、
strategic_guidance、creative_direction_adjustment、warnings、
scheduling_proposals（可选，当你认为需要调整大纲结构时填充）、
foreshadowing_items（可选，伏笔检测结果列表）。

每个 foreshadowing_item 包含:
- foreshadow_id: 伏笔 ID（新伏笔自动编号 F001/F002...，已有伏笔用原 ID）
- type: "plot" / "character" / "theme" / "world"
- description: 伏笔描述
- buried_chapter: 埋设章节 ID
- status: "buried" / "in_progress" / "closed"
- related_character_ids: 关联角色 ID 列表
- expected_close_chapter: 预计回收章节（可用区间 "ch_008-ch_012"）
- notes: 备注（可选）"""

    def analyze(
        self,
        results: list,
        upcoming_chapter_hint: str,
        remaining_chapters: list[tuple[str, str]] | None = None,
    ) -> DirectorAnalysis:
        """分析已完成章节的全局叙事状态，输出策略指导。

        Args:
            results: 已完成的 ChapterResult 列表
            upcoming_chapter_hint: 下一章的大纲提示
            remaining_chapters: 剩余待创作章节列表，供调度决策参考

        Returns:
            DirectorAnalysis 分析结果（含可选的 scheduling_proposals 和 foreshadowing_items）
        """
        if not results:
            return DirectorAnalysis(
                pacing_assessment="无数据",
                tension_curve="无数据",
                character_arc_status={},
                strategic_guidance="",
            )

        task_message = self._build_task_message(results, upcoming_chapter_hint, remaining_chapters)
        messages = assemble_context(
            project_root=self.project_root,
            task_message=task_message,
            prompt_path=self.prompt_path,
            strategy=detect_strategy(get_model_window(getattr(self.llm_bus, 'model', ''))),
        )

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.llm_bus.chat(messages, temperature=0.3)
                text = response.choices[0].message.content
                if not text:
                    raise ValueError("LLM 返回空文本")
                analysis = self._parse_analysis_from_text(text)
                logger.info(
                    "Director 分析完成: %s, 张力=%s, 伏笔=%d条",
                    analysis.pacing_assessment,
                    analysis.tension_curve,
                    len(analysis.foreshadowing_items),
                )

                # 自动落盘：伏笔更新 + 规划笔记
                try:
                    self._update_foreshadowing(analysis)
                except Exception as e:
                    logger.warning("伏笔更新失败（不影响分析）: %s", e)
                try:
                    self._append_to_planner_notes(analysis)
                except Exception as e:
                    logger.warning("规划笔记写入失败（不影响分析）: %s", e)

                return analysis
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    "Director 分析 JSON 解析失败 (尝试 %d/%d): %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    e,
                )
                if attempt < MAX_RETRIES:
                    messages.append({"role": "assistant", "content": text or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"你的输出 JSON 格式有误: {e}\n请重新输出合法的 JSON 对象。"
                            ),
                        }
                    )

        raise RuntimeError(f"Director 分析失败，已重试 {MAX_RETRIES} 次: {last_error}")

    def _parse_analysis_from_text(self, text: str) -> DirectorAnalysis:
        """从 LLM 输出中解析 DirectorAnalysis JSON。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)

        data = json.loads(cleaned)

        # 处理 foreshadowing_items 中的 ForeshadowItem 对象
        if "foreshadowing_items" in data and data["foreshadowing_items"]:
            data["foreshadowing_items"] = [
                ForeshadowItem(**item) for item in data["foreshadowing_items"]
            ]

        return DirectorAnalysis(**data)
