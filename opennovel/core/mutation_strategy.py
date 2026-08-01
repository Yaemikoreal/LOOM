"""变异策略引擎 - 智能选择变异维度和模板。

策略引擎根据当前叙事状态（评审分数、章节特征、已用维度），
选择 1-2 个最相关的变异维度和对应的结构模板。

选择逻辑：
1. 评审分数低的维度 → 优先变异（纠错型）
2. 高潮/转折章节 → 优先叙事结构变异（探索型）
3. 避免重复使用同一维度（多样性）
"""

import logging
import random

from opennovel.schemas.evaluation import ChapterEvaluation
from opennovel.schemas.mutation import (
    MutationDimension,
    MutationPlan,
    StructuralTemplate,
    get_templates_by_dimension,
)

logger = logging.getLogger(__name__)

# 维度到评审维度的映射（用于纠错型变异）
DIMENSION_TO_EVAL_INDEX: dict[MutationDimension, int] = {
    MutationDimension.NARRATIVE_STRUCTURE: 1,  # 情节逻辑
    MutationDimension.POV_VOICE: 0,  # 文笔质量
    MutationDimension.CAUSAL_TIMELINE: 3,  # 节奏把控
    MutationDimension.ARC_THEME: 4,  # 情感表达
}


def select_mutation_plan(
    evaluation: ChapterEvaluation | None = None,
    used_dimensions: list[MutationDimension] | None = None,
    is_climax: bool = False,
    variation_mode: str = "exploratory",
) -> MutationPlan:
    """选择变异计划。

    Args:
        evaluation: 前一章评审结果（纠错型模式使用）
        used_dimensions: 已使用过的变异维度（避免重复）
        is_climax: 是否为高潮章节
        variation_mode: "exploratory"（探索型）或 "corrective"（纠错型）

    Returns:
        MutationPlan 变异计划
    """
    used = set(used_dimensions or [])

    if variation_mode == "corrective" and evaluation:
        return _select_corrective_plan(evaluation, used)
    else:
        return _select_exploratory_plan(used, is_climax)


def _select_corrective_plan(
    evaluation: ChapterEvaluation,
    used: set[MutationDimension],
) -> MutationPlan:
    """纠错型变异：针对评审分数最低的维度选择变异。

    Args:
        evaluation: 评审结果
        used: 已使用的维度

    Returns:
        MutationPlan
    """
    # 找出分数最低的维度
    dimension_scores: list[tuple[MutationDimension, int]] = []
    for dim, eval_idx in DIMENSION_TO_EVAL_INDEX.items():
        if eval_idx < len(evaluation.dimensions):
            score = evaluation.dimensions[eval_idx].score
            dimension_scores.append((dim, score))

    # 按分数排序（最低优先）
    dimension_scores.sort(key=lambda x: x[1])

    selected_dims: list[MutationDimension] = []
    for dim, _score in dimension_scores:
        if dim not in used and len(selected_dims) < 1:
            selected_dims.append(dim)
            break

    # 如果所有维度都用过，选分数最低的
    if not selected_dims and dimension_scores:
        selected_dims.append(dimension_scores[0][0])

    templates = _select_templates(selected_dims)
    lowest_score = dimension_scores[0][1] if dimension_scores else 50

    return MutationPlan(
        dimensions=selected_dims,
        templates=templates,
        intensity=max(0.3, min(0.9, (80 - lowest_score) / 100)),
        rationale=(
            f"针对评审薄弱维度（"
            f"{selected_dims[0].value if selected_dims else 'N/A'}，"
            f"得分={lowest_score}）进行结构性变异"
        ),
    )


def _select_exploratory_plan(
    used: set[MutationDimension],
    is_climax: bool,
) -> MutationPlan:
    """探索型变异：随机选择维度，高潮章节优先叙事结构。

    Args:
        used: 已使用的维度
        is_climax: 是否为高潮章节

    Returns:
        MutationPlan
    """
    all_dims = list(MutationDimension)

    # 高潮章节优先叙事结构
    if is_climax and MutationDimension.NARRATIVE_STRUCTURE not in used:
        selected = [MutationDimension.NARRATIVE_STRUCTURE]
    else:
        # 从未使用的维度中选择
        available = [d for d in all_dims if d not in used]
        if not available:
            available = all_dims  # 全部用过则允许重复
        selected = [random.choice(available)]

    templates = _select_templates(selected)

    return MutationPlan(
        dimensions=selected,
        templates=templates,
        intensity=random.uniform(0.4, 0.8),
        rationale=f"探索型变异：{selected[0].value}" + ("（高潮章节）" if is_climax else ""),
    )


def _select_templates(
    dimensions: list[MutationDimension],
) -> list[StructuralTemplate]:
    """为选定维度选择具体的结构模板。

    Args:
        dimensions: 选定的变异维度

    Returns:
        对应的结构模板列表
    """
    if not dimensions:
        return []

    candidates = get_templates_by_dimension(dimensions[0])
    if not candidates:
        return []

    return [random.choice(candidates)]


def build_mutation_prompt_hint(plan: MutationPlan) -> str:
    """将变异计划转换为 Writer Prompt 注入指令。

    Args:
        plan: 变异计划

    Returns:
        可注入 Prompt 的变异指令文本
    """
    if not plan.templates:
        return ""

    hints = []
    for template in plan.templates:
        hints.append(f"### 结构变异指令: {template.name}\n{template.prompt_hint}")

    return "\n\n".join(hints)
