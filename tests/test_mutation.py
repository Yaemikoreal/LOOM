"""深层变异系统测试 - Phase 2.4。

测试范围：
- MutationDimension 枚举
- StructuralTemplate 结构模板
- MutationPlan 变异计划
- MutationStrategy 策略引擎
- build_mutation_prompt_hint Prompt 构建
- Writer think_variations 结构性变异集成
"""

import pytest

from opennovel.core.mutation_strategy import (
    build_mutation_prompt_hint,
    select_mutation_plan,
)
from opennovel.schemas.evaluation import ChapterEvaluation, DimensionScore
from opennovel.schemas.mutation import (
    MutationDimension,
    MutationPlan,
    StructuralTemplate,
    TEMPLATES,
    get_template,
    get_templates_by_dimension,
)


class TestMutationDimension:
    """MutationDimension 枚举测试。"""

    def test_four_dimensions(self):
        """四个正交维度。"""
        assert len(MutationDimension) == 4

    def test_dimension_values(self):
        """维度值正确。"""
        assert MutationDimension.NARRATIVE_STRUCTURE == "narrative_structure"
        assert MutationDimension.POV_VOICE == "pov_voice"
        assert MutationDimension.CAUSAL_TIMELINE == "causal_timeline"
        assert MutationDimension.ARC_THEME == "arc_theme"


class TestStructuralTemplate:
    """StructuralTemplate 模型测试。"""

    def test_template_creation(self):
        """模板创建正确。"""
        template = StructuralTemplate(
            template_id="test",
            dimension=MutationDimension.NARRATIVE_STRUCTURE,
            name="测试模板",
            description="测试描述",
            prompt_hint="请按测试结构创作",
        )
        assert template.template_id == "test"
        assert template.dimension == MutationDimension.NARRATIVE_STRUCTURE

    def test_templates_library(self):
        """预定义模板库非空。"""
        assert len(TEMPLATES) > 0

    def test_templates_cover_all_dimensions(self):
        """模板覆盖所有维度。"""
        for dim in MutationDimension:
            templates = get_templates_by_dimension(dim)
            assert len(templates) >= 1, f"维度 {dim} 缺少模板"

    def test_get_template_by_id(self):
        """按 ID 获取模板。"""
        template = get_template("three_act_classic")
        assert template is not None
        assert template.name == "经典三幕剧"

    def test_get_template_nonexistent(self):
        """不存在的模板返回 None。"""
        assert get_template("nonexistent") is None

    def test_get_templates_by_dimension(self):
        """按维度获取模板列表。"""
        structure_templates = get_templates_by_dimension(
            MutationDimension.NARRATIVE_STRUCTURE
        )
        assert len(structure_templates) >= 2


class TestMutationPlan:
    """MutationPlan 模型测试。"""

    def test_plan_creation(self):
        """计划创建正确。"""
        plan = MutationPlan(
            dimensions=[MutationDimension.NARRATIVE_STRUCTURE],
            templates=[get_template("three_act_classic")],
            intensity=0.6,
            rationale="测试理由",
        )
        assert len(plan.dimensions) == 1
        assert plan.intensity == 0.6

    def test_plan_intensity_range(self):
        """强度范围校验。"""
        plan = MutationPlan(
            dimensions=[MutationDimension.ARC_THEME],
            templates=[],
            intensity=0.5,
        )
        assert 0.0 <= plan.intensity <= 1.0


class TestSelectMutationPlan:
    """select_mutation_plan 策略引擎测试。"""

    def test_exploratory_mode(self):
        """探索型模式返回有效计划。"""
        plan = select_mutation_plan(variation_mode="exploratory")
        assert len(plan.dimensions) >= 1
        assert plan.intensity > 0

    def test_corrective_mode_with_evaluation(self):
        """纠错型模式针对薄弱维度。"""
        evaluation = ChapterEvaluation(
            total_score=70,
            dimensions=[
                DimensionScore(dimension="文笔质量", score=14, comment=""),
                DimensionScore(dimension="情节逻辑", score=10, comment=""),
                DimensionScore(dimension="角色一致性", score=16, comment=""),
                DimensionScore(dimension="节奏把控", score=15, comment=""),
                DimensionScore(dimension="情感表达", score=15, comment=""),
            ],
            summary="",
            issues=[],
            suggestions=[],
        )
        plan = select_mutation_plan(
            evaluation=evaluation,
            variation_mode="corrective",
        )
        # 情节逻辑得分最低(10)，应选择 NARRATIVE_STRUCTURE
        assert MutationDimension.NARRATIVE_STRUCTURE in plan.dimensions

    def test_corrective_avoids_used_dimensions(self):
        """纠错型避免重复使用维度。"""
        evaluation = ChapterEvaluation(
            total_score=70,
            dimensions=[
                DimensionScore(dimension="文笔质量", score=14, comment=""),
                DimensionScore(dimension="情节逻辑", score=10, comment=""),
                DimensionScore(dimension="角色一致性", score=16, comment=""),
                DimensionScore(dimension="节奏把控", score=15, comment=""),
                DimensionScore(dimension="情感表达", score=15, comment=""),
            ],
            summary="",
            issues=[],
            suggestions=[],
        )
        plan = select_mutation_plan(
            evaluation=evaluation,
            used_dimensions=[MutationDimension.NARRATIVE_STRUCTURE],
            variation_mode="corrective",
        )
        # NARRATIVE_STRUCTURE 已用过，应选其他维度
        assert MutationDimension.NARRATIVE_STRUCTURE not in plan.dimensions

    def test_exploratory_climax(self):
        """高潮章节优先叙事结构变异。"""
        plan = select_mutation_plan(
            is_climax=True,
            variation_mode="exploratory",
        )
        assert MutationDimension.NARRATIVE_STRUCTURE in plan.dimensions

    def test_plan_has_templates(self):
        """计划包含有效的结构模板。"""
        plan = select_mutation_plan(variation_mode="exploratory")
        # 应该至少有 1 个模板
        assert len(plan.templates) >= 1


class TestBuildMutationPromptHint:
    """build_mutation_prompt_hint 测试。"""

    def test_hint_contains_template_name(self):
        """提示包含模板名称。"""
        plan = MutationPlan(
            dimensions=[MutationDimension.NARRATIVE_STRUCTURE],
            templates=[get_template("reverse_chronology")],
        )
        hint = build_mutation_prompt_hint(plan)
        assert "倒叙" in hint

    def test_hint_empty_no_templates(self):
        """无模板时返回空字符串。"""
        plan = MutationPlan(
            dimensions=[MutationDimension.ARC_THEME],
            templates=[],
        )
        hint = build_mutation_prompt_hint(plan)
        assert hint == ""

    def test_hint_contains_prompt_instruction(self):
        """提示包含创作指令。"""
        plan = MutationPlan(
            dimensions=[MutationDimension.POV_VOICE],
            templates=[get_template("unreliable_narrator")],
        )
        hint = build_mutation_prompt_hint(plan)
        assert "不可靠叙述者" in hint


class TestWriterStructuralVariation:
    """Writer think_variations 结构性变异集成测试。"""

    def test_think_variations_with_mutation(self, tmp_path):
        """think_variations 使用结构性变异。"""
        from unittest.mock import MagicMock

        from opennovel.agents.writer import Writer
        from opennovel.core.retriever import Retriever

        # Mock LLM 返回合法的大纲 JSON
        outline_json = '{"chapter_id": "ch_001", "title": "测试", "summary": "摘要", "scenes": [{"scene_id": "s1", "description": "场景", "characters_involved": ["char_001"], "emotional_tone": "紧张", "estimated_words": 1000}], "character_arcs": {}, "key_plot_points": [], "narrative_rhythm": "快", "target_words": 1000}'

        mock_bus = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = outline_json
        mock_bus.chat.return_value = mock_response

        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = ""
        mock_ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=mock_bus,
            retriever=mock_ret,
            project_root=tmp_path,
        )

        outlines = writer.think_variations(
            "ch_001", "大纲提示", n_variants=2, variation_mode="exploratory"
        )

        assert len(outlines) == 2
        # 验证 LLM 被调用了 2 次
        assert mock_bus.chat.call_count == 2
