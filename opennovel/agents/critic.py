"""Critic Agent - 文学评判代理。

负责对 Writer 产出的章节进行五维百分制评分。
80 分以上合格，90 分以上优秀。
"""

import json
import logging
from pathlib import Path

from opennovel.core.context_assembler import (
    ContextStrategy,
    assemble_context,
    detect_strategy,
    get_model_window,
)
from opennovel.core.hybrid_retriever import HybridRetriever
from opennovel.core.llm import LLMBus
from opennovel.core.retriever import Retriever
from opennovel.schemas.evaluation import ChapterEvaluation
from opennovel.schemas.outline import ChapterOutline
from opennovel.schemas.outline_evaluation import OutlineEvaluation
from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class Critic:
    """Critic Agent — 文学评判员。

    使用方式:
        critic = Critic(llm_bus=llm_bus, project_root=project_root, retriever=retriever)
        evaluation = critic.evaluate("ch_001", chapter_text, outline)
        if evaluation.is_pass:
            print(f"合格! {evaluation.total_score} 分")
    """

    def __init__(
        self,
        llm_bus: LLMBus,
        project_root: Path,
        prompt_path: Path | None = None,
        retriever: Retriever | None = None,
        event_store: EventStore | None = None,
        hybrid_retriever: HybridRetriever | None = None,
    ) -> None:
        self.llm_bus = llm_bus
        self.project_root = project_root
        self.prompt_path = prompt_path or (
            Path(__file__).parent.parent / "prompts" / "critic.v1.md"
        )
        self.retriever = retriever
        self.event_store = event_store
        self.hybrid_retriever = hybrid_retriever

    def _load_prompt(self) -> str:
        """加载 Critic Prompt，文件不存在时返回硬编码兜底。"""
        if not self.prompt_path.exists():
            logger.warning("Critic Prompt 文件不存在: %s", self.prompt_path)
            return "你是一位文学评判员。对章节进行百分制评分。"
        return self.prompt_path.read_text(encoding="utf-8")

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
        """通过 ContextAssembler 组装完整评审上下文。

        注入 CANON（世界观校验基准）、STATE_MEMORY（角色状态校验基准）、
        SUBCONSCIOUS（情感表达参考）和因果链上下文（因果一致性校验）。
        优先使用 HybridRetriever 统一检索。
        """
        if self.hybrid_retriever:
            # 混合检索模式
            result = self.hybrid_retriever.query_for_critic(
                chapter_id="", chapter_text=task_message[:1000]
            )
            canon_content = result.canon_content
            subconscious_content = result.subconscious_content
            causal_chain_context = result.causal_chain_context
        else:
            # 回退模式
            canon_content = ""
            subconscious_content = ""
            if self.retriever:
                canon_content = self.retriever.query_canon(task_message[:500], top_k=3)
                subconscious_content = self.retriever.query_subconscious(
                    task_message[:500], top_k=2
                )
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

    def _build_task_message(
        self,
        chapter_id: str,
        chapter_text: str,
        outline: ChapterOutline,
    ) -> str:
        """构建评审任务消息（不含设定/状态，由 ContextAssembler 注入）。"""
        scenes_text = "\n".join(
            f"- {s.scene_id}: {s.description} ({s.emotional_tone})" for s in outline.scenes
        )

        return f"""## 评分任务

请对章节 `{chapter_id}` 进行专业评分。

### 章节大纲 (参考标准)
- 标题: {outline.title}
- 概要: {outline.summary}
- 场景分解:
{scenes_text}
- 叙事节奏: {outline.narrative_rhythm}

### 章节正文

{chapter_text}

请按照五维评分标准（文笔质量20分+情节逻辑20分+角色一致性20分+节奏把控20分+情感表达20分=100分）评分。

输出合法的 JSON 对象：
{{
  "total_score": 85,
  "dimensions": [
    {{"dimension": "文笔质量", "score": 18, "comment": "评语"}},
    {{"dimension": "情节逻辑", "score": 17, "comment": "评语"}},
    {{"dimension": "角色一致性", "score": 17, "comment": "评语"}},
    {{"dimension": "节奏把控", "score": 16, "comment": "评语"}},
    {{"dimension": "情感表达", "score": 17, "comment": "评语"}}
  ],
  "summary": "总体评价",
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1", "建议2"],
  "anchored_issues": [
    {{
      "dimension": "情节逻辑",
      "severity": "major",
      "quote": "原文中存在问题的 20-50 字引用",
      "problem": "问题描述",
      "suggestion": "修改建议",
      "location_hint": "第 3 段"
    }}
  ]
}}"""

    def _parse_evaluation_from_text(self, text: str) -> ChapterEvaluation:
        """从 LLM 输出中解析 ChapterEvaluation JSON。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)

        data = json.loads(cleaned)
        return ChapterEvaluation(**data)

    def evaluate(
        self,
        chapter_id: str,
        chapter_text: str,
        outline: ChapterOutline,
    ) -> ChapterEvaluation:
        """评估章节质量。

        Args:
            chapter_id: 章节 ID
            chapter_text: 章节正文
            outline: 章节大纲 (评分参考)

        Returns:
            ChapterEvaluation 评分结果
        """
        task_message = self._build_task_message(chapter_id, chapter_text, outline)
        messages = self._build_context(task_message)

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.llm_bus.chat(messages, temperature=0.2)
                text = response.choices[0].message.content
                if not text:
                    raise ValueError("LLM 返回空文本")
                evaluation = self._parse_evaluation_from_text(text)
                logger.info(
                    "Critic 评分完成: %s, %d 分 (%s)",
                    chapter_id,
                    evaluation.total_score,
                    "合格" if evaluation.is_pass else "不合格",
                )
                return evaluation
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    "Critic 评分 JSON 解析失败 (尝试 %d/%d): %s", attempt + 1, MAX_RETRIES + 1, e
                )
                if attempt < MAX_RETRIES:
                    messages.append({"role": "assistant", "content": text or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": f"你的输出 JSON 格式有误: {e}\n请重新输出合法的 JSON 对象。",
                        }
                    )

        raise RuntimeError(f"Critic 评分失败，已重试 {MAX_RETRIES} 次: {last_error}")

    def _build_outline_task_message(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        previous_summary: str,
    ) -> str:
        """构建大纲评审任务消息。"""
        scenes_text = "\n".join(
            f"- {s.scene_id}: {s.description} ({s.emotional_tone}, {s.estimated_words}字)"
            for s in outline.scenes
        )
        arcs_text = "\n".join(f"- {cid}: {desc}" for cid, desc in outline.character_arcs.items())
        plot_text = "\n".join(f"- {p}" for p in outline.key_plot_points)

        return f"""## 大纲评审任务

请对章节 `{chapter_id}` 的大纲方案进行专业评分。

### 大纲内容
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

### 前文摘要
{previous_summary or "这是第一章"}

请按照三维评分标准（情节逻辑20分+角色一致性20分+节奏设计20分=60分）评分。

输出合法的 JSON 对象：
{{
  "total_score": 48,
  "dimensions": [
    {{"dimension": "情节逻辑", "score": 17, "comment": "评语"}},
    {{"dimension": "角色一致性", "score": 16, "comment": "评语"}},
    {{"dimension": "节奏设计", "score": 15, "comment": "评语"}}
  ],
  "summary": "总体评价",
  "issues": ["问题1"],
  "suggestions": ["建议1"]
}}"""

    def evaluate_outline(
        self,
        chapter_id: str,
        outline: ChapterOutline,
        previous_summary: str = "",
    ) -> OutlineEvaluation:
        """评估大纲方案质量（三维评分，用于多方案预审选择）。

        Args:
            chapter_id: 章节 ID
            outline: 大纲方案
            previous_summary: 前一章摘要

        Returns:
            OutlineEvaluation 大纲评审结果
        """
        task_message = self._build_outline_task_message(
            chapter_id,
            outline,
            previous_summary,
        )

        # 使用大纲专用 prompt，复用 _build_context 的完整上下文注入
        original_prompt_path = self.prompt_path
        outline_prompt_path = self.prompt_path.parent / "critic_outline.v1.md"
        if outline_prompt_path.exists():
            self.prompt_path = outline_prompt_path
        try:
            messages = self._build_context(task_message)
        finally:
            self.prompt_path = original_prompt_path

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.llm_bus.chat(messages, temperature=0.2)
                text = response.choices[0].message.content
                if not text:
                    raise ValueError("LLM 返回空文本")
                evaluation = self._parse_outline_evaluation_from_text(text)
                logger.info(
                    "Critic 大纲评审完成: %s, %d 分",
                    chapter_id,
                    evaluation.total_score,
                )
                return evaluation
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    "Critic 大纲评审 JSON 解析失败 (尝试 %d/%d): %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    e,
                )
                if attempt < MAX_RETRIES:
                    messages.append({"role": "assistant", "content": text or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": f"你的输出 JSON 格式有误: {e}\n请重新输出合法的 JSON 对象。",
                        }
                    )

        raise RuntimeError(f"Critic 大纲评审失败，已重试 {MAX_RETRIES} 次: {last_error}")

    def _parse_outline_evaluation_from_text(self, text: str) -> OutlineEvaluation:
        """从 LLM 输出中解析 OutlineEvaluation JSON。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)

        data = json.loads(cleaned)
        return OutlineEvaluation(**data)
