"""多模型编排器 — Agent 内部的 "提议-综合" LLM 混合机制。

为 Writer/Critic/Director 提供多模型并行提案能力：
- Writer: 创意生成 + 逻辑结构 + 细节描写 → 综合仲裁
- Critic: 多模型并行评分 → 中位数/均值融合
- Director: 多角度策略分析

详见 docs/adr/0010-multi-agent-architecture-enhancement.md。
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ModelProposal:
    """单个模型生成的提案。"""

    model: str
    """模型名称"""

    content: str
    """提案文本"""

    role: str = ""
    """提案角色（creative/logical/detail/synthesis）"""

    metadata: dict = field(default_factory=dict)
    """附加元数据（如大纲、评分维度等）"""


@dataclass
class SynthesisResult:
    """综合后的结果。"""

    content: str
    """综合后的内容"""

    proposals_used: list[str] = field(default_factory=list)
    """参与综合的提案模型列表"""

    method: str = "single"
    """融合方法：single / median / synthesis"""


class MultiModelOrchestrator:
    """多模型编排器。

    管理多个 LLM 的并行提案和结果综合。
    仅在作者显式配置多模型时激活，默认走单模型路径。

    使用方式:
        orch = MultiModelOrchestrator(llm_bus)
        result = orch.propose_and_synthesize(
            messages,
            models=["gpt-4o", "claude-sonnet-4-6", "deepseek/deepseek-chat"],
            roles=["creative", "logical", "detail"],
            synthesizer_model="gpt-4o",
        )
    """

    def __init__(self, llm_bus=None) -> None:
        """初始化编排器。

        Args:
            llm_bus: LLMBus 实例（用于调用各模型）
        """
        self._llm_bus = llm_bus

    def propose_parallel(
        self,
        messages: list[dict],
        models: list[str],
        roles: list[str] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> list[ModelProposal]:
        """并行调用多个模型生成提案。

        Args:
            messages: 发送给各模型的消息列表
            models: 模型名称列表
            roles: 各模型的角色标签（与 models 一一对应）
            temperature: 生成温度
            max_tokens: 最大输出 Token 数

        Returns:
            ModelProposal 列表（按调用顺序）
        """
        if not self._llm_bus:
            logger.warning("LLMBus 不可用，无法执行多模型提案")
            return []

        if not models:
            return []

        props: list[ModelProposal] = []
        if roles is None:
            roles = [f"model_{i}" for i in range(len(models))]

        for model, role in zip(models, roles):
            try:
                # 在构造时使用指定模型
                kwargs = {"temperature": temperature}
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens

                response = self._llm_bus.chat(
                    messages,
                    model=model,
                    **kwargs,
                )
                content = response.choices[0].message.content
                props.append(
                    ModelProposal(
                        model=model,
                        content=content,
                        role=role,
                    )
                )
                logger.debug("模型 %s (%s) 提案完成: %d 字符", model, role, len(content))
            except Exception as e:
                logger.warning("模型 %s (%s) 提案失败: %s", model, role, e)
                # 继续其他模型（不因单个失败而中断）

        return props

    def synthesize(
        self,
        proposals: list[ModelProposal],
        messages: list[dict],
        synthesizer_model: str,
        temperature: float = 0.5,
    ) -> SynthesisResult:
        """综合多个提案为最终输出。

        Args:
            proposals: 各模型生成的提案列表
            messages: 原始消息（用于提供上下文）
            synthesizer_model: 综合模型的名称
            temperature: 综合温度

        Returns:
            SynthesisResult 综合结果
        """
        if not proposals:
            return SynthesisResult(content="", method="single")

        if len(proposals) == 1:
            return SynthesisResult(
                content=proposals[0].content,
                proposals_used=[proposals[0].model],
                method="single",
            )

        # 构建综合 prompt
        synth_prompt = self._build_synthesis_prompt(proposals, messages)
        synth_messages = [{"role": "user", "content": synth_prompt}]

        try:
            response = self._llm_bus.chat(
                synth_messages,
                model=synthesizer_model,
                temperature=temperature,
            )
            content = response.choices[0].message.content
            return SynthesisResult(
                content=content,
                proposals_used=[p.model for p in proposals],
                method="synthesis",
            )
        except Exception as e:
            logger.warning("综合失败: %s，回退到第一个提案", e)
            return SynthesisResult(
                content=proposals[0].content,
                proposals_used=[proposals[0].model],
                method="single",
            )

    def multi_critic_vote(
        self,
        messages: list[dict],
        models: list[str],
        temperature: float = 0.3,
    ) -> dict:
        """多 Critic 并行评分投票。

        使用多个模型并行评分，取中位数作为最终分数，
        降低单一模型偏见。仅对 CLIMAX 章节默认启用。

        Args:
            messages: Critic 评估消息
            models: 模型列表（至少 2 个）
            temperature: 评估温度

        Returns:
            {"total_score": median, "individual_scores": [...], "variance": float}
        """
        if len(models) < 2:
            logger.debug("多 Critic 投票需要 ≥2 个模型")
            return {"total_score": 0, "individual_scores": [], "variance": 0.0}

        props = self.propose_parallel(
            messages,
            models=models,
            roles=["critic"] * len(models),
            temperature=temperature,
        )

        # 尝试从提案中提取分数
        scores: list[float] = []
        for p in props:
            score = self._extract_score_from_text(p.content)
            if score is not None:
                scores.append(score)

        if not scores:
            logger.warning("多 Critic 评分提取失败，所有模型均未返回有效分数")
            return {"total_score": 0, "individual_scores": [], "variance": 0.0}

        if len(scores) == 1:
            return {
                "total_score": scores[0],
                "individual_scores": scores,
                "variance": 0.0,
            }

        median_score = statistics.median(scores)
        variance = statistics.variance(scores) if len(scores) >= 2 else 0.0

        logger.info(
            "多 Critic 投票: median=%.1f, scores=%s, variance=%.2f",
            median_score,
            [f"{s:.1f}" for s in scores],
            variance,
        )
        return {
            "total_score": median_score,
            "individual_scores": scores,
            "variance": variance,
        }

    @staticmethod
    def _extract_score_from_text(text: str) -> float | None:
        """从 Critic 输出文本中提取总评分。

        Args:
            text: Critic 输出文本

        Returns:
            评分 0-100，或 None
        """
        import re

        # 匹配模式: "总评分: 85" / "total_score: 85" / "分数: 85/100"
        patterns = [
            r"总[体]?评分[：:]\s*(\d+(?:\.\d+)?)",
            r"total_score[：:\s]*(\d+(?:\.\d+)?)",
            r"分数[：:]\s*(\d+(?:\.\d+)?)",
            r"score[：:\s]*(\d+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                score = float(match.group(1))
                if 0 <= score <= 100:
                    return score
        return None

    @staticmethod
    def _build_synthesis_prompt(
        proposals: list[ModelProposal],
        original_messages: list[dict],
    ) -> str:
        """构建综合 prompt。"""
        original_task = ""
        for msg in original_messages:
            if msg.get("role") == "user":
                original_task = msg.get("content", "")[:500]
                break

        parts = [
            "你是一个创作综合仲裁者。以下是多个模型针对同一任务生成的提案。",
            "请分析各提案的优缺点，取精华去糟粕，输出一份综合后的最佳版本。",
            "",
            f"原始任务: {original_task}",
            "",
            "各模型提案:",
        ]

        for i, p in enumerate(proposals, 1):
            role_desc = f"（角色: {p.role}）" if p.role else ""
            parts.append(f"\n--- 提案 {i}: {p.model} {role_desc} ---")
            parts.append(p.content[:2000])

        parts.append("\n---\n请输出综合后的最终版本:")
        return "\n".join(parts)
