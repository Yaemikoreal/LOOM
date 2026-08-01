"""Writer Agent - 沉浸式创作代理。

负责思考规划（输出结构化大纲）和文学创作（输出章节正文）。
两阶段工作流: think → write/revise。

支持 Agent 自治（ADR 0006）：通过 write_with_autonomy() 实现
创作中的主动工具调用，Writer 可在写作过程中自主查询缺失信息。
"""

import json
import logging
from pathlib import Path
from typing import Any

from opennovel.core.agent_autonomy import (
    AutonomousConfig,
    AutonomousWriteLoop,
    ToolCallExecutor,
    ToolCallParser,
)
from opennovel.core.context_assembler import (
    ContextStrategy,
    assemble_context,
    detect_strategy,
    get_model_window,
)
from opennovel.core.hybrid_retriever import HybridRetriever
from opennovel.core.llm import LLMBus
from opennovel.core.mutation_strategy import build_mutation_prompt_hint, select_mutation_plan
from opennovel.core.retriever import Retriever
from opennovel.schemas.evaluation import ChapterEvaluation
from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeResult, KnowledgeSource
from opennovel.schemas.mutation import MutationDimension
from opennovel.schemas.outline import ChapterOutline
from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class Writer:
    """Writer Agent — 小说创作代理。

    支持阶段级模型路由（ADR 0005 成本优化器）：
    - think_model: 思考阶段模型（可用便宜小模型，如 gpt-4o-mini）
    - write_model: 创作阶段模型（主力大模型）
    - revise_model: 修订阶段模型（不设置则继承 write_model 或默认 model）

    使用方式:
        writer = Writer(llm_bus=llm_bus, retriever=retriever, project_root=project_root)
        outline = writer.think("ch_001", "四人在加油站相遇", previous_summary="")
        chapter_text = writer.write("ch_001", outline, previous_chapter_text="")
    """

    def __init__(
        self,
        llm_bus: LLMBus,
        retriever: Retriever,
        project_root: Path,
        prompt_path: Path | None = None,
        creative_direction: str = "",
        words_per_chapter: int = 3000,
        event_store: EventStore | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        think_model: str | None = None,
        write_model: str | None = None,
        revise_model: str | None = None,
        write_model_climax: str | None = None,
        tool_registry: Any | None = None,
        safety_fence: Any | None = None,
        autonomy_config: AutonomousConfig | None = None,
    ) -> None:
        self.llm_bus = llm_bus
        self.retriever = retriever
        self.project_root = project_root
        self.prompt_path = prompt_path or (
            Path(__file__).parent.parent / "prompts" / "writer.v1.md"
        )
        self.creative_direction = creative_direction
        self.words_per_chapter = words_per_chapter
        self.event_store = event_store
        self.hybrid_retriever = hybrid_retriever
        # 阶段级模型路由（ADR 0005）
        self.think_model = think_model
        self.write_model = write_model or think_model
        self.revise_model = revise_model or write_model or think_model
        self.write_model_climax = write_model_climax  # 高潮章节专用模型
        # Agent 自治（ADR 0006）
        self.tool_registry = tool_registry
        self.safety_fence = safety_fence
        self.autonomy_config = autonomy_config or AutonomousConfig(enabled=False)
        self._autonomy_loop: AutonomousWriteLoop | None = None
        self._tool_executor: ToolCallExecutor | None = None

    def _get_all_character_ids(self) -> list[str]:
        """获取所有角色 ID。"""
        chars_dir = self.project_root / "characters"
        if not chars_dir.exists():
            return []
        return sorted([f.stem for f in chars_dir.glob("char_*.md")])

    def _build_context(
        self,
        task_message: str,
    ) -> list[dict[str, str]]:
        """通过 ContextAssembler 组装完整上下文（CANON + STATE + SUBCONSCIOUS + 任务）。

        优先使用 HybridRetriever 统一检索，回退到 Retriever + EventStore 分离模式。

        Args:
            task_message: 任务指令（大纲/创作/修订等）

        Returns:
            组装完成的消息列表
        """
        if self.hybrid_retriever:
            # 混合检索模式：统一 SQL + 向量
            result = self.hybrid_retriever.query_for_writer(
                chapter_id="", outline_hint=task_message[:500]
            )
            canon_content = result.canon_content
            subconscious_content = result.subconscious_content
            causal_chain_context = result.causal_chain_context
        else:
            # 回退模式：Retriever + EventStore 分离
            canon_content = self.retriever.query_canon(task_message[:500], top_k=3)
            subconscious_content = self.retriever.query_subconscious(task_message[:500], top_k=2)
            causal_chain_context = ""
            if self.event_store:
                high_events = self.event_store.get_high_pressure_events(threshold=0.5)
                if high_events:
                    event_lines = []
                    for e in high_events[-10:]:
                        chain_info = ""
                        if e.caused_by:
                            chain_info = f" ← 由 {e.caused_by} 引起"
                        event_lines.append(
                            f"- [{e.event_id}] {e.event_type}: {e.description} "
                            f"(压强={e.causal_pressure}){chain_info}"
                        )
                    causal_chain_context = "\n".join(event_lines)

        return assemble_context(
            project_root=self.project_root,
            task_message=task_message,
            prompt_path=self.prompt_path,
            canon_content=canon_content,
            subconscious_content=subconscious_content,
            causal_chain_context=causal_chain_context,
            active_characters=self._get_all_character_ids(),
            strategy=detect_strategy(get_model_window(getattr(self.llm_bus, 'model', ''))),
        )

    def _build_think_task_message(
        self,
        chapter_id: str,
        chapter_outline_hint: str,
        previous_summary: str,
    ) -> str:
        """构建思考阶段的任务消息（不含设定/状态，由 ContextAssembler 注入）。"""
        return f"""## 思考任务

请为章节 `{chapter_id}` 进行深度思考规划，输出结构化 JSON 大纲。

### 大纲提示
{chapter_outline_hint}

### 创作方向
{self.creative_direction or "无特殊要求"}

### 目标字数
{self.words_per_chapter} 字

### 前文摘要
{previous_summary or "这是第一章，无前文"}

请输出合法的 JSON 对象，格式如下：
{{
  "chapter_id": "{chapter_id}",
  "title": "章节标题",
  "summary": "本章概要（200字以内）",
  "scenes": [
    {{
      "scene_id": "scene_1",
      "description": "场景描述",
      "characters_involved": ["char_001"],
      "emotional_tone": "情绪基调",
      "estimated_words": 800
    }}
  ],
  "character_arcs": {{"char_001": "情绪变化描述"}},
  "key_plot_points": ["关键情节节点"],
  "narrative_rhythm": "叙事节奏描述",
  "target_words": {self.words_per_chapter}
}}"""

    def detect_knowledge_gaps(
        self,
        outline: ChapterOutline,
        available_context: str = "",
    ) -> list[KnowledgeNeed]:
        """检测创作当前章节所需的知识缺口。

        扫描大纲中的场景和角色弧线，识别已有哪些上下文信息，
        列出需要额外查询的知识点。

        Args:
            outline: 当前章节的大纲
            available_context: 已注入的上下文文本（用于判断哪些知识已存在）

        Returns:
            需要查询的知识需求列表
        """
        needs: list[KnowledgeNeed] = []
        seen: set[str] = set()

        # 1. 检查大纲中涉及的角色（来自 scenes 和 character_arcs）
        all_char_ids: set[str] = set()
        for scene in outline.scenes:
            for cid in scene.characters_involved:
                all_char_ids.add(cid)
        for cid in outline.character_arcs:
            all_char_ids.add(cid)

        for cid in sorted(all_char_ids):
            if cid in seen:
                continue
            # 检查角色知识是否已在上下文中
            if cid not in available_context and cid not in seen:
                needs.append(
                    KnowledgeNeed(
                        concept=cid,
                        source=KnowledgeSource.CHARACTER,
                        context=f"获取角色 {cid} 的当前状态用于章节 {outline.chapter_id} 创作",
                        character_id=cid,
                    )
                )
                seen.add(cid)

        # 2. 检查场景描述中是否引用了不在上下文中的设定概念
        #    （简单启发式：查找 3 字以上的非角色名引用）
        for scene in outline.scenes:
            desc = scene.description
            # 提取可能的设定关键词（出现在引号或特定标记中的概念）

            # 检查 "世界观"、"设定"、"规则" 等关键词
            setting_keywords = ["魔法", "规则", "设定", "诅咒", "封印", "神", "传说", "历史"]
            for kw in setting_keywords:
                if kw in desc and kw not in available_context and kw not in seen:
                    needs.append(
                        KnowledgeNeed(
                            concept=kw,
                            source=KnowledgeSource.CANON,
                            context=f"场景 '{scene.description[:30]}' 中涉及 {kw}",
                        )
                    )
                    seen.add(kw)

        return needs

    @staticmethod
    def format_knowledge_results(results: list[KnowledgeResult]) -> str:
        """将知识查询结果格式化为作家可读的上下文文本。

        Args:
            results: 知识查询结果列表

        Returns:
            格式化的上下文文本，可用于增强创作 Prompt
        """
        if not results:
            return ""

        lines = ["\n### Agent 自治 — 主动检索的补充信息"]
        for r in results:
            if r.content and r.relevance > 0:
                lines.append(f"\n  [{r.source.value}] {r.concept}:")
                for line in r.content.split("\n"):
                    lines.append(f"    {line}")
        return "\n".join(lines)

    def _build_write_task_message(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        previous_chapter_text: str,
        feedback: str = "",
    ) -> str:
        """构建创作阶段的任务消息（不含设定/状态，由 ContextAssembler 注入）。"""
        scenes_text = "\n".join(
            f"- 场景 {s.scene_id}: {s.description} ({s.estimated_words}字, {s.emotional_tone})"
            for s in outline.scenes
        )
        arcs_text = "\n".join(f"- {cid}: {desc}" for cid, desc in outline.character_arcs.items())
        plot_text = "\n".join(f"- {p}" for p in outline.key_plot_points)

        revise_note = ""
        if feedback:
            # 检测是否为锚定反馈（包含原文引用）
            if "原文:" in feedback:
                revise_note = f"""

### 修订反馈（精确修改指引）
{feedback}

请根据以上定位指引精确修改：
1. 先在正文中找到「原文」引用的位置
2. 针对性地修改该段落，不要重写全章
3. 保持已有优点，只修正标注的问题"""
            else:
                revise_note = f"""

### 修订反馈（请针对性修改以下问题）
{feedback}
请在保持已有优点的基础上，针对上述问题进行修改。不要重写全章。"""

        prev_text = (
            previous_chapter_text[-2000:] if previous_chapter_text else "这是第一章，请从头开始"
        )

        return f"""## 创作任务

请根据以下大纲创作章节 `{chapter_id}` 的正文。

### 章节大纲
- 标题: {outline.title}
- 概要: {outline.summary}
- 目标字数: {outline.target_words}
- 叙事节奏: {outline.narrative_rhythm}

### 场景分解
{scenes_text}

### 角色情绪弧线
{arcs_text}

### 关键情节节点
{plot_text}

### 创作方向
{self.creative_direction or "无特殊要求"}

### 前文结尾
{prev_text}{revise_note}

请直接输出小说正文，不要输出任何元数据、注释或解释。以 `# {outline.title}` 作为开头。"""

    def _parse_outline_from_text(self, text: str, chapter_id: str) -> ChapterOutline:
        """从 LLM 输出中解析 ChapterOutline JSON。"""
        # 清理 markdown 代码块标记
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)

        data = json.loads(cleaned)
        data["chapter_id"] = chapter_id
        return ChapterOutline(**data)

    def think(
        self,
        chapter_id: str,
        chapter_outline_hint: str,
        previous_summary: str = "",
    ) -> ChapterOutline:
        """思考规划阶段：分析要素，输出结构化大纲。

        Args:
            chapter_id: 章节 ID (如 "ch_001")
            chapter_outline_hint: 大纲文件中本章的描述
            previous_summary: 前一章的摘要

        Returns:
            ChapterOutline 结构化大纲
        """
        task_message = self._build_think_task_message(
            chapter_id,
            chapter_outline_hint,
            previous_summary,
        )
        messages = self._build_context(task_message)

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.llm_bus.chat(
                    messages,
                    temperature=0.7,
                    model=self.think_model,
                )
                text = response.choices[0].message.content
                if not text:
                    raise ValueError("LLM 返回空文本")
                outline = self._parse_outline_from_text(text, chapter_id)
                logger.info("Writer 思考完成: %s, %d 个场景", chapter_id, len(outline.scenes))
                return outline
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    "Writer 思考 JSON 解析失败 (尝试 %d/%d): %s", attempt + 1, MAX_RETRIES + 1, e
                )
                if attempt < MAX_RETRIES:
                    messages.append({"role": "assistant", "content": text or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": f"你的输出 JSON 格式有误: {e}\n请重新输出合法的 JSON 对象。",
                        }
                    )

        raise RuntimeError(f"Writer 思考失败，已重试 {MAX_RETRIES} 次: {last_error}")

    def think_variations(
        self,
        chapter_id: str,
        chapter_outline_hint: str,
        previous_summary: str = "",
        n_variants: int = 3,
        variation_mode: str = "exploratory",
        corrective_feedback: str = "",
        previous_evaluation: ChapterEvaluation | None = None,
        used_dimensions: list[MutationDimension] | None = None,
        is_climax: bool = False,
    ) -> list[ChapterOutline]:
        """结构性变异：生成多个叙事方向的大纲方案。

        支持两种变异模式：
        - exploratory（探索型）：策略引擎随机选择维度 + 不同 temperature
        - corrective（纠错型）：针对评审薄弱维度进行结构性变异

        Args:
            chapter_id: 章节 ID
            chapter_outline_hint: 大纲文件中本章的描述
            previous_summary: 前一章的摘要
            n_variants: 生成方案数量
            variation_mode: "exploratory"（探索型）或 "corrective"（纠错型）
            corrective_feedback: 纠错模式下的 Critic 反馈
            previous_evaluation: 前一章评审结果（纠错型模式使用）
            used_dimensions: 已使用过的变异维度（避免重复）
            is_climax: 是否为高潮章节

        Returns:
            大纲方案列表（长度 = n_variants）
        """
        temperatures = [0.5, 0.7, 0.9][:n_variants]
        while len(temperatures) < n_variants:
            temperatures.append(min(temperatures[-1] + 0.15, 1.0))

        direction_hints = [
            "请尝试一个出人意料的叙事方向，打破读者预期。",
            "请尝试一个以角色内心成长为核心的叙事方向。",
            "请尝试一个以世界观揭示或秘密揭露为核心的叙事方向。",
        ]

        # 为每个方案生成独立的变异计划
        mutation_plans = []
        for _i in range(n_variants):
            plan = select_mutation_plan(
                evaluation=previous_evaluation,
                used_dimensions=used_dimensions,
                is_climax=is_climax,
                variation_mode=variation_mode,
            )
            mutation_plans.append(plan)

        outlines: list[ChapterOutline] = []
        for i in range(n_variants):
            task_message = self._build_think_task_message(
                chapter_id,
                chapter_outline_hint,
                previous_summary,
            )

            # 注入结构性变异指令（Phase 2.4）
            plan = mutation_plans[i]
            if plan.templates:
                mutation_hint = build_mutation_prompt_hint(plan)
                task_message += f"\n\n### 结构变异方案 {i + 1}\n{mutation_hint}"
                if plan.rationale:
                    task_message += f"\n变异理由: {plan.rationale}"

            # 探索型：添加方向提示（补充结构性变异）
            if variation_mode == "exploratory" and not plan.templates:
                hint = direction_hints[i % len(direction_hints)]
                task_message += f"\n\n### 创作方向提示（方案 {i + 1}）\n{hint}"

            # 纠错型：注入负向约束
            if variation_mode == "corrective" and corrective_feedback:
                task_message += (
                    f"\n\n### 前章问题（请在本方案中避免以下问题）\n"
                    f"{corrective_feedback}\n"
                    f"请尝试一种与前章不同的修复策略。"
                )

            messages = self._build_context(task_message)

            last_error = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    response = self.llm_bus.chat(
                        messages,
                        temperature=temperatures[i],
                        model=self.think_model,
                    )
                    text = response.choices[0].message.content
                    if not text:
                        raise ValueError("LLM 返回空文本")
                    outline = self._parse_outline_from_text(text, chapter_id)
                    outlines.append(outline)
                    logger.info(
                        "Writer 变异方案 %d/%d: %s, %d 个场景 (T=%.1f)",
                        i + 1,
                        n_variants,
                        chapter_id,
                        len(outline.scenes),
                        temperatures[i],
                    )
                    break
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    last_error = e
                    logger.warning(
                        "Writer 变异方案 %d 解析失败 (尝试 %d/%d): %s",
                        i + 1,
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
            else:
                raise RuntimeError(
                    f"Writer 变异方案 {i + 1} 失败，已重试 {MAX_RETRIES} 次: {last_error}"
                )

        return outlines

    def write(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        previous_chapter_text: str = "",
        additional_knowledge: str = "",
        chapter_hint: str = "",
    ) -> str:
        """创作阶段：根据大纲创作章节正文。

        Args:
            chapter_id: 章节 ID
            outline: 思考阶段输出的大纲
            previous_chapter_text: 前一章正文
            additional_knowledge: ToolRegistry 主动检索的补充上下文（可选）
            chapter_hint: 大纲提示文本，用于章节类型感知的模型选择

        Returns:
            章节正文 (纯文本)
        """
        task_message = self._build_write_task_message(
            chapter_id,
            outline,
            previous_chapter_text,
        )
        if additional_knowledge:
            task_message += f"\n\n{additional_knowledge}"
        messages = self._build_context(task_message)

        # 章节类型感知的模型选择
        model = self.write_model
        if chapter_hint and self.write_model_climax:
            from opennovel.core.chapter_utils import ChapterType, detect_chapter_type
            if detect_chapter_type(chapter_hint) == ChapterType.CLIMAX:
                model = self.write_model_climax
                logger.info("高潮章节 %s 使用增强模型: %s", chapter_id, model)

        response = self.llm_bus.chat(
            messages,
            temperature=0.8,
            max_tokens=4000,
            model=model,
        )
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("Writer 创作返回空文本")

        logger.info("Writer 创作完成: %s, %d 字", chapter_id, len(text))
        return text.strip()

    def write_with_autonomy(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        previous_chapter_text: str = "",
        chapter_hint: str = "",
    ) -> str:
        """创作阶段带自治能力：可 mid-write 主动查询缺失信息。

        与 write() 的区别：
        - 在创作 Prompt 末尾注入工具调用协议说明
        - 使用 AutonomousWriteLoop 多轮交互
        - 每次工具调用受 SafetyFence 约束
        - 需要 tool_registry 和 safety_fence 已配置

        Args:
            chapter_id: 章节 ID
            outline: 思考阶段输出的大纲
            previous_chapter_text: 前一章正文
            chapter_hint: 大纲提示文本，用于章节类型感知的模型选择

        Returns:
            章节正文 (纯文本)

        Raises:
            RuntimeError: 自治能力未配置或安全围栏违规时
        """
        if not self.tool_registry or not self.safety_fence:
            raise RuntimeError("Agent 自治未配置: 需要 tool_registry 和 safety_fence")

        # 构建基础任务消息
        task_message = self._build_write_task_message(
            chapter_id,
            outline,
            previous_chapter_text,
        )
        # 注入自治 Prompt 后缀（工具调用协议）
        task_message += ToolCallParser.get_autonomy_prompt_suffix()

        # 通过 ContextAssembler 组装完整上下文
        messages = self._build_context(task_message)

        # 创建自治执行器
        executor = ToolCallExecutor(self.tool_registry)
        loop = AutonomousWriteLoop(
            llm_bus=self.llm_bus,
            executor=executor,
            safety_fence=self.safety_fence,
            config=self.autonomy_config,
        )

        # 章节类型感知的模型选择
        model = self.write_model
        if chapter_hint and self.write_model_climax:
            from opennovel.core.chapter_utils import ChapterType, detect_chapter_type
            if detect_chapter_type(chapter_hint) == ChapterType.CLIMAX:
                model = self.write_model_climax
                logger.info("高潮章节 %s 使用增强模型: %s", chapter_id, model)

        # 执行自治循环
        return loop.execute(messages, model=model, agent_name="writer")

    def _build_hot_fix_task_message(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        current_text: str,
        anchored_issues: list[dict],
    ) -> str:
        """构建局部热修复的任务消息。

        Args:
            chapter_id: 章节 ID
            outline: 原始大纲
            current_text: 当前章节正文
            anchored_issues: 锚定问题列表（含 quote/problem/suggestion）

        Returns:
            热修复任务消息
        """
        # 提取每个问题的原文段落上下文
        issues_text = ""
        for i, issue in enumerate(anchored_issues, 1):
            quote = issue.get("quote", "")
            problem = issue.get("problem", "")
            suggestion = issue.get("suggestion", "")
            dimension = issue.get("dimension", "")
            severity = issue.get("severity", "minor")

            # 在正文中查找包含 quote 的段落
            surrounding = self._find_paragraph_around(current_text, quote)

            issues_text += (
                f"\n### 问题 {i}（{severity.upper()}）[{dimension}]\n"
                f'原文定位: "{quote}"\n'
                f"上下文段落:\n```\n{surrounding}\n```\n"
                f"问题描述: {problem}\n"
                f"修改建议: {suggestion}\n"
            )

        return f"""## 局部热修复任务

请根据 Critic 的评审反馈，对章节 `{chapter_id}` 中的问题段落进行精确修改。
你已经完成了全章创作，现在只需要修改被指出的问题区域，其余部分保持不变。

### 修改原则
1. **严禁重写全章** — 只修改被指出的问题段落
2. **保持上下文连贯** — 确保修改后的段落与周围文本自然衔接
3. **维持原有风格** — 保持全章在文笔、节奏、情感上的一致性
4. **只解决标注的问题** — 不要改动未提及的部分

### 章节大纲回顾
- 标题: {outline.title}
- 概要: {outline.summary}

### 待修复问题
{issues_text}

### 输出格式
请对每个问题分别输出修复后的段落，格式如下：

```
## 修复 1
[修复后的段落文本]

## 修复 2
[修复后的段落文本]
```

不需要输出未修改的部分，只需输出变更后的段落。"""

    def _find_paragraph_around(self, text: str, quote: str, context_chars: int = 200) -> str:
        """在正文中查找包含 quote 的段落及其上下文。

        先精确查找 quote 位置，然后扩展获取包含 quote 的整段前后文，
        找不到 quote 时回退到返回文本开头部分。

        Args:
            text: 当前章节正文
            quote: 要定位的原文引用（20-50 字）
            context_chars: 上下文扩展字符数

        Returns:
            包含 quote 的段落上下文
        """
        if not quote or not text:
            return text[: context_chars * 2] if text else ""

        pos = text.find(quote)
        if pos == -1:
            # 模糊匹配：按字查找
            for ch in quote[:5]:
                p = text.find(ch)
                if p != -1:
                    pos = p
                    break
            if pos == -1:
                return text[: context_chars * 2]

        # 向前扩展到段落开头或上下文边界
        start = max(0, pos - context_chars)
        while start > 0 and text[start] not in "\n\n":
            start -= 1

        # 向后扩展到段落结尾或上下文边界
        end = min(len(text), pos + len(quote) + context_chars)
        while end < len(text) and text[end - 1] not in "\n\n":
            end += 1

        return text[start:end].strip()

    def hot_fix(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        current_text: str,
        anchored_issues: list[dict],
    ) -> str | None:
        """局部热修复：根据锚定问题对章节进行精确段落级修改。

        与全章 revise() 不同，hot_fix 只替换被指出的问题段落，
        其余部分保持不变，从而节约 Token 且不破坏已通过的部分。

        Args:
            chapter_id: 章节 ID
            outline: 原始大纲
            current_text: 当前章节正文
            anchored_issues: 锚定问题列表，每项含 quote/problem/suggestion

        Returns:
            修复后的完整章节正文；如果修复失败或无法定位返回 None
        """
        if not anchored_issues or not current_text:
            return None

        # 验证能定位到所有问题的原文
        for issue in anchored_issues:
            quote = issue.get("quote", "")
            if quote and current_text.find(quote) == -1:
                logger.warning("hot_fix 无法定位原文引用: '%s'，回退到全章 revise", quote[:20])
                return None

        task_message = self._build_hot_fix_task_message(
            chapter_id,
            outline,
            current_text,
            anchored_issues,
        )
        messages = self._build_context(task_message)

        # 追加当前正文
        messages.append(
            {
                "role": "user",
                "content": (
                    f"以下是当前章节全文，请根据上面的问题定位进行精确修改：\n\n{current_text}"
                ),
            }
        )

        try:
            response = self.llm_bus.chat(
                messages,
                temperature=0.5,  # 低温度保持稳定性
                max_tokens=2000,  # 局部修复不需要太多 token
                model=self.revise_model,
            )
            text = response.choices[0].message.content
            if not text:
                logger.warning("hot_fix 返回空文本，回退到全章 revise")
                return None

            # 解析修复段落并替换到原文中
            revised = self._apply_hot_fix(current_text, text, anchored_issues)
            if revised is None or len(revised) < len(current_text) * 0.5:
                logger.warning(
                    "hot_fix 结果异常（长度 %d vs 原长 %d），回退到全章 revise",
                    len(revised or ""),
                    len(current_text),
                )
                return None

            logger.info(
                "hot_fix 完成: %s, %d 个问题修复, %d 字",
                chapter_id,
                len(anchored_issues),
                len(revised),
            )
            return revised

        except Exception as e:
            logger.warning("hot_fix 执行失败: %s，回退到全章 revise", e)
            return None

    def _apply_hot_fix(
        self,
        original_text: str,
        llm_output: str,
        anchored_issues: list[dict],
    ) -> str | None:
        """将 LLM 输出的修复段落合并回原文。

        解析 LLM 输出的 "## 修复 N" 标记的修复段落，
        查找原文中对应的段落并替换。

        Args:
            original_text: 原始章节全文
            llm_output: LLM 返回的修复段落文本
            anchored_issues: 锚定问题列表

        Returns:
            合并修复后的完整章节正文，失败时返回 None
        """
        # 解析修复段落
        import re

        fix_pattern = re.compile(r"## 修复\s+\d+\n(.+?)(?=\n## 修复|\Z)", re.DOTALL)
        fixes = fix_pattern.findall(llm_output)

        if not fixes:
            # 尝试整段替换（LLM 可能直接返回完整正文）
            if len(llm_output) > len(original_text) * 0.8:
                return llm_output.strip()
            return None

        # 按顺序替换（从后往前避免位置偏移）
        text = original_text
        try:
            # 先收集所有替换对
            replacements: list[tuple[int, int, str]] = []
            for fix_text, issue in zip(fixes, anchored_issues, strict=False):
                quote = issue.get("quote", "")
                if not quote:
                    continue
                pos = text.find(quote)
                if pos == -1:
                    continue

                # 找到包含 quote 的完整段落
                para_start = text.rfind("\n\n", 0, pos)
                para_start = para_start + 2 if para_start != -1 else 0
                para_end = text.find("\n\n", pos + len(quote))
                para_end = para_end if para_end != -1 else len(text)

                replacements.append((para_start, para_end, fix_text.strip()))

            # 从后往前应用替换（避免索引偏移）
            for start, end, new_text in sorted(replacements, key=lambda x: -x[0]):
                text = text[:start] + new_text + "\n" + text[end:]

            return text.strip()

        except Exception as e:
            logger.warning("_apply_hot_fix 合并失败: %s", e)
            return None

    def revise(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        current_text: str,
        feedback: str,
    ) -> str:
        """修订阶段：根据 Critic 反馈修改章节。

        Args:
            chapter_id: 章节 ID
            outline: 原始大纲
            current_text: 当前章节正文
            feedback: Critic 的反馈

        Returns:
            修订后的章节正文
        """
        task_message = self._build_write_task_message(
            chapter_id,
            outline,
            "",
            feedback=feedback,
        )
        messages = self._build_context(task_message)

        # 追加当前正文供修订
        messages.append(
            {
                "role": "user",
                "content": f"以下是当前章节正文，请根据上面的反馈进行修订：\n\n{current_text}",
            }
        )

        response = self.llm_bus.chat(
            messages,
            temperature=0.7,
            max_tokens=4000,
            model=self.revise_model,
        )
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("Writer 修订返回空文本")

        logger.info("Writer 修订完成: %s, %d 字", chapter_id, len(text))
        return text.strip()
