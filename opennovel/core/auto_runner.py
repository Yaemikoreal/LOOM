"""三 Agent 自主创作编排器。

协调 Writer → Critic → Manager 的创作循环，
管理重试逻辑、快照、日志和进度输出。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from opennovel.agents.critic import Critic
from opennovel.agents.manager import Manager
from opennovel.agents.writer import Writer
from opennovel.core.chapter_utils import ChapterType, detect_chapter_type
from opennovel.core.config import LoomConfig
from opennovel.core.diff_checker import DiffChecker, Mismatch
from opennovel.core.hybrid_retriever import HybridRetriever
from opennovel.core.llm import LLMBus
from opennovel.core.retriever import Retriever
from opennovel.core.safety_fence import SafetyFence
from opennovel.core.state_manager import StateManager
from opennovel.core.tool_registry import ToolRegistry
from opennovel.schemas.director import SchedulingAction, SchedulingProposal
from opennovel.schemas.evaluation import ChapterEvaluation
from opennovel.schemas.outline import ChapterOutline
from opennovel.storage.metrics import MetricsStore
from opennovel.storage.summaries import write_summary
from opennovel.storage.timeline import write_timeline
from opennovel.storage.yaml_storage import YAMLStorage

logger = logging.getLogger(__name__)

MAX_CHAPTER_RETRIES = 5
MANAGER_BATCH_THRESHOLD = 90  # 评分 >= 90 时跳过 Manager 实时更新，改为批处理
DIRECTOR_INTERVAL = 3  # 日常章节每 N 章运行一次 Director

console = Console()


def should_skip_manager(evaluation: ChapterEvaluation) -> bool:
    """判断是否跳过 Manager 实时更新。

    评分 >= MANAGER_BATCH_THRESHOLD 且无锚定问题时，
    将 Manager 更新推迟到批处理阶段。

    Args:
        evaluation: Critic 评审结果

    Returns:
        True 表示跳过，延后批处理
    """
    return evaluation.total_score >= MANAGER_BATCH_THRESHOLD and not evaluation.has_anchored_issues


def should_skip_director(
    chapter_type: ChapterType,
    chapter_index: int,
    total_chapters: int,
) -> bool:
    """判断是否跳过 Director 分析。

    高潮章节强制运行，过渡章节跳过，日常章节每 N 章运行一次。

    Args:
        chapter_type: 章节类型
        chapter_index: 当前章节索引（0-based）
        total_chapters: 总章节数

    Returns:
        True 表示跳过 Director
    """
    # 最后一章不运行 Director（没有下一章需要指导）
    if chapter_index >= total_chapters - 1:
        return True

    # 高潮章节：强制运行
    if chapter_type == ChapterType.CLIMAX:
        return False

    # 过渡章节：跳过
    if chapter_type == ChapterType.TRANSITION:
        return True

    # 日常章节：每 N 章运行一次（第 0 章、第 N 章...）
    return chapter_index % DIRECTOR_INTERVAL != 0


def parse_outline_from_text(outline_text: str) -> list[tuple[str, str]]:
    """解析大纲文本，返回 [(chapter_id, chapter_hint), ...] 列表。

    不依赖 AutoRunner 实例，供 CLI dry-run 模式直接使用。

    Args:
        outline_text: Markdown 格式的大纲文本

    Returns:
        [(chapter_id, chapter_hint), ...]
    """
    chapters = []
    current_title = None
    current_lines = []

    for line in outline_text.split("\n"):
        if line.startswith("## "):
            if current_title is not None:
                chapters.append((current_title, "\n".join(current_lines).strip()))
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title is not None:
        chapters.append((current_title, "\n".join(current_lines).strip()))

    result: list[tuple[str, str]] = []
    for i, (title, hint) in enumerate(chapters, 1):
        chapter_id = f"ch_{i:03d}"
        result.append((chapter_id, f"{title}\n{hint}"))

    return result


class _OutlineWithKnowledge:
    """将补充知识注入 outline 的临时包装类。

    Args:
        base: 原始 ChapterOutline
        extra: 补充知识文本
    """

    def __init__(self, base: ChapterOutline, extra: str) -> None:
        self.chapter_id = base.chapter_id
        self.title = base.title
        self.summary = f"{base.summary}\n\n{extra}"
        self.scenes = base.scenes
        self.character_arcs = base.character_arcs
        self.key_plot_points = base.key_plot_points
        self.narrative_rhythm = base.narrative_rhythm
        self.target_words = base.target_words


@dataclass
class DeferredManagerData:
    """延后的 Manager 更新数据，用于批处理。"""

    chapter_id: str
    chapter_text: str
    active_characters: list[str]
    chapter_type: ChapterType


@dataclass
class ChapterResult:
    """单章创作结果。"""

    chapter_id: str
    outline: ChapterOutline
    chapter_text: str
    evaluation: ChapterEvaluation
    retry_count: int
    manager_summary: str = ""
    word_count: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    manager_skipped: bool = False  # True 表示本次 Manager 更新被延后批处理


@dataclass
class RunReport:
    """完整创作运行报告。"""

    chapters: list[ChapterResult] = field(default_factory=list)
    total_chapters: int = 0
    successful_chapters: int = 0
    failed_chapters: int = 0
    start_time: str = ""
    end_time: str = ""
    log_lines: list[str] = field(default_factory=list)
    all_mismatches: list[Mismatch] = field(default_factory=list)


def _proposal_sort_key(
    proposal: SchedulingProposal,
    chapters: list[tuple[str, str]],
) -> int:
    """生成提议的排序键，用于从后往前应用。

    Args:
        proposal: 调度提议
        chapters: 当前章节列表

    Returns:
        目标章节在列表中的索引，找不到时返回 -1
    """
    for i, (cid, _) in enumerate(chapters):
        if cid == proposal.target_chapter_id:
            return i
    return -1


def _proposal_affects_future(
    proposal: SchedulingProposal,
    chapters: list[tuple[str, str]],
    current_index: int,
) -> bool:
    """判断提议是否影响未完成的章节。

    Args:
        proposal: 调度提议
        chapters: 当前章节列表
        current_index: 当前已完成的章节索引

    Returns:
        True 表示提议作用于未完成的章节
    """
    return _proposal_sort_key(proposal, chapters) > current_index


def _generate_new_chapter_id(existing_ids: set[str]) -> str:
    """生成新的不重复章节 ID。

    Args:
        existing_ids: 现有章节 ID 集合

    Returns:
        新的章节 ID，如 ch_008
    """
    max_num = 0
    for cid in existing_ids:
        if cid.startswith("ch_"):
            try:
                num = int(cid[3:])
                max_num = max(max_num, num)
            except ValueError:
                continue
    return f"ch_{max_num + 1:03d}"


class AutoRunner:
    """三 Agent 自主创作编排器。

    使用方式:
        runner = AutoRunner(project_root=Path("."), config=config)
        report = runner.run(outline_text)
    """

    def __init__(self, project_root: Path, config: LoomConfig) -> None:
        self.project_root = project_root
        self.config = config
        self.storage = YAMLStorage()
        self.log_lines: list[str] = []

        # 指标数据库（Phase 2.2）
        metrics_path = project_root / ".novel.metrics.db"
        self.metrics = MetricsStore(metrics_path)

        # 安全围栏（ADR 0006 — Agent 自治约束边界）
        self.safety_fence = SafetyFence(config.safety_fence)
        if not config.safety_fence.enabled:
            logger.info("安全围栏已禁用")

        # Prompt 日志目录
        prompt_log_dir = project_root / "debug" / "prompts"

        # 初始化三个 Agent 的 LLMBus（注入 MetricsStore + Prompt 日志）
        writer_cfg = config.get_agent_llm_config("writer")
        critic_cfg = config.get_agent_llm_config("critic")
        manager_cfg = config.get_agent_llm_config("manager")

        self.writer_bus = LLMBus(
            model=writer_cfg["model"] or config.model,
            api_base=writer_cfg["api_base"] or config.api_base,
            api_key=writer_cfg["api_key"] or config.api_key,
            metrics_store=self.metrics,
            agent_name="writer",
            prompt_log_dir=prompt_log_dir,
        )
        self.critic_bus = LLMBus(
            model=critic_cfg["model"] or config.model,
            api_base=critic_cfg["api_base"] or config.api_base,
            api_key=critic_cfg["api_key"] or config.api_key,
            metrics_store=self.metrics,
            agent_name="critic",
            prompt_log_dir=prompt_log_dir,
        )
        self.manager_bus = LLMBus(
            model=manager_cfg["model"] or config.model,
            api_base=manager_cfg["api_base"] or config.api_base,
            api_key=manager_cfg["api_key"] or config.api_key,
            metrics_store=self.metrics,
            agent_name="manager",
            prompt_log_dir=prompt_log_dir,
        )

        # 初始化组件
        retriever = Retriever(project_root)
        self._build_or_load_indexes(retriever)
        self.state_manager = StateManager(project_root)
        self.diff_checker = DiffChecker(project_root, self.storage)

        # EventStore（懒加载，数据库可能尚不存在）
        from opennovel.storage.sqlite import EventStore

        db_path = project_root / ".novel.db"
        event_store = EventStore(db_path) if db_path.exists() else None

        # 混合检索路由器（Phase 2.3）
        hybrid = HybridRetriever(
            project_root=project_root,
            event_store=event_store,
            retriever=retriever,
        )

        # 工具注册中心（Agent 自治 — 知识缺口检索，需在 Writer 之前创建）
        self.tool_registry = ToolRegistry(
            project_root=project_root,
            retriever=retriever,
            event_store=event_store,
            storage=self.storage,
        )

        self.writer = Writer(
            llm_bus=self.writer_bus,
            retriever=retriever,
            project_root=project_root,
            creative_direction=config.creative_direction,
            words_per_chapter=config.words_per_chapter,
            event_store=event_store,
            hybrid_retriever=hybrid,
            think_model=config.agent_writer.think_model,
            write_model=config.agent_writer.write_model,
            write_model_climax=config.agent_writer.write_model_climax,
            revise_model=config.agent_writer.revise_model,
            tool_registry=self.tool_registry,
            safety_fence=self.safety_fence,
        )
        self.critic = Critic(
            llm_bus=self.critic_bus,
            project_root=project_root,
            retriever=retriever,
            event_store=event_store,
            hybrid_retriever=hybrid,
        )
        self.manager = Manager(
            llm_bus=self.manager_bus,
            state_manager=self.state_manager,
            project_root=project_root,
        )

        # Director Agent（可选）
        self.director = None
        if config.director_enabled:
            from opennovel.agents.director import Director

            director_cfg = config.get_agent_llm_config("director")
            director_bus = LLMBus(
                model=director_cfg["model"] or config.model,
                api_base=director_cfg["api_base"] or config.api_base,
                api_key=director_cfg["api_key"] or config.api_key,
                metrics_store=self.metrics,
                agent_name="director",
                prompt_log_dir=prompt_log_dir,
            )
            self.director = Director(
                llm_bus=director_bus,
                project_root=project_root,
                event_store=event_store,
            )

        # 条件管线：延后批处理的 Manager 更新队列
        self._deferred_manager_updates: list[DeferredManagerData] = []

    def _build_or_load_indexes(self, retriever: Retriever) -> None:
        """构建或加载向量索引（canon + subconscious）。

        如果索引已存在则加载，否则从源文件构建。
        确保 Writer/Critic 能获得世界观和潜意识上下文。
        """
        index_dir = retriever._index_dir
        canon_index = index_dir / "canon"
        sub_index = index_dir / "subconscious"

        # Canon 索引
        if canon_index.exists() and any(canon_index.iterdir()):
            self._log("加载 canon 向量索引...", "info")
            retriever._canon_store.load_index()
        else:
            canon_dir = self.project_root / "canon"
            if canon_dir.exists() and any(canon_dir.glob("*.md")):
                self._log("构建 canon 向量索引...", "info")
                retriever.build_canon_index()
            else:
                self._log("canon 目录不存在或为空，跳过索引构建", "info")

        # Subconscious 索引
        if sub_index.exists() and any(sub_index.iterdir()):
            self._log("加载 subconscious 向量索引...", "info")
            retriever._subconscious_store.load_index()
        else:
            sub_dir = self.project_root / "subconscious"
            if sub_dir.exists() and any(sub_dir.glob("*.md")):
                self._log("构建 subconscious 向量索引...", "info")
                retriever.build_subconscious_index()
            else:
                self._log("subconscious 目录不存在或为空，跳过索引构建", "info")

    def _log(self, message: str, level: str = "info") -> None:
        """输出日志到终端和日志列表。"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        self.log_lines.append(log_line)

        style_map = {"info": "dim", "success": "green", "warning": "yellow", "error": "red"}
        style = style_map.get(level, "dim")
        console.print(f"  [{style}]{log_line}[/{style}]")

    def _check_safety(self, agent: str, additional_tokens: int = 0) -> bool:
        """执行安全围栏检查，失败时记录日志。

        封装 SafetyFence.check_all()，添加 AutoRunner 级别的日志输出。

        Args:
            agent: Agent 名称
            additional_tokens: 预计消耗的 Token 数

        Returns:
            True 表示通过
        """
        if not self.safety_fence.check_all(agent, additional_tokens):
            violations = self.safety_fence.violations
            if violations:
                last = violations[-1]
                self._log(
                    f"安全围栏: [{last.rule}] {last.detail}",
                    "warning",
                )
            return False
        return True

    def _process_deferred_manager_updates(self, results: list[ChapterResult]) -> None:
        """批处理延后的 Manager 更新。

        对所有因高分跳过实时 Manager 的章节执行批量状态提取，
        并将提取结果回填到对应的 ChapterResult。

        Args:
            results: 已完成的所有章节结果列表
        """
        if not self._deferred_manager_updates:
            return

        console.print(
            f"\n[bold cyan]📦 批处理 Manager 更新[/bold cyan] "
            f"({len(self._deferred_manager_updates)} 章延后)"
        )
        self._log(
            f"开始批处理 {len(self._deferred_manager_updates)} 个延后的 Manager 更新",
            "info",
        )

        for deferred in self._deferred_manager_updates:
            try:
                with self.metrics.trace("manager", "batch_update", deferred.chapter_id):
                    manager_result = self.manager.update(
                        deferred.chapter_id,
                        deferred.chapter_text,
                        deferred.active_characters,
                    )
                    # 回填结果到 ChapterResult
                    for r in results:
                        if r.chapter_id == deferred.chapter_id:
                            r.manager_summary = manager_result.chapter_summary
                            r.manager_skipped = False
                            break
                    self._log(
                        f"  {deferred.chapter_id}: "
                        f"{len(manager_result.character_updates)} 个角色变更, "
                        f"{len(manager_result.events)} 个事件",
                        "success",
                    )
            except Exception as e:
                self._log(f"  {deferred.chapter_id} Manager 批处理失败: {e}", "error")

        # 清空队列
        self._deferred_manager_updates.clear()
        self._log("批处理完成", "success")

    def _apply_scheduling_proposals(
        self,
        chapters: list[tuple[str, str]],
        proposals: list[SchedulingProposal],
        current_index: int,
    ) -> list[tuple[str, str]]:
        """处理 Director 的章节调度提议，修改剩余章节列表。

        支持的调度动作（按优先级）：
        1. SKIP — 跳过无必要的章节
        2. INSERT — 在指定位置前插入补充章节
        3. MERGE — 合并内容稀疏的章节

        Args:
            chapters: 当前完整章节列表（含已完成部分）
            proposals: Director 输出的调度提议列表
            current_index: 当前已完成的章节索引（0-based）

        Returns:
            修改后的章节列表，已完成的章节不受影响
        """
        if not proposals:
            return chapters

        result = list(chapters)
        # 从后往前应用提案，避免索引偏移
        sorted_proposals = sorted(
            proposals, key=lambda p: _proposal_sort_key(p, result), reverse=True
        )
        # 只保留影响剩余章节的提议
        sorted_proposals = [
            p for p in sorted_proposals if _proposal_affects_future(p, result, current_index)
        ]

        if not sorted_proposals:
            return result

        total_before = len(result)
        for proposal in sorted_proposals:
            if proposal.action == SchedulingAction.SKIP:
                result = self._apply_skip_proposal(result, proposal)
            elif proposal.action == SchedulingAction.INSERT:
                result = self._apply_insert_proposal(result, proposal, current_index)
            elif proposal.action == SchedulingAction.MERGE:
                self._log(
                    f"调度提议: 合并 {proposal.target_chapter_id} ↔ {proposal.merge_with} "
                    f"— {proposal.rationale}",
                    "info",
                )
                # MERGE 暂未实现，仅记录日志
            else:
                self._log(f"未知调度动作: {proposal.action}", "warning")

        changes = total_before - len(result)
        if changes:
            self._log(
                f"调度执行: {changes} 处大纲调整",
                "success",
            )

        return result

    def _apply_skip_proposal(
        self,
        chapters: list[tuple[str, str]],
        proposal: SchedulingProposal,
    ) -> list[tuple[str, str]]:
        """执行 SKIP 调度：从章节列表中移除目标章节。

        Args:
            chapters: 当前章节列表
            proposal: 调度提议

        Returns:
            移除目标章节后的列表
        """
        target = proposal.target_chapter_id
        idx = next((i for i, (cid, _) in enumerate(chapters) if cid == target), -1)
        if idx == -1:
            self._log(f"调度跳过失败: 未找到章节 {target}", "warning")
            return chapters

        self._log(
            f"调度执行: 跳过 {target} — {proposal.rationale}",
            "success",
        )
        return chapters[:idx] + chapters[idx + 1 :]

    def _apply_insert_proposal(
        self,
        chapters: list[tuple[str, str]],
        proposal: SchedulingProposal,
        current_index: int,
    ) -> list[tuple[str, str]]:
        """执行 INSERT 调度：在目标章节前插入补充章节。

        Args:
            chapters: 当前章节列表
            proposal: 调度提议
            current_index: 当前已完成章节索引

        Returns:
            插入补充章节后的列表
        """
        if not proposal.new_chapter_hint:
            self._log("调度插入失败: 补充章节大纲为空", "warning")
            return chapters

        target = proposal.target_chapter_id
        idx = next((i for i, (cid, _) in enumerate(chapters) if cid == target), -1)
        if idx == -1:
            self._log(f"调度插入失败: 未找到目标章节 {target}", "warning")
            return chapters

        # 生成新章节 ID
        existing_ids = {cid for cid, _ in chapters}
        new_id = _generate_new_chapter_id(existing_ids)

        self._log(
            f"调度执行: 在 {target} 前插入 {new_id} — {proposal.rationale}",
            "success",
        )
        return chapters[:idx] + [(new_id, proposal.new_chapter_hint)] + chapters[idx:]

    def _parse_outline(self, outline_text: str) -> list[tuple[str, str]]:
        """解析大纲文本，返回 [(chapter_id, chapter_hint), ...] 列表。"""
        return parse_outline_from_text(outline_text)

    def _get_previous_summary(self, chapter_index: int, results: list[ChapterResult]) -> str:
        """获取前一章的摘要。"""
        if chapter_index <= 0 or not results:
            return ""
        return results[-1].manager_summary

    def _get_previous_chapter_text(self, chapter_index: int, results: list[ChapterResult]) -> str:
        """获取前一章的正文末尾。"""
        if chapter_index <= 0 or not results:
            return ""
        text = results[-1].chapter_text
        return text[-2000:] if len(text) > 2000 else text

    def _get_active_characters(self) -> list[str]:
        """获取所有角色 ID。"""
        chars_dir = self.project_root / "characters"
        if not chars_dir.exists():
            return []
        return sorted([f.stem for f in chars_dir.glob("char_*.md")])

    def _should_generate_variations(
        self,
        chapter_hint: str,
        previous_result: ChapterResult | None,
    ) -> tuple[bool, str, str]:
        """判断是否需要多方案生成。

        Returns:
            (是否需要, 模式 "exploratory"/"corrective", 纠错反馈)
        """
        # 用户强制
        if "<!-- multi -->" in chapter_hint:
            return True, "exploratory", ""

        # 纠错型：前章评分 < 80
        if previous_result and previous_result.evaluation.total_score < 80:
            issues = previous_result.evaluation.issues
            feedback = "\n".join(f"- {i}" for i in issues) if issues else "评分偏低，请调整方向"
            return True, "corrective", feedback

        # 探索型：仅高潮关键词触发（短 hint 不自动触发）
        climax_keywords = ["转折", "高潮", "climax", "决战", "大结局", "finale"]
        hint_lower = chapter_hint.lower()
        is_climax = any(kw in hint_lower for kw in climax_keywords)

        if is_climax:
            return True, "exploratory", ""

        return False, "", ""

    def run_chapter(
        self,
        chapter_id: str,
        chapter_hint: str,
        previous_summary: str = "",
        previous_text: str = "",
        results: list[ChapterResult] | None = None,
    ) -> ChapterResult:
        """执行单章创作循环: think → write → evaluate → (revise if needed) → update。

        Args:
            chapter_id: 章节 ID
            chapter_hint: 大纲中本章的描述
            previous_summary: 前一章摘要
            previous_text: 前一章正文末尾

        Returns:
            ChapterResult
        """
        # Step 1: Writer 思考（含盲目变异）
        console.print(f"\n[bold]📝 Writer 思考规划[/bold] {chapter_id}")

        # 获取前一章结果用于触发判断
        prev_result = results[-1] if results else None
        should_multi, mode, feedback = self._should_generate_variations(
            chapter_hint,
            prev_result,
        )

        if should_multi:
            # 多方案生成 → Critic 预审 → 选择最佳
            n_variants = 3
            self._log(
                f"结构性变异触发: {mode} 模式, {n_variants} 个方案",
                "info",
            )
            with self.metrics.trace("writer", "think_variations", chapter_id):
                outlines = self.writer.think_variations(
                    chapter_id,
                    chapter_hint,
                    previous_summary,
                    n_variants=n_variants,
                    variation_mode=mode,
                    corrective_feedback=feedback,
                    previous_evaluation=prev_result.evaluation if prev_result else None,
                    is_climax=any(
                        kw in chapter_hint.lower()
                        for kw in ["转折", "高潮", "climax", "决战", "大结局", "finale"]
                    ),
                )

            # Critic 预审每个方案
            evaluations = []
            for idx, o in enumerate(outlines):
                with self.metrics.trace("critic", "evaluate_outline", chapter_id):
                    eval_result = self.critic.evaluate_outline(
                        chapter_id,
                        o,
                        previous_summary,
                    )
                evaluations.append(eval_result)
                self._log(
                    f"方案 {idx + 1}: {eval_result.total_score} 分 "
                    f"(情节{eval_result.dimensions[0].score} "
                    f"角色{eval_result.dimensions[1].score} "
                    f"节奏{eval_result.dimensions[2].score})",
                    "info",
                )

            # 选择最佳方案
            best_idx = max(
                range(len(evaluations)),
                key=lambda i: evaluations[i].total_score,
            )
            outline = outlines[best_idx]
            self._log(
                f"选择方案 {best_idx + 1}/{n_variants} "
                f"({evaluations[best_idx].total_score} 分): {outline.title}",
                "success",
            )
        else:
            with self.metrics.trace("writer", "think", chapter_id):
                outline = self.writer.think(chapter_id, chapter_hint, previous_summary)

        self._log(
            f"Writer 思考完成: {len(outline.scenes)} 个场景, 目标 {outline.target_words} 字",
            "success",
        )

        # Step 1.5: 知识缺口检测（Agent 自治 — 主动检索）
        additional_knowledge = ""
        if hasattr(self, "tool_registry") and hasattr(self.writer, "detect_knowledge_gaps"):
            needs = self.writer.detect_knowledge_gaps(outline, previous_text)
            if needs:
                self._log(
                    f"知识缺口检测: 发现 {len(needs)} 个需要补充的信息",
                    "info",
                )
                results = self.tool_registry.fulfill(needs)
                filled = [r for r in results if r.content and r.relevance > 0]
                if filled:
                    additional_knowledge = self.writer.format_knowledge_results(filled)
                    self._log(
                        f"主动检索: {len(filled)}/{len(needs)} 个缺口已补充",
                        "success",
                    )
                else:
                    self._log(
                        f"主动检索: 未找到补充信息（{len(needs)} 个缺口）",
                        "info",
                    )

        # Step 2-3: 创作 → 评分循环
        best_text = ""
        best_evaluation = None
        retry_count = 0

        # 首次创作（注入主动检索的补充信息）
        console.print(f"[bold]✍️  Writer 创作[/bold] {chapter_id}")
        with self.metrics.trace("writer", "write", chapter_id):
            # Agent 自治模式：开启 mid-write 工具调用
            if self.config.safety_fence.enabled and self.tool_registry is not None:
                self._log("Agent 自治模式: 支持创作中主动查询", "info")
                # 将补充信息注入 outline 的 summary
                if additional_knowledge:
                    enhanced = _OutlineWithKnowledge(outline, additional_knowledge)
                    chapter_text = self.writer.write_with_autonomy(
                        chapter_id, enhanced, previous_text,
                        chapter_hint=chapter_hint,
                    )
                else:
                    chapter_text = self.writer.write_with_autonomy(
                        chapter_id, outline, previous_text,
                        chapter_hint=chapter_hint,
                    )
            else:
                # 传统模式：无 mid-write 工具调用
                chapter_text = self.writer.write(
                    chapter_id,
                    outline,
                    previous_text,
                    additional_knowledge=additional_knowledge,
                    chapter_hint=chapter_hint,
                )
        word_count = len(chapter_text)
        self._log(f"Writer 创作完成: {word_count} 字", "success")

        for attempt in range(MAX_CHAPTER_RETRIES + 1):
            # Critic 评分
            console.print(f"[bold]📊 Critic 评分[/bold] (第 {attempt + 1} 次)")
            with self.metrics.trace("critic", "evaluate", chapter_id):
                evaluation = self.critic.evaluate(chapter_id, chapter_text, outline)
            d = evaluation.dimensions
            score_str = (
                f"{evaluation.total_score} 分 "
                f"(文笔{d[0].score} 情节{d[1].score} 角色{d[2].score} "
                f"节奏{d[3].score} 情感{d[4].score})"
            )
            self._log(f"Critic 评分: {score_str}", "success" if evaluation.is_pass else "warning")

            # 记录评审历史到指标数据库
            self.metrics.record_evaluation(
                chapter_id=chapter_id,
                total_score=evaluation.total_score,
                dimensions=[d[0].score, d[1].score, d[2].score, d[3].score, d[4].score],
                is_pass=evaluation.is_pass,
                retry_count=attempt,
            )

            if evaluation.is_pass:
                best_text = chapter_text
                best_evaluation = evaluation
                retry_count = attempt
                status = "✓ 优秀" if evaluation.is_excellent else "✓ 合格"
                console.print(f"[bold green]{status}[/bold green] {score_str}")
                break

            # 不合格：记录最佳版本
            if best_evaluation is None or evaluation.total_score > best_evaluation.total_score:
                best_text = chapter_text
                best_evaluation = evaluation

            if attempt >= MAX_CHAPTER_RETRIES:
                self._log(f"已达最大重试次数 ({MAX_CHAPTER_RETRIES})，取最高分版本", "warning")
                retry_count = attempt
                break

            # Writer 修订（局部热修复优先 → 全章 revise 回退）
            console.print(
                f"[bold yellow]↩️  退回 Writer 修订[/bold yellow] (第 {attempt + 1} 次重试)"
            )
            self._log(f"不合格 ({evaluation.total_score} 分)，退回修订", "warning")

            if evaluation.has_anchored_issues and hasattr(self.writer, "hot_fix"):
                # 优先局部热修复（ADR 0006 — Agent 自治）
                # 安全围栏检查：hot_fix 是自治调用，约束递归深度和 Token
                if self._check_safety("writer", additional_tokens=2000):
                    anchored_data = [a.model_dump() for a in evaluation.anchored_issues]
                    console.print(f"[bold]🔧 局部热修复[/bold] ({len(anchored_data)} 个问题)")
                    with (
                        self.safety_fence.autonomous_call("writer", max_tokens=2000),
                        self.metrics.trace("writer", "hot_fix", chapter_id),
                    ):
                        hot_fixed = self.writer.hot_fix(
                            chapter_id,
                            outline,
                            chapter_text,
                            anchored_data,
                        )
                    if hot_fixed is not None:
                        chapter_text = hot_fixed
                        self._log(
                            f"局部热修复完成: {len(chapter_text)} 字",
                            "success",
                        )
                        self.safety_fence.record_tokens(2000)
                        continue  # 直接进入下一轮评估

                    # hot_fix 失败，回退到全章 revise
                    self._log("局部热修复失败（无法定位原文），回退到全章修订", "warning")
                else:
                    self._log("安全围栏阻断 hot_fix，回退到全章修订", "warning")

            # 全章 revise
            if evaluation.has_anchored_issues:
                parts = []
                for issue in evaluation.anchored_issues:
                    parts.append(
                        f"[{issue.severity.upper()}] [{issue.dimension}]\n"
                        f'  原文: "{issue.quote}"\n'
                        f"  问题: {issue.problem}\n"
                        f"  建议: {issue.suggestion}"
                    )
                feedback = "不合格原因及修改指引:\n" + "\n".join(parts)
            else:
                issues_text = "\n".join(f"- {i}" for i in evaluation.issues)
                suggestions_text = "\n".join(f"- {s}" for s in evaluation.suggestions)
                feedback = f"不合格原因:\n{issues_text}\n\n改进建议:\n{suggestions_text}"

            with self.metrics.trace("writer", "revise", chapter_id):
                chapter_text = self.writer.revise(chapter_id, outline, chapter_text, feedback)
            self._log(f"Writer 修订完成: {len(chapter_text)} 字", "success")

        if best_evaluation is None:
            raise RuntimeError(f"章节 {chapter_id} 创作失败：所有尝试均未通过评审")

        # 使用最高分版本的字数（而非最后一次修订的字数）
        word_count = len(best_text)
        active_chars = self._get_active_characters()
        chapter_type = detect_chapter_type(chapter_hint)

        # Step 4-5: 条件管线分支
        # 条件跳转（Conditional Jump）— ADR 0006:
        #   评分 >= 90 → 跳过 Manager 实时更新，延后批处理
        if should_skip_manager(best_evaluation):
            self._log(
                f"条件跳转: 评分 {best_evaluation.total_score} >= {MANAGER_BATCH_THRESHOLD}，"
                f"跳过 Manager 实时更新（延后批处理）",
                "info",
            )
            manager_skipped = True
            manager_summary = ""
            event_ids: list[str] = []

            # 延后章节也写入摘要（用已知信息，不依赖 Manager 实时输出）
            try:
                write_summary(
                    project_root=self.project_root,
                    chapter_id=chapter_id,
                    chapter_title=outline.title,
                    chapter_summary=f"评分 {best_evaluation.total_score}，延后批处理。",
                    total_score=best_evaluation.total_score,
                    word_count=word_count,
                )
            except Exception as e:
                self._log(f"摘要写入失败（不影响创作）: {e}", "warning")

            # 记录延后批处理数据
            self._deferred_manager_updates.append(
                DeferredManagerData(
                    chapter_id=chapter_id,
                    chapter_text=best_text,
                    active_characters=active_chars,
                    chapter_type=chapter_type,
                )
            )
        else:
            manager_skipped = False
            console.print(f"[bold]🔄 Manager 更新状态[/bold] {chapter_id}")
            manager_result = None
            try:
                with self.metrics.trace("manager", "update", chapter_id):
                    manager_result = self.manager.update(chapter_id, best_text, active_chars)
                self._log(
                    f"Manager 更新: {len(manager_result.character_updates)} 个角色变更, "
                    f"{len(manager_result.events)} 个事件",
                    "success",
                )
                manager_summary = manager_result.chapter_summary
                event_ids = [e.event_id for e in manager_result.events] if manager_result else []

                # 自动写入章节摘要（零额外 Token，复用 Manager 输出）
                try:
                    key_events = [e.description for e in manager_result.events[:10]] if manager_result.events else None
                    char_changes = [
                        f"{u.character_id}.{u.field}: {u.value} ({u.reason})"
                        for u in manager_result.character_updates[:10]
                    ] if manager_result.character_updates else None
                    write_summary(
                        project_root=self.project_root,
                        chapter_id=chapter_id,
                        chapter_title=outline.title,
                        chapter_summary=manager_summary,
                        total_score=best_evaluation.total_score,
                        word_count=word_count,
                        key_events=key_events,
                        character_changes=char_changes,
                        mismatches=[str(m) for m in chapter_mismatches] if chapter_mismatches else None,
                    )
                except Exception as e:
                    self._log(f"摘要写入失败（不影响创作）: {e}", "warning")

            except Exception as e:
                self._log(f"Manager 更新失败: {e}", "error")
                manager_summary = ""
                event_ids = []

        # Step 5: 快照 + 写入 + 一致性校验
        chapter_path = self.project_root / "draft" / f"{chapter_id}.md"

        # 构建受影响文件列表（章节文件 + 活跃角色文件）
        affected_files = [chapter_path]
        for char_id in active_chars:
            char_path = self.project_root / "characters" / f"{char_id}.md"
            if char_path.exists():
                affected_files.append(char_path)

        # 写入前快照（铁律 4：操作可逆）
        snapshot = self.state_manager.create_snapshot(chapter_id, affected_files)
        self._log(f"快照已创建: {snapshot.snapshot_id}", "info")

        # 写入章节文件
        chapter_meta = {
            "id": chapter_id,
            "title": outline.title,
            "pov": (
                outline.scenes[0].characters_involved[0]
                if outline.scenes and outline.scenes[0].characters_involved
                else ""
            ),
            "active_characters": active_chars,
        }
        self.storage.write_markdown_file(chapter_path, chapter_meta, best_text)
        self._log(f"章节已写入: {chapter_path}", "success")

        # 完成快照
        self.state_manager.update_snapshot_after(
            snapshot.snapshot_id,
            affected_files,
            event_ids,
        )

        # 一致性校验
        chapter_mismatches = self.diff_checker.check_chapter(chapter_path)
        if chapter_mismatches:
            for m in chapter_mismatches:
                self._log(f"[{m.severity}] {m.message}", "warning")
        else:
            self._log("一致性校验通过", "success")

        return ChapterResult(
            chapter_id=chapter_id,
            outline=outline,
            chapter_text=best_text,
            evaluation=best_evaluation,
            retry_count=retry_count,
            manager_summary=manager_summary,
            word_count=word_count,
            mismatches=chapter_mismatches,
            manager_skipped=manager_skipped,
        )

    def run(self, outline_text: str) -> RunReport:
        """执行完整创作循环。

        Args:
            outline_text: 大纲文本 (Markdown 格式)

        Returns:
            RunReport 完整运行报告
        """
        report = RunReport(start_time=datetime.now().isoformat())
        chapters = self._parse_outline(outline_text)

        # 限制章节数
        max_chapters = self.config.target_chapters
        if len(chapters) > max_chapters:
            chapters = chapters[:max_chapters]

        report.total_chapters = len(chapters)
        console.print(
            Panel(
                f"[bold cyan]OpenNovel Auto[/bold cyan] - 三 Agent 自主创作\n"
                f"章节数: {len(chapters)} | 每章目标: {self.config.words_per_chapter} 字\n"
                f"创作方向: {self.config.creative_direction or '无特殊要求'}",
                border_style="cyan",
            )
        )

        results: list[ChapterResult] = []
        for i, (chapter_id, chapter_hint) in enumerate(chapters):
            console.print(f"\n{'=' * 60}")
            console.print(f"[bold cyan]📖 第 {i + 1}/{len(chapters)} 章: {chapter_id}[/bold cyan]")
            console.print(f"{'=' * 60}")

            previous_summary = self._get_previous_summary(i, results)
            previous_text = self._get_previous_chapter_text(i, results)
            chapter_type = detect_chapter_type(chapter_hint)

            try:
                result = self.run_chapter(
                    chapter_id,
                    chapter_hint,
                    previous_summary,
                    previous_text,
                    results=results,
                )
                results.append(result)
                report.successful_chapters += 1

                # Director 全局分析 — 条件路由（ADR 0006）
                #   高潮章节: 强制运行
                #   过渡章节: 跳过
                #   日常章节: 每 N 章运行一次
                should_run_director = self.director is not None and not should_skip_director(
                    chapter_type, i, len(chapters)
                )

                if should_run_director:
                    # 安全围栏检查：Director 分析消耗约 3000 tokens
                    if not self._check_safety("director", additional_tokens=3000):
                        self._log("安全围栏阻断 Director 分析", "warning")
                        continue

                    try:
                        # 传递已完成结果 + 下一章 hint + 剩余章节（供调度决策）
                        remaining = chapters[i + 1 :]
                        analysis = self.director.analyze(
                            results,
                            chapters[i + 1][1],
                            remaining_chapters=remaining,
                        )
                        # 注入策略指导到下一章 hint
                        if analysis.strategic_guidance:
                            next_id, next_hint = chapters[i + 1]
                            chapters[i + 1] = (
                                next_id,
                                f"{next_hint}\n\n### 导演策略指导\n{analysis.strategic_guidance}",
                            )
                            self._log(
                                f"Director 指导: {analysis.strategic_guidance[:80]}...",
                                "info",
                            )
                        # 注入创作方向调整
                        if analysis.creative_direction_adjustment:
                            self.writer.creative_direction += (
                                f"\n{analysis.creative_direction_adjustment}"
                            )
                        # 记录警告
                        for warning in analysis.warnings:
                            self._log(f"Director 警告: {warning}", "warning")

                        # 处理章节调度提议（P4 Director 增强）
                        if analysis.scheduling_proposals:
                            chapters = self._apply_scheduling_proposals(
                                chapters,
                                analysis.scheduling_proposals,
                                i,
                            )

                    except Exception as e:
                        self._log(f"Director 分析失败（不影响创作）: {e}", "warning")
                elif chapter_type == ChapterType.TRANSITION:
                    self._log("条件跳转: 过渡章节，跳过 Director 分析", "info")

            except Exception as e:
                self._log(f"章节 {chapter_id} 创作失败: {e}", "error")
                report.failed_chapters += 1

        # ── 批处理延后的 Manager 更新 ──
        if self._deferred_manager_updates:
            self._process_deferred_manager_updates(results)

        # 写入最终时间线
        try:
            write_timeline(self.project_root)
            self._log(f"时间线已写入: timeline/events.md", "info")
        except Exception as e:
            self._log(f"时间线写入失败（不影响报告）: {e}", "warning")

        report.chapters = results
        report.end_time = datetime.now().isoformat()
        report.log_lines = self.log_lines
        report.all_mismatches = [m for r in results for m in r.mismatches]

        # 写入日志文件
        self._write_log(report)

        # 输出最终报告
        self._print_report(report)

        return report

    def _write_log(self, report: RunReport) -> None:
        """写入运行日志到 run_log.md。"""
        log_path = self.project_root / "run_log.md"
        lines = [
            "# 自主创作日志\n",
            f"开始时间: {report.start_time}",
            f"结束时间: {report.end_time}",
            (
                f"总章节: {report.total_chapters} | "
                f"成功: {report.successful_chapters} | "
                f"失败: {report.failed_chapters}\n"
            ),
        ]

        for result in report.chapters:
            lines.append(f"## {result.chapter_id}: {result.outline.title}")
            lines.append(f"- 字数: {result.word_count}")
            lines.append(f"- 评分: {result.evaluation.total_score} 分")
            lines.append(f"- 重试: {result.retry_count} 次")
            if result.manager_skipped:
                lines.append("- Manager 更新: 延后批处理")
            elif result.manager_summary:
                lines.append(f"- 摘要: {result.manager_summary}")
            if result.mismatches:
                lines.append(f"- 一致性问题: {len(result.mismatches)} 个")
                for m in result.mismatches:
                    lines.append(f"  - [{m.severity}] {m.message}")
            else:
                lines.append("- 一致性校验: ✓ 通过")
            lines.append("")

        # 全局一致性汇总
        total_mismatches = len(report.all_mismatches)
        if total_mismatches > 0:
            lines.append("## 一致性校验汇总")
            lines.append(f"共 {total_mismatches} 个问题\n")
            for m in report.all_mismatches:
                lines.append(f"- [{m.severity}] [{m.category}] {m.message}")
            lines.append("")

        lines.append("---")
        lines.append("## 完整日志")
        lines.extend(self.log_lines)

        try:
            log_path.write_text("\n".join(lines), encoding="utf-8")
            console.print(f"\n[dim]日志已写入: {log_path}[/dim]")
        except Exception as e:
            logger.error("日志写入失败: %s", e)

    def _print_report(self, report: RunReport) -> None:
        """输出最终报告。"""
        console.print(f"\n{'=' * 60}")
        console.print("[bold cyan]📊 创作完成报告[/bold cyan]")
        console.print(f"{'=' * 60}")

        for result in report.chapters:
            score = result.evaluation.total_score
            status = (
                "[green]优秀[/green]"
                if score >= 90
                else "[yellow]合格[/yellow]"
                if score >= 80
                else "[red]待审[/red]"
            )
            console.print(
                f"  {result.chapter_id}: {result.outline.title} | "
                f"{score} 分 {status} | {result.word_count} 字 | "
                f"重试 {result.retry_count} 次"
            )

        total_words = sum(r.word_count for r in report.chapters)
        avg_score = (
            sum(r.evaluation.total_score for r in report.chapters) / len(report.chapters)
            if report.chapters
            else 0
        )
        deferred_count = sum(1 for r in report.chapters if r.manager_skipped)

        console.print(f"\n  总字数: {total_words}")
        console.print(f"  平均分: {avg_score:.1f}")
        console.print(f"  成功率: {report.successful_chapters}/{report.total_chapters}")
        if deferred_count:
            console.print(f"  条件管线: [dim]{deferred_count} 章 Manager 延后批处理[/dim]")

        if report.all_mismatches:
            warnings = sum(1 for m in report.all_mismatches if m.severity.value == "WARNING")
            infos = sum(1 for m in report.all_mismatches if m.severity.value == "INFO")
            console.print(
                f"  一致性问题: [yellow]{warnings}[/yellow] 警告, [dim]{infos}[/dim] 信息"
            )
        else:
            console.print("  一致性校验: [green]全部通过[/green]")

        # Token 消耗统计
        try:
            usage = self.metrics.get_usage_by_agent()
            if usage:
                total = sum(v["total_tokens"] for v in usage.values())
                console.print(f"\n  Token 总消耗: {total:,}")
                for agent, stats in sorted(usage.items()):
                    console.print(f"    {agent}: {stats['total_tokens']:,}")
        except Exception:
            pass
