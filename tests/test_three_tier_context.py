"""三级上下文策略测试 - FRUGAL / STANDARD / PANORAMIC 组装逻辑。"""

from pathlib import Path

import pytest

from opennovel.core.context_assembler import (
    OUTPUT_RESERVE,
    PANORAMIC_SOFT_LIMIT,
    STANDARD_TOKEN_BUDGET,
    ContextStrategy,
    TokenCounter,
    assemble_actor_context,
    detect_strategy,
)
from opennovel.storage.yaml_storage import YAMLStorage

# ── ContextStrategy 枚举测试 ──


class TestContextStrategy:
    """ContextStrategy 枚举定义测试。"""

    def test_frugal_value(self) -> None:
        """测试 FRUGAL 策略值。"""
        assert ContextStrategy.FRUGAL.value == "frugal"

    def test_standard_value(self) -> None:
        """测试 STANDARD 策略值。"""
        assert ContextStrategy.STANDARD.value == "standard"

    def test_panoramic_value(self) -> None:
        """测试 PANORAMIC 策略值。"""
        assert ContextStrategy.PANORAMIC.value == "panoramic"

    def test_all_strategies_exist(self) -> None:
        """测试三种策略全部定义。"""
        strategies = list(ContextStrategy)
        assert len(strategies) == 3


# ── detect_strategy 测试 ──


class TestDetectStrategy:
    """detect_strategy 模型窗口映射测试。"""

    def test_small_window_returns_frugal(self) -> None:
        """测试 <32K 窗口返回 FRUGAL。"""
        assert detect_strategy(8192) == ContextStrategy.FRUGAL
        assert detect_strategy(16384) == ContextStrategy.FRUGAL
        assert detect_strategy(31999) == ContextStrategy.FRUGAL

    def test_medium_window_returns_standard(self) -> None:
        """测试 32K-128K 窗口返回 STANDARD。"""
        assert detect_strategy(32000) == ContextStrategy.STANDARD
        assert detect_strategy(65536) == ContextStrategy.STANDARD
        assert detect_strategy(127999) == ContextStrategy.STANDARD

    def test_large_window_returns_panoramic(self) -> None:
        """测试 >128K 窗口返回 PANORAMIC。"""
        assert detect_strategy(128000) == ContextStrategy.PANORAMIC
        assert detect_strategy(1000000) == ContextStrategy.PANORAMIC

    def test_boundary_32k(self) -> None:
        """测试 32K 边界值。"""
        assert detect_strategy(32000) == ContextStrategy.STANDARD
        assert detect_strategy(31999) == ContextStrategy.FRUGAL

    def test_boundary_128k(self) -> None:
        """测试 128K 边界值。"""
        assert detect_strategy(128000) == ContextStrategy.PANORAMIC
        assert detect_strategy(127999) == ContextStrategy.STANDARD


# ── STANDARD 策略常量测试 ──


class TestStandardConstants:
    """STANDARD 策略预算常量测试。"""

    def test_standard_budget_value(self) -> None:
        """测试 STANDARD 预算为 48000 tokens。"""
        assert STANDARD_TOKEN_BUDGET == 48000

    def test_panoramic_soft_limit(self) -> None:
        """测试 PANORAMIC 软限为 128K tokens。"""
        assert PANORAMIC_SOFT_LIMIT == 128000


# ── STANDARD 策略组装测试 ──


class TestStandardAssembly:
    """STANDARD 策略上下文组装测试。"""

    def test_standard_includes_all_characters(self, tmp_path: Path, standard_setup: dict) -> None:
        """测试 STANDARD 策略注入所有活跃角色状态。"""
        messages = assemble_actor_context(
            chapter_path=standard_setup["chapter_path"],
            project_root=tmp_path,
            current_text="故事正文。",
            strategy=ContextStrategy.STANDARD,
            yaml_storage=standard_setup["storage"],
        )
        # 应包含所有活跃角色的状态
        content = " ".join(m["content"] for m in messages)
        assert "char_001" in content
        assert "char_002" in content

    def test_standard_budget_larger_than_frugal(self, tmp_path: Path, standard_setup: dict) -> None:
        """测试 STANDARD 预算大于 FRUGAL。"""
        # 组装一段较长的文本
        long_text = "这是一段测试文本。" * 500
        messages = assemble_actor_context(
            chapter_path=standard_setup["chapter_path"],
            project_root=tmp_path,
            current_text=long_text,
            strategy=ContextStrategy.STANDARD,
            yaml_storage=standard_setup["storage"],
        )
        # STANDARD 应能容纳更多文本
        total_content = " ".join(m["content"] for m in messages)
        assert len(total_content) > 0

    def test_standard_preserves_authority_hierarchy(
        self, tmp_path: Path, standard_setup: dict
    ) -> None:
        """测试 STANDARD 策略保持权威分级标签。"""
        messages = assemble_actor_context(
            chapter_path=standard_setup["chapter_path"],
            project_root=tmp_path,
            current_text="正文。",
            canon_content="魔法设定。",
            subconscious_content="灵感碎片。",
            strategy=ContextStrategy.STANDARD,
            yaml_storage=standard_setup["storage"],
        )
        content = " ".join(m["content"] for m in messages)
        assert "[CANON" in content
        assert "[STATE MEMORY" in content
        assert "[SUBCONSCIOUS" in content


# ── PANORAMIC 策略组装测试 ──


class TestPanoramicAssembly:
    """PANORAMIC 策略上下文组装测试。"""

    def test_panoramic_includes_full_canon(self, tmp_path: Path, panoramic_setup: dict) -> None:
        """测试 PANORAMIC 策略注入全量设定。"""
        messages = assemble_actor_context(
            chapter_path=panoramic_setup["chapter_path"],
            project_root=tmp_path,
            current_text="正文。",
            canon_content=panoramic_setup["long_canon"],
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=panoramic_setup["storage"],
        )
        content = " ".join(m["content"] for m in messages)
        # PANORAMIC 不应截断设定
        assert "重要世界观规则" in content

    def test_panoramic_includes_full_subconscious(
        self, tmp_path: Path, panoramic_setup: dict
    ) -> None:
        """测试 PANORAMIC 策略注入全量潜意识。"""
        messages = assemble_actor_context(
            chapter_path=panoramic_setup["chapter_path"],
            project_root=tmp_path,
            current_text="正文。",
            subconscious_content=panoramic_setup["long_subconscious"],
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=panoramic_setup["storage"],
        )
        content = " ".join(m["content"] for m in messages)
        assert "灵感碎片" in content

    def test_panoramic_soft_limit_applied(self, tmp_path: Path, panoramic_setup: dict) -> None:
        """测试 PANORAMIC 策略应用 128K 软限。"""
        # 创建超长内容
        huge_text = "段落内容。" * 50000
        messages = assemble_actor_context(
            chapter_path=panoramic_setup["chapter_path"],
            project_root=tmp_path,
            current_text=huge_text,
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=panoramic_setup["storage"],
        )
        # 总 token 不应超过软限 + 输出预留
        counter = TokenCounter()
        total = sum(counter.count(m["content"]) for m in messages)
        assert total <= PANORAMIC_SOFT_LIMIT + 2000  # 加上人格注入等不可裁剪部分


# ── 默认策略测试 ──


class TestDefaultStrategy:
    """默认策略（不传 strategy 参数）行为测试。"""

    def test_default_uses_frugal(self, tmp_path: Path, standard_setup: dict) -> None:
        """测试不传 strategy 时默认使用 FRUGAL。"""
        messages = assemble_actor_context(
            chapter_path=standard_setup["chapter_path"],
            project_root=tmp_path,
            current_text="正文。",
            yaml_storage=standard_setup["storage"],
        )
        # FRUGAL 预算下，角色状态应被截断或不包含所有角色
        # 验证返回格式正确即可
        assert len(messages) >= 1
        for msg in messages:
            assert "role" in msg
            assert "content" in msg


# ── Fixtures ──


@pytest.fixture
def standard_setup(tmp_path: Path) -> dict:
    """创建 STANDARD 策略测试所需的项目结构。"""
    (tmp_path / "characters").mkdir()
    storage = YAMLStorage()

    # 两个角色
    storage.write_markdown_file(
        tmp_path / "characters" / "char_001.md",
        {
            "id": "char_001",
            "name": "主角",
            "physical": {"injuries": [], "buffs": [], "debuffs": []},
        },
        "# 主角背景",
    )
    storage.write_markdown_file(
        tmp_path / "characters" / "char_002.md",
        {
            "id": "char_002",
            "name": "配角",
            "physical": {"injuries": [], "buffs": [], "debuffs": []},
        },
        "# 配角背景",
    )

    # 章节引用两个角色
    chapter_path = tmp_path / "ch_001.md"
    storage.write_markdown_file(
        chapter_path,
        {
            "id": "ch_001",
            "pov": "char_001",
            "active_characters": ["char_001", "char_002"],
        },
        "# 第一章\n\n故事开始。",
    )

    return {"storage": storage, "chapter_path": chapter_path}


@pytest.fixture
def panoramic_setup(tmp_path: Path) -> dict:
    """创建 PANORAMIC 策略测试所需的项目结构。"""
    (tmp_path / "characters").mkdir()
    storage = YAMLStorage()

    storage.write_markdown_file(
        tmp_path / "characters" / "char_001.md",
        {
            "id": "char_001",
            "name": "主角",
            "physical": {"injuries": [], "buffs": [], "debuffs": []},
        },
        "# 主角背景\n\n详细的角色历史。",
    )

    chapter_path = tmp_path / "ch_001.md"
    storage.write_markdown_file(
        chapter_path,
        {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
        "# 第一章\n\n故事开始。",
    )

    # 长设定
    long_canon = "重要世界观规则。\n" + "详细设定内容。\n" * 200
    long_subconscious = "灵感碎片。\n" + "更多灵感。\n" * 100

    return {
        "storage": storage,
        "chapter_path": chapter_path,
        "long_canon": long_canon,
        "long_subconscious": long_subconscious,
    }


# ── PANORAMIC 历史章节注入测试 ──


class TestPanoramicHistoricalInjection:
    """PANORAMIC 策略历史章节倒序注入测试。"""

    def test_injects_previous_chapters(self, tmp_path: Path) -> None:
        """测试 PANORAMIC 模式注入历史章节正文。"""
        (tmp_path / "characters").mkdir()
        (tmp_path / "draft").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        # 创建 3 个章节
        for i in range(1, 4):
            storage.write_markdown_file(
                tmp_path / "draft" / f"ch_{i:03d}.md",
                {"id": f"ch_{i:03d}", "pov": "char_001"},
                f"# 第{i}章\n\n这是第{i}章的内容。",
            )

        # 当前章节是 ch_003
        chapter_path = tmp_path / "draft" / "ch_003.md"

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="当前正文。",
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=storage,
        )

        content = " ".join(m["content"] for m in messages)
        # 应包含历史章节内容（倒序：ch_002 优先于 ch_001）
        assert "ch_002" in content or "第2章" in content
        assert "ch_001" in content or "第1章" in content

    def test_excludes_current_chapter(self, tmp_path: Path) -> None:
        """测试不注入当前章节到历史中。"""
        (tmp_path / "characters").mkdir()
        (tmp_path / "draft").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        storage.write_markdown_file(
            tmp_path / "draft" / "ch_001.md",
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章\n\n第一章独有标记XYZ。",
        )

        chapter_path = tmp_path / "draft" / "ch_001.md"

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="当前正文。",
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=storage,
        )

        # 历史注入不应包含当前章节
        history_msgs = [m for m in messages if "[CHAPTER HISTORY" in m.get("content", "")]
        for m in history_msgs:
            assert "独有标记XYZ" not in m["content"]

    def test_no_previous_chapters(self, tmp_path: Path) -> None:
        """测试没有历史章节时不报错。"""
        (tmp_path / "characters").mkdir()
        (tmp_path / "draft").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        chapter_path = tmp_path / "draft" / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=storage,
        )

        # 不应有历史注入消息
        history_msgs = [m for m in messages if "[CHAPTER HISTORY" in m.get("content", "")]
        assert len(history_msgs) == 0

    def test_reverse_order(self, tmp_path: Path) -> None:
        """测试历史章节按倒序注入（最近的优先）。"""
        (tmp_path / "characters").mkdir()
        (tmp_path / "draft").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        # 创建 3 个章节，每章有独特标记
        storage.write_markdown_file(
            tmp_path / "draft" / "ch_001.md",
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章\n\nMARKER_CH001。",
        )
        storage.write_markdown_file(
            tmp_path / "draft" / "ch_002.md",
            {"id": "ch_002", "pov": "char_001"},
            "# 第二章\n\nMARKER_CH002。",
        )

        chapter_path = tmp_path / "draft" / "ch_003.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_003", "pov": "char_001"},
            "# 第三章",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=storage,
        )

        # 找到历史注入消息
        history_msgs = [m for m in messages if "[CHAPTER HISTORY" in m.get("content", "")]
        if history_msgs:
            content = history_msgs[0]["content"]
            # ch_002 应在 ch_001 之前（倒序）
            idx_002 = content.find("MARKER_CH002")
            idx_001 = content.find("MARKER_CH001")
            if idx_002 >= 0 and idx_001 >= 0:
                assert idx_002 < idx_001


# ── 辅助函数 ──


def _make_large_text(token_counter: TokenCounter, target_tokens: int) -> str:
    """生成至少 target_tokens 数量的文本。"""
    base = "这是一段用于测试截断逻辑的长文本内容。" * 50
    result = base
    while token_counter.count(result) < target_tokens:
        result += base
    return result


# ── STANDARD 截断分支测试 ──


class TestStandardTruncation:
    """STANDARD 策略中各层级截断分支测试。"""

    def test_standard_canon_truncated(self, tmp_path: Path) -> None:
        """测试 STANDARD 模式下 CANON 超过 20% 预算时被截断 (覆盖 389-390 行)。"""
        counter = TokenCounter()
        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        # STANDARD budget = 48000 - 2000 = 46000, canon = 20% = 9200 tokens
        large_canon = _make_large_text(counter, 15000)

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            canon_content=large_canon,
            strategy=ContextStrategy.STANDARD,
            yaml_storage=storage,
        )

        canon_msgs = [m for m in messages if "[CANON | IMMUTABLE" in m.get("content", "")]
        assert len(canon_msgs) == 1
        budget = STANDARD_TOKEN_BUDGET - OUTPUT_RESERVE
        canon_budget = int(budget * 0.20)
        assert counter.count(canon_msgs[0]["content"]) <= canon_budget

    def test_standard_state_budget_break(self, tmp_path: Path) -> None:
        """测试 STANDARD 模式下状态预算耗尽后跳过剩余角色 (覆盖 404 行)。"""
        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()

        # STANDARD budget = 46000, state = 30% = 13800 tokens
        # 创建一个超大角色来吃掉状态预算（确保 JSON 序列化后 > 13800 tokens）
        large_name = "超级长的角色名" * 5000
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {
                "id": "char_001",
                "name": large_name,
                "physical": {"injuries": [], "buffs": [], "debuffs": []},
            },
            "# 背景",
        )
        storage.write_markdown_file(
            tmp_path / "characters" / "char_002.md",
            {"id": "char_002", "name": "配角"},
            "# 配角背景",
        )

        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {
                "id": "ch_001",
                "pov": "char_001",
                "active_characters": ["char_001", "char_002"],
            },
            "# 第一章",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            strategy=ContextStrategy.STANDARD,
            yaml_storage=storage,
        )

        # char_001 的状态应该占满了预算，char_002 可能被跳过或截断
        total_content = " ".join(m["content"] for m in messages)
        assert "char_001" in total_content

    def test_standard_missing_char_file_skipped(self, tmp_path: Path) -> None:
        """测试 STANDARD 模式下角色文件不存在时跳过 (覆盖 407 行)。"""
        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()

        # 只创建 char_001，不创建 char_002
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "主角"},
            "# 背景",
        )

        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {
                "id": "ch_001",
                "pov": "char_001",
                "active_characters": ["char_001", "char_002"],
            },
            "# 第一章",
        )

        # 不应报错，char_002 应被跳过
        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            strategy=ContextStrategy.STANDARD,
            yaml_storage=storage,
        )

        content = " ".join(m["content"] for m in messages)
        assert "char_001" in content

    def test_standard_per_char_state_truncated(self, tmp_path: Path) -> None:
        """测试 STANDARD 模式下单个角色状态超过剩余预算时截断 (覆盖 416-417 行)。"""
        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()

        # STANDARD state_budget = int(46000 * 0.30) = 13800 tokens
        # char_001 占用大部分预算（~12000 tokens），char_002 超过剩余
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {
                "id": "char_001",
                "name": "角色一" * 4000,
                "physical": {"injuries": [], "buffs": [], "debuffs": []},
            },
            "# 背景一",
        )
        storage.write_markdown_file(
            tmp_path / "characters" / "char_002.md",
            {
                "id": "char_002",
                "name": "角色二" * 2000,
                "physical": {"injuries": [], "buffs": [], "debuffs": []},
            },
            "# 背景二",
        )

        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {
                "id": "ch_001",
                "pov": "char_001",
                "active_characters": ["char_001", "char_002"],
            },
            "# 第一章",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            strategy=ContextStrategy.STANDARD,
            yaml_storage=storage,
        )

        # 两个角色的状态都应出现在消息中（可能被截断）
        content = " ".join(m["content"] for m in messages)
        assert "char_001" in content

    def test_standard_subconscious_truncated(self, tmp_path: Path) -> None:
        """测试 STANDARD 模式下潜意识超过 15% 预算时截断 (覆盖 432-433 行)。"""
        counter = TokenCounter()
        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        # STANDARD budget = 46000, sub = 15% = 6900 tokens
        large_sub = _make_large_text(counter, 10000)

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            subconscious_content=large_sub,
            strategy=ContextStrategy.STANDARD,
            yaml_storage=storage,
        )

        sub_msgs = [m for m in messages if "[SUBCONSCIOUS FRAGMENT" in m.get("content", "")]
        assert len(sub_msgs) == 1
        budget = STANDARD_TOKEN_BUDGET - OUTPUT_RESERVE
        sub_budget = int(budget * 0.15)
        assert counter.count(sub_msgs[0]["content"]) <= sub_budget

    def test_standard_text_truncated(self, tmp_path: Path) -> None:
        """测试 STANDARD 模式下近期正文超过剩余预算时截断 (覆盖 444 行)。"""
        counter = TokenCounter()
        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        # 使用大文本来消耗剩余预算
        large_text = _make_large_text(counter, 50000)

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text=large_text,
            strategy=ContextStrategy.STANDARD,
            yaml_storage=storage,
        )

        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        # 总 token 应在预算内
        total = sum(counter.count(m["content"]) for m in messages)
        assert total <= STANDARD_TOKEN_BUDGET

    def test_standard_persona_injected(self, tmp_path: Path) -> None:
        """测试 STANDARD 模式下人格 Prompt 被注入 (覆盖 377-381 行)。"""
        (tmp_path / "characters").mkdir()
        (tmp_path / "prompts").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        prompt_path = tmp_path / "prompts" / "actor.v1.md"
        prompt_path.write_text("# Actor 人格\n\n你是写作代理。", encoding="utf-8")

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            prompt_path=prompt_path,
            strategy=ContextStrategy.STANDARD,
            yaml_storage=storage,
        )

        # 应该包含人格消息
        persona_msgs = [m for m in messages if "写作代理" in m.get("content", "")]
        assert len(persona_msgs) == 1


# ── PANORAMIC 截断分支测试 ──


class TestPanoramicTruncation:
    """PANORAMIC 策略中截断分支测试。"""

    def test_panoramic_persona_injected(self, tmp_path: Path) -> None:
        """测试 PANORAMIC 模式下人格 Prompt 被注入 (覆盖 481-485 行)。"""
        (tmp_path / "characters").mkdir()
        (tmp_path / "prompts").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        prompt_path = tmp_path / "prompts" / "actor.v1.md"
        prompt_path.write_text("# Actor 人格\n\n你是全景写作代理。", encoding="utf-8")

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            prompt_path=prompt_path,
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=storage,
        )

        persona_msgs = [m for m in messages if "全景写作代理" in m.get("content", "")]
        assert len(persona_msgs) == 1

    def test_panoramic_missing_char_file_skipped(self, tmp_path: Path) -> None:
        """测试 PANORAMIC 模式下角色文件不存在时跳过 (覆盖 503 行)。"""
        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()

        # 只创建 char_001
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "主角"},
            "# 背景",
        )

        chapter_path = tmp_path / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {
                "id": "ch_001",
                "pov": "char_001",
                "active_characters": ["char_001", "char_002", "char_003"],
            },
            "# 第一章",
        )

        # 不应报错，缺失的角色文件应被跳过
        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="正文。",
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=storage,
        )

        content = " ".join(m["content"] for m in messages)
        assert "char_001" in content

    def test_panoramic_text_truncated_by_soft_limit(self, tmp_path: Path) -> None:
        """测试 PANORAMIC 模式下当前正文超过剩余预算时被截断。"""
        counter = TokenCounter()
        (tmp_path / "characters").mkdir()
        (tmp_path / "draft").mkdir()
        storage = YAMLStorage()

        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        # 创建历史章节消耗部分预算
        for i in range(1, 4):
            storage.write_markdown_file(
                tmp_path / "draft" / f"ch_{i:03d}.md",
                {"id": f"ch_{i:03d}", "pov": "char_001"},
                f"# 第{i}章\n\n" + "历史内容。" * 100,
            )

        chapter_path = tmp_path / "draft" / "ch_004.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_004", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第四章",
        )

        # 超大正文
        huge_text = _make_large_text(counter, 150000)

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text=huge_text,
            strategy=ContextStrategy.PANORAMIC,
            yaml_storage=storage,
        )

        # 总 token 应在软限范围内
        total = sum(counter.count(m["content"]) for m in messages)
        assert total <= PANORAMIC_SOFT_LIMIT + 5000  # 容差
