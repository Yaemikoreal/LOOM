"""新 Schema 模块测试 - outline、evaluation、manager_update 数据模型校验。"""

import pytest
from pydantic import ValidationError

from opennovel.schemas.evaluation import ChapterEvaluation, DimensionScore
from opennovel.schemas.manager_update import CharacterUpdate, EventRecord, ManagerUpdateResult
from opennovel.schemas.outline import ChapterOutline, SceneBreakdown


class TestSceneBreakdown:
    """SceneBreakdown 数据模型测试。"""

    def test_scene_breakdown_creation(self) -> None:
        """测试正常创建 SceneBreakdown。"""
        scene = SceneBreakdown(
            scene_id="scene_001",
            description="主角在酒馆中偶遇神秘旅人",
            characters_involved=["char_001", "char_002"],
            emotional_tone="悬念",
            estimated_words=1500,
        )
        assert scene.scene_id == "scene_001"
        assert scene.description == "主角在酒馆中偶遇神秘旅人"
        assert len(scene.characters_involved) == 2
        assert scene.emotional_tone == "悬念"
        assert scene.estimated_words == 1500

    def test_scene_breakdown_empty_characters(self) -> None:
        """测试空角色列表合法。"""
        scene = SceneBreakdown(
            scene_id="scene_002",
            description="独白场景",
            characters_involved=[],
            emotional_tone="沉思",
            estimated_words=800,
        )
        assert scene.characters_involved == []

    def test_scene_breakdown_invalid_character_id(self) -> None:
        """测试角色 ID 不以 char_ 开头时校验失败。"""
        with pytest.raises(ValidationError, match="char_"):
            SceneBreakdown(
                scene_id="scene_001",
                description="测试场景",
                characters_involved=["role_001"],
                emotional_tone="紧张",
                estimated_words=1000,
            )

    def test_scene_breakdown_multiple_invalid_ids(self) -> None:
        """测试多个角色 ID 中有不合法时校验失败。"""
        with pytest.raises(ValidationError, match="char_"):
            SceneBreakdown(
                scene_id="scene_001",
                description="测试场景",
                characters_involved=["char_001", "invalid_id"],
                emotional_tone="紧张",
                estimated_words=1000,
            )


class TestChapterOutline:
    """ChapterOutline 数据模型测试。"""

    def test_chapter_outline_creation(self) -> None:
        """测试正常创建 ChapterOutline。"""
        outline = ChapterOutline(
            chapter_id="ch_001",
            title="命运的开端",
            summary="主角在酒馆中接到神秘委托，踏上冒险之旅。",
            scenes=[
                SceneBreakdown(
                    scene_id="scene_001",
                    description="酒馆偶遇",
                    characters_involved=["char_001"],
                    emotional_tone="悬念",
                    estimated_words=1500,
                ),
            ],
            character_arcs={"char_001": "从犹豫到坚定"},
            key_plot_points=["接到委托", "获得线索"],
            narrative_rhythm="慢热开场",
            target_words=3000,
        )
        assert outline.chapter_id == "ch_001"
        assert outline.title == "命运的开端"
        assert len(outline.scenes) == 1
        assert outline.target_words == 3000

    def test_chapter_outline_invalid_chapter_id(self) -> None:
        """测试章节 ID 不以 ch_ 开头时校验失败。"""
        with pytest.raises(ValidationError, match="ch_"):
            ChapterOutline(
                chapter_id="chapter_001",
                title="测试章节",
                summary="测试概要",
                scenes=[],
                character_arcs={},
                key_plot_points=[],
                narrative_rhythm="平稳",
                target_words=3000,
            )

    def test_chapter_outline_summary_too_long(self) -> None:
        """测试概要超过 500 字时校验失败。"""
        long_summary = "这是一段很长的概要。" * 60  # 超过 500 字
        with pytest.raises(ValidationError, match="500"):
            ChapterOutline(
                chapter_id="ch_001",
                title="测试章节",
                summary=long_summary,
                scenes=[],
                character_arcs={},
                key_plot_points=[],
                narrative_rhythm="平稳",
                target_words=3000,
            )

    def test_chapter_outline_summary_boundary(self) -> None:
        """测试概要恰好 500 字时合法。"""
        exact_summary = "字" * 500
        outline = ChapterOutline(
            chapter_id="ch_001",
            title="测试章节",
            summary=exact_summary,
            scenes=[],
            character_arcs={},
            key_plot_points=[],
            narrative_rhythm="平稳",
            target_words=3000,
        )
        assert len(outline.summary) == 500


class TestDimensionScore:
    """DimensionScore 数据模型测试。"""

    def test_dimension_score_creation(self) -> None:
        """测试正常创建 DimensionScore。"""
        score = DimensionScore(
            dimension="文笔",
            score=16,
            comment="描写细腻，用词精准",
        )
        assert score.dimension == "文笔"
        assert score.score == 16
        assert score.comment == "描写细腻，用词精准"

    def test_dimension_score_boundary_zero(self) -> None:
        """测试分数下边界 0 合法。"""
        score = DimensionScore(dimension="情节", score=0, comment="需要大幅改进")
        assert score.score == 0

    def test_dimension_score_boundary_twenty(self) -> None:
        """测试分数上边界 20 合法。"""
        score = DimensionScore(dimension="文笔", score=20, comment="完美")
        assert score.score == 20

    def test_dimension_score_above_range(self) -> None:
        """测试分数超过 20 时校验失败。"""
        with pytest.raises(ValidationError, match="0-20"):
            DimensionScore(dimension="文笔", score=21, comment="超出范围")

    def test_dimension_score_below_range(self) -> None:
        """测试分数低于 0 时校验失败。"""
        with pytest.raises(ValidationError, match="0-20"):
            DimensionScore(dimension="文笔", score=-1, comment="负分")


class TestChapterEvaluation:
    """ChapterEvaluation 数据模型测试。"""

    def _make_valid_dimensions(self) -> list[DimensionScore]:
        """创建合法的 5 维度评分列表。"""
        return [
            DimensionScore(dimension="文笔", score=16, comment="优秀"),
            DimensionScore(dimension="情节", score=15, comment="良好"),
            DimensionScore(dimension="人物", score=17, comment="生动"),
            DimensionScore(dimension="节奏", score=14, comment="适中"),
            DimensionScore(dimension="设定", score=16, comment="合理"),
        ]

    def test_chapter_evaluation_creation(self) -> None:
        """测试正常创建 ChapterEvaluation。"""
        eval_result = ChapterEvaluation(
            total_score=82,
            dimensions=self._make_valid_dimensions(),
            summary="整体质量良好",
            issues=["节奏稍慢"],
            suggestions=["增加冲突"],
        )
        assert eval_result.total_score == 82
        assert len(eval_result.dimensions) == 5
        assert eval_result.is_pass is True
        assert eval_result.is_excellent is False

    def test_chapter_evaluation_total_score_above_range(self) -> None:
        """测试总分超过 100 时校验失败。"""
        with pytest.raises(ValidationError, match="0-100"):
            ChapterEvaluation(
                total_score=101,
                dimensions=self._make_valid_dimensions(),
                summary="测试",
                issues=[],
                suggestions=[],
            )

    def test_chapter_evaluation_total_score_below_range(self) -> None:
        """测试总分低于 0 时校验失败。"""
        with pytest.raises(ValidationError, match="0-100"):
            ChapterEvaluation(
                total_score=-1,
                dimensions=self._make_valid_dimensions(),
                summary="测试",
                issues=[],
                suggestions=[],
            )

    def test_chapter_evaluation_wrong_dimensions_count(self) -> None:
        """测试维度数量不是 5 时校验失败。"""
        too_few = [
            DimensionScore(dimension="文笔", score=16, comment="优秀"),
            DimensionScore(dimension="情节", score=15, comment="良好"),
        ]
        with pytest.raises(ValidationError, match="5 个维度"):
            ChapterEvaluation(
                total_score=80,
                dimensions=too_few,
                summary="测试",
                issues=[],
                suggestions=[],
            )

    def test_chapter_evaluation_is_pass_property(self) -> None:
        """测试 is_pass 属性（>=80 分合格）。"""
        eval_79 = ChapterEvaluation(
            total_score=79,
            dimensions=self._make_valid_dimensions(),
            summary="接近合格",
            issues=[],
            suggestions=[],
        )
        assert eval_79.is_pass is False

        eval_80 = ChapterEvaluation(
            total_score=80,
            dimensions=self._make_valid_dimensions(),
            summary="刚好合格",
            issues=[],
            suggestions=[],
        )
        assert eval_80.is_pass is True

    def test_chapter_evaluation_is_excellent_property(self) -> None:
        """测试 is_excellent 属性（>=90 分优秀）。"""
        eval_89 = ChapterEvaluation(
            total_score=89,
            dimensions=self._make_valid_dimensions(),
            summary="接近优秀",
            issues=[],
            suggestions=[],
        )
        assert eval_89.is_excellent is False

        eval_90 = ChapterEvaluation(
            total_score=90,
            dimensions=self._make_valid_dimensions(),
            summary="刚好优秀",
            issues=[],
            suggestions=[],
        )
        assert eval_90.is_excellent is True

    def test_chapter_evaluation_boundary_zero(self) -> None:
        """测试总分下边界 0 合法。"""
        eval_result = ChapterEvaluation(
            total_score=0,
            dimensions=self._make_valid_dimensions(),
            summary="最低分",
            issues=[],
            suggestions=[],
        )
        assert eval_result.total_score == 0
        assert eval_result.is_pass is False
        assert eval_result.is_excellent is False

    def test_chapter_evaluation_boundary_hundred(self) -> None:
        """测试总分上边界 100 合法。"""
        eval_result = ChapterEvaluation(
            total_score=100,
            dimensions=self._make_valid_dimensions(),
            summary="满分",
            issues=[],
            suggestions=[],
        )
        assert eval_result.total_score == 100
        assert eval_result.is_pass is True
        assert eval_result.is_excellent is True


class TestCharacterUpdate:
    """CharacterUpdate 数据模型测试。"""

    def test_character_update_creation(self) -> None:
        """测试正常创建 CharacterUpdate。"""
        update = CharacterUpdate(
            character_id="char_001",
            field="physical.injuries",
            value=["left_arm_fracture"],
            reason="战斗中左臂受伤",
        )
        assert update.character_id == "char_001"
        assert update.field == "physical.injuries"
        assert update.value == ["left_arm_fracture"]
        assert update.reason == "战斗中左臂受伤"

    def test_character_update_invalid_id(self) -> None:
        """测试角色 ID 不以 char_ 开头时校验失败。"""
        with pytest.raises(ValidationError, match="char_"):
            CharacterUpdate(
                character_id="role_001",
                field="emotional.grief",
                value=0.5,
                reason="失去同伴",
            )

    def test_character_update_empty_id(self) -> None:
        """测试空角色 ID 时校验失败。"""
        with pytest.raises(ValidationError, match="char_"):
            CharacterUpdate(
                character_id="",
                field="emotional.grief",
                value=0.5,
                reason="测试",
            )


class TestEventRecord:
    """EventRecord 数据模型测试。"""

    def test_event_record_creation(self) -> None:
        """测试正常创建 EventRecord。"""
        event = EventRecord(
            event_id="evt_ch001_001",
            character_id="char_001",
            event_type="INJURY",
            description="左臂骨折",
            causal_pressure=0.8,
            timestamp="第3天·午后",
        )
        assert event.event_id == "evt_ch001_001"
        assert event.character_id == "char_001"
        assert event.event_type == "INJURY"
        assert event.causal_pressure == 0.8

    def test_event_record_causal_pressure_boundary_zero(self) -> None:
        """测试因果压强下边界 0.0 合法。"""
        event = EventRecord(
            event_id="evt_001",
            character_id="char_001",
            event_type="ITEM_GAIN",
            description="获得普通物品",
            causal_pressure=0.0,
            timestamp="第1天",
        )
        assert event.causal_pressure == 0.0

    def test_event_record_causal_pressure_boundary_one(self) -> None:
        """测试因果压强上边界 1.0 合法。"""
        event = EventRecord(
            event_id="evt_001",
            character_id="char_001",
            event_type="KNOWLEDGE",
            description="得知关键秘密",
            causal_pressure=1.0,
            timestamp="第5天",
        )
        assert event.causal_pressure == 1.0

    def test_event_record_causal_pressure_above_range(self) -> None:
        """测试因果压强超过 1.0 时校验失败。"""
        with pytest.raises(ValidationError, match="0.0-1.0"):
            EventRecord(
                event_id="evt_001",
                character_id="char_001",
                event_type="INJURY",
                description="测试",
                causal_pressure=1.1,
                timestamp="第1天",
            )

    def test_event_record_causal_pressure_below_range(self) -> None:
        """测试因果压强低于 0.0 时校验失败。"""
        with pytest.raises(ValidationError, match="0.0-1.0"):
            EventRecord(
                event_id="evt_001",
                character_id="char_001",
                event_type="INJURY",
                description="测试",
                causal_pressure=-0.1,
                timestamp="第1天",
            )

    def test_event_record_invalid_character_id(self) -> None:
        """测试角色 ID 不以 char_ 开头时校验失败。"""
        with pytest.raises(ValidationError, match="char_"):
            EventRecord(
                event_id="evt_001",
                character_id="role_001",
                event_type="INJURY",
                description="测试",
                causal_pressure=0.5,
                timestamp="第1天",
            )


class TestManagerUpdateResult:
    """ManagerUpdateResult 数据模型测试。"""

    def test_manager_update_result_creation(self) -> None:
        """测试正常创建 ManagerUpdateResult。"""
        result = ManagerUpdateResult(
            character_updates=[
                CharacterUpdate(
                    character_id="char_001",
                    field="physical.injuries",
                    value=["left_arm_fracture"],
                    reason="战斗受伤",
                ),
            ],
            events=[
                EventRecord(
                    event_id="evt_ch001_001",
                    character_id="char_001",
                    event_type="INJURY",
                    description="左臂骨折",
                    causal_pressure=0.8,
                    timestamp="第3天·午后",
                ),
            ],
            chapter_summary="主角在战斗中受伤，但获得了重要线索。",
        )
        assert len(result.character_updates) == 1
        assert len(result.events) == 1
        assert "战斗" in result.chapter_summary

    def test_manager_update_result_empty_lists(self) -> None:
        """测试空列表合法（无更新、无事件）。"""
        result = ManagerUpdateResult(
            character_updates=[],
            events=[],
            chapter_summary="平静的一天，没有发生任何事件。",
        )
        assert result.character_updates == []
        assert result.events == []
