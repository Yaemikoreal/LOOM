"""context_assembler 模块测试 - Token 计数、权威组装、熔断机制。"""

from pathlib import Path

import pytest

from opennovel.core.context_assembler import (
    BUDGET_RATIOS,
    INPUT_TOKEN_BUDGET,
    TOTAL_TOKEN_BUDGET,
    TRUNCATION_ORDER,
    ContextMessage,
    ContextStrategy,
    TokenCounter,
    _apply_circuit_breaker,
    _load_previous_chapters,
    assemble_actor_context,
    load_prompt,
    wrap_with_authority_tag,
)
from opennovel.schemas.character import AuthorityLevel
from opennovel.storage.yaml_storage import YAMLStorage

# ── TokenCounter 测试 ──


class TestTokenCounter:
    """TokenCounter 基础功能测试。"""

    def test_default_encoding(self) -> None:
        """测试默认编码模型。"""
        counter = TokenCounter()
        assert counter._encoding is not None

    def test_count_simple_text(self) -> None:
        """测试简单文本计数。"""
        counter = TokenCounter()
        count = counter.count("hello world")
        assert count > 0
        assert isinstance(count, int)

    def test_count_empty_text(self) -> None:
        """测试空文本计数为 0。"""
        counter = TokenCounter()
        assert counter.count("") == 0

    def test_count_chinese_text(self) -> None:
        """测试中文文本计数。"""
        counter = TokenCounter()
        count = counter.count("你好世界")
        assert count > 0

    def test_truncate_short_text_unchanged(self) -> None:
        """测试短文本不被截断。"""
        counter = TokenCounter()
        result = counter.truncate_to_budget("hello", max_tokens=100)
        assert result == "hello"

    def test_truncate_long_text(self) -> None:
        """测试长文本被截断。"""
        counter = TokenCounter()
        text = "hello world " * 100
        result = counter.truncate_to_budget(text, max_tokens=5)
        assert counter.count(result) <= 5

    def test_unknown_encoding_fallback(self) -> None:
        """测试未知编码模型回退到 cl100k_base。"""
        counter = TokenCounter(model="nonexistent_model")
        assert counter._encoding is not None
        assert counter.count("test") > 0


# ── ContextMessage 测试 ──


class TestContextMessage:
    """ContextMessage 数据类测试。"""

    def test_creation_with_authority(self) -> None:
        """测试带权威层级的消息创建。"""
        msg = ContextMessage(
            role="system",
            content="测试内容",
            authority=AuthorityLevel.CANON,
        )
        assert msg.role == "system"
        assert msg.content == "测试内容"
        assert msg.authority == AuthorityLevel.CANON
        assert msg.token_count == 0

    def test_creation_without_authority(self) -> None:
        """测试无权威层级的消息创建（人格注入）。"""
        msg = ContextMessage(role="system", content="人格 Prompt")
        assert msg.authority is None

    def test_to_dict(self) -> None:
        """测试转换为 LLM API 格式。"""
        msg = ContextMessage(role="user", content="续写锚点")
        d = msg.to_dict()
        assert d == {"role": "user", "content": "续写锚点"}


# ── load_prompt 测试 ──


class TestLoadPrompt:
    """load_prompt 函数测试。"""

    def test_load_existing_prompt(self, tmp_path: Path) -> None:
        """测试加载存在的 Prompt 文件。"""
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text("# 测试 Prompt\n\n你是测试代理。", encoding="utf-8")

        content = load_prompt(prompt_file)
        assert "测试 Prompt" in content
        assert "测试代理" in content

    def test_load_nonexistent_prompt(self, tmp_path: Path) -> None:
        """测试加载不存在的 Prompt 文件抛出异常。"""
        with pytest.raises(FileNotFoundError):
            load_prompt(tmp_path / "nonexistent.md")


# ── wrap_with_authority_tag 测试 ──


class TestWrapWithAuthorityTag:
    """wrap_with_authority_tag 函数测试。"""

    def test_canon_tag(self) -> None:
        """测试 CANON 权威标签。"""
        result = wrap_with_authority_tag("设定内容", AuthorityLevel.CANON)
        assert "[CANON | IMMUTABLE | HIGH AUTHORITY]" in result
        assert "设定内容" in result

    def test_state_memory_tag(self) -> None:
        """测试 STATE_MEMORY 权威标签。"""
        result = wrap_with_authority_tag("状态内容", AuthorityLevel.STATE_MEMORY)
        assert "[STATE MEMORY | MEDIUM AUTHORITY]" in result

    def test_subconscious_tag(self) -> None:
        """测试 SUBCONSCIOUS 权威标签。"""
        result = wrap_with_authority_tag("灵感内容", AuthorityLevel.SUBCONSCIOUS)
        assert "[SUBCONSCIOUS FRAGMENT | LOW AUTHORITY | OPTIONAL]" in result


# ── 常量测试 ──


class TestConstants:
    """预算常量测试。"""

    def test_token_budget_values(self) -> None:
        """测试 Token 预算常量合理性。"""
        assert TOTAL_TOKEN_BUDGET == 8000
        assert INPUT_TOKEN_BUDGET == 6000  # 8000 - 2000

    def test_budget_ratios_sum(self) -> None:
        """测试各层级预算占比总和为 1.0。"""
        total = sum(BUDGET_RATIOS.values())
        assert abs(total - 1.0) < 0.001

    def test_truncation_order(self) -> None:
        """测试裁剪从低权威到高权威。"""
        assert TRUNCATION_ORDER == [
            AuthorityLevel.SUBCONSCIOUS,
            AuthorityLevel.STATE_MEMORY,
            AuthorityLevel.CANON,
        ]


# ── _apply_circuit_breaker 测试 ──


class TestCircuitBreaker:
    """_apply_circuit_breaker 熔断机制测试。"""

    def test_no_truncation_under_budget(self) -> None:
        """测试未超限时不裁剪。"""
        counter = TokenCounter()
        messages = [
            ContextMessage(role="system", content="短文本", authority=AuthorityLevel.CANON),
        ]
        messages[0].token_count = counter.count("短文本")

        result = _apply_circuit_breaker(messages, counter, budget=10000)
        assert len(result) == 1
        assert result[0].content == "短文本"

    def test_truncates_subconscious_first(self) -> None:
        """测试超限时优先裁剪 SUBCONSCIOUS。"""
        counter = TokenCounter()

        # 创建三个层级的消息，总 token 超限
        canon_text = "A" * 500
        state_text = "B" * 500
        sub_text = "C" * 500

        messages = [
            ContextMessage(role="system", content=canon_text, authority=AuthorityLevel.CANON),
            ContextMessage(
                role="system", content=state_text, authority=AuthorityLevel.STATE_MEMORY
            ),
            ContextMessage(role="system", content=sub_text, authority=AuthorityLevel.SUBCONSCIOUS),
        ]
        for m in messages:
            m.token_count = counter.count(m.content)

        total_tokens = sum(m.token_count for m in messages)
        # 设置预算刚好够 CANON + STATE，不够 SUBCONSCIOUS
        budget = total_tokens - counter.count(sub_text) + 10

        result = _apply_circuit_breaker(messages, counter, budget=budget)

        # SUBCONSCIOUS 应被裁剪
        sub_msg = [m for m in result if m.authority == AuthorityLevel.SUBCONSCIOUS]
        if sub_msg:
            assert sub_msg[0].token_count < counter.count(sub_text)

    def test_preserves_none_authority(self) -> None:
        """测试人格注入（authority=None）不被裁剪。"""
        counter = TokenCounter()
        messages = [
            ContextMessage(role="system", content="人格", authority=None),
            ContextMessage(
                role="system",
                content="X" * 2000,
                authority=AuthorityLevel.SUBCONSCIOUS,
            ),
        ]
        messages[0].token_count = counter.count("人格")
        messages[1].token_count = counter.count("X" * 2000)

        # 预算只够人格消息
        budget = counter.count("人格") + 50
        result = _apply_circuit_breaker(messages, counter, budget=budget)

        # 人格消息应保留
        persona_msgs = [m for m in result if m.authority is None]
        assert len(persona_msgs) == 1
        assert persona_msgs[0].content == "人格"


# ── assemble_actor_context 测试 ──


class TestAssembleActorContext:
    """assemble_actor_context 完整组装测试。"""

    def test_basic_assembly(self, tmp_path: Path) -> None:
        """测试基本的上下文组装。"""
        # 创建项目结构
        project_root = tmp_path
        (project_root / "characters").mkdir()
        (project_root / "prompts").mkdir()

        # 创建角色文件
        storage = YAMLStorage()
        storage.write_markdown_file(
            project_root / "characters" / "char_001.md",
            {
                "id": "char_001",
                "name": "测试角色",
                "physical": {"injuries": [], "buffs": [], "debuffs": []},
            },
            "# 角色背景",
        )

        # 创建 Prompt 文件
        prompt_path = project_root / "prompts" / "actor.v1.md"
        prompt_path.write_text("# Actor Prompt\n\n你是写作代理。", encoding="utf-8")

        # 创建章节文件
        chapter_path = project_root / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章\n\n故事开始。",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=project_root,
            current_text="故事开始的文本。",
            prompt_path=prompt_path,
            canon_content="魔法消耗寿命。",
            subconscious_content="灵感碎片。",
            yaml_storage=storage,
        )

        # 应该包含：人格 + CANON + STATE + SUBCONSCIOUS + 近期正文
        assert len(messages) >= 4
        # 检查消息角色
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles

    def test_assembly_without_optional_content(self, tmp_path: Path) -> None:
        """测试不提供可选内容时的组装。"""
        project_root = tmp_path
        (project_root / "characters").mkdir()

        storage = YAMLStorage()
        storage.write_markdown_file(
            project_root / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        chapter_path = project_root / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=project_root,
            current_text="正文内容",
            yaml_storage=storage,
        )

        # 至少应有 STATE + 近期正文
        assert len(messages) >= 2

    def test_messages_are_valid_dict_format(self, tmp_path: Path) -> None:
        """测试返回的消息列表格式正确。"""
        project_root = tmp_path
        (project_root / "characters").mkdir()

        storage = YAMLStorage()
        storage.write_markdown_file(
            project_root / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        chapter_path = project_root / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=project_root,
            current_text="正文",
            yaml_storage=storage,
        )

        for msg in messages:
            assert isinstance(msg, dict)
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in ("system", "user", "assistant")


# ── FRUGAL 截断分支测试 ──


def _generate_large_text(token_counter: TokenCounter, target_tokens: int) -> str:
    """生成至少 target_tokens 数量的文本用于测试。"""
    # 每段基础文本约 200 tokens，循环生成直到足够
    base = "这是一段用于测试截断逻辑的长文本内容。" * 50
    result = base
    while token_counter.count(result) < target_tokens:
        result += base
    return result


class TestFrugalTruncation:
    """FRUGAL 策略中各层级截断分支测试。"""

    def test_canon_truncated_when_exceeds_budget(self, tmp_path: Path) -> None:
        """测试 CANON 内容超过预算时被截断 (覆盖 290-291 行)。"""
        counter = TokenCounter()
        project_root = tmp_path
        (project_root / "characters").mkdir()

        storage = YAMLStorage()
        storage.write_markdown_file(
            project_root / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        chapter_path = project_root / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        # CANON 预算 = int(6000 * 0.20) = 1200 tokens，生成远超的内容
        large_canon = _generate_large_text(counter, 2000)

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=project_root,
            current_text="正文。",
            canon_content=large_canon,
            yaml_storage=storage,
            strategy=ContextStrategy.FRUGAL,
        )

        # CANON 消息应该被截断
        canon_msgs = [m for m in messages if "[CANON | IMMUTABLE" in m.get("content", "")]
        assert len(canon_msgs) == 1
        canon_tokens = counter.count(canon_msgs[0]["content"])
        assert canon_tokens <= int(INPUT_TOKEN_BUDGET * BUDGET_RATIOS[AuthorityLevel.CANON])

    def test_state_truncated_when_exceeds_budget(self, tmp_path: Path) -> None:
        """测试 STATE MEMORY 内容超过预算时被截断 (覆盖 311-312 行)。"""
        counter = TokenCounter()
        project_root = tmp_path
        (project_root / "characters").mkdir()

        storage = YAMLStorage()

        # STATE 预算 = int(6000 * 0.30) = 1800 tokens
        # 角色 Frontmatter 的 JSON 序列化需要足够大，确保超过 1800 tokens
        large_name = "角色名" * 2000
        storage.write_markdown_file(
            project_root / "characters" / "char_001.md",
            {
                "id": "char_001",
                "name": large_name,
                "physical": {"injuries": [], "buffs": [], "debuffs": []},
            },
            "# 背景",
        )

        chapter_path = project_root / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=project_root,
            current_text="正文。",
            yaml_storage=storage,
            strategy=ContextStrategy.FRUGAL,
        )

        state_msgs = [m for m in messages if "[STATE MEMORY" in m.get("content", "")]
        assert len(state_msgs) == 1
        state_tokens = counter.count(state_msgs[0]["content"])
        assert state_tokens <= int(INPUT_TOKEN_BUDGET * BUDGET_RATIOS[AuthorityLevel.STATE_MEMORY])

    def test_subconscious_truncated_when_exceeds_budget(self, tmp_path: Path) -> None:
        """测试 SUBCONSCIOUS 内容超过预算时被截断 (覆盖 326-327 行)。"""
        counter = TokenCounter()
        project_root = tmp_path
        (project_root / "characters").mkdir()

        storage = YAMLStorage()
        storage.write_markdown_file(
            project_root / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        chapter_path = project_root / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        # SUBCONSCIOUS 预算 = int(6000 * 0.10) = 600 tokens
        large_sub = _generate_large_text(counter, 1000)

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=project_root,
            current_text="正文。",
            subconscious_content=large_sub,
            yaml_storage=storage,
            strategy=ContextStrategy.FRUGAL,
        )

        sub_msgs = [m for m in messages if "[SUBCONSCIOUS FRAGMENT" in m.get("content", "")]
        assert len(sub_msgs) == 1
        sub_tokens = counter.count(sub_msgs[0]["content"])
        assert sub_tokens <= int(INPUT_TOKEN_BUDGET * BUDGET_RATIOS[AuthorityLevel.SUBCONSCIOUS])

    def test_current_text_truncated_when_exceeds_budget(self, tmp_path: Path) -> None:
        """测试近期正文超过剩余预算时被截断 (覆盖 340 行)。"""
        counter = TokenCounter()
        project_root = tmp_path
        (project_root / "characters").mkdir()

        storage = YAMLStorage()
        storage.write_markdown_file(
            project_root / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )

        chapter_path = project_root / "ch_001.md"
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章",
        )

        # 近期正文预算 = int(6000 * 0.40) = 2400 tokens，加上其他层占用后剩余更少
        # 不提供 canon/sub，让剩余预算尽量给 text，但 text 仍然超限
        large_text = _generate_large_text(counter, 3000)

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=project_root,
            current_text=large_text,
            yaml_storage=storage,
            strategy=ContextStrategy.FRUGAL,
        )

        # 应该有一个 user 消息，内容被截断
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert counter.count(user_msgs[0]["content"]) <= INPUT_TOKEN_BUDGET


# ── _apply_circuit_breaker 完全移除测试 ──


class TestCircuitBreakerCompleteRemoval:
    """测试熔断机制中消息被完全移除的场景 (覆盖 693-695 行)。"""

    def test_message_completely_removed(self) -> None:
        """测试当需要裁剪的 token 数 >= 消息 token 数时，消息被完全移除。"""
        counter = TokenCounter()

        # 创建两条消息，一条很小一条很大
        small_msg = ContextMessage(role="system", content="小文本", authority=AuthorityLevel.CANON)
        small_msg.token_count = counter.count("小文本")

        large_text = "大量内容" * 300
        large_msg = ContextMessage(
            role="system",
            content=large_text,
            authority=AuthorityLevel.SUBCONSCIOUS,
        )
        large_msg.token_count = counter.count(large_text)

        # 设置预算极小，使得 excess >= large_msg.token_count，触发完全移除
        # total = small(~3) + large(~450) ≈ 453, budget = 1 → excess = 452 >= 450
        budget = 1
        messages = [small_msg, large_msg]

        result = _apply_circuit_breaker(messages, counter, budget)

        # 大消息应被完全移除（token_count 降为 0，content 清空）
        sub_msgs = [m for m in result if m.authority == AuthorityLevel.SUBCONSCIOUS]
        # SUBCONSCIOUS 消息应被完全移除（被过滤掉或 content 为空）
        if sub_msgs:
            assert sub_msgs[0].token_count == 0
            assert sub_msgs[0].content == ""
        # 总 token 应在预算内
        total = sum(m.token_count for m in result)
        assert total <= budget


# ── _load_previous_chapters 测试 ──


class TestLoadPreviousChapters:
    """_load_previous_chapters 函数测试。"""

    def test_corrupted_file_skipped(self, tmp_path: Path) -> None:
        """测试损坏文件（读取异常）被跳过 (覆盖 635-636 行)。"""
        counter = TokenCounter()
        (tmp_path / "draft").mkdir()

        # 创建一个有效的章节
        valid_file = tmp_path / "draft" / "ch_001.md"
        valid_file.write_text("# 第一章\n\n正常内容。", encoding="utf-8")

        # 创建一个会导致 Unicode 解码错误的损坏文件（无效 UTF-8 字节）
        corrupted_file = tmp_path / "draft" / "ch_002.md"
        corrupted_file.write_bytes(b"---\nid: ch_002\n---\n\xff\xfe\x00\x01\x80\xfe")

        current_path = tmp_path / "draft" / "ch_003.md"
        current_path.write_text("# 第三章", encoding="utf-8")

        result = _load_previous_chapters(current_path, tmp_path, counter, budget=10000)

        # 应该只包含 ch_001 的内容（ch_002 被跳过）
        assert "第一章" in result or "正常内容" in result

    def test_empty_body_skipped(self, tmp_path: Path) -> None:
        """测试只有 Frontmatter 没有正文的章节被跳过 (覆盖 639 行)。"""
        counter = TokenCounter()
        (tmp_path / "draft").mkdir()

        # 创建只有 Frontmatter 没有正文的章节
        empty_body_file = tmp_path / "draft" / "ch_001.md"
        empty_body_file.write_text("---\nid: ch_001\n---\n   \n  ", encoding="utf-8")

        # 创建一个有效章节
        valid_file = tmp_path / "draft" / "ch_002.md"
        valid_file.write_text("# 第二章\n\n有效内容。", encoding="utf-8")

        current_path = tmp_path / "draft" / "ch_003.md"
        current_path.write_text("# 第三章", encoding="utf-8")

        result = _load_previous_chapters(current_path, tmp_path, counter, budget=10000)

        # ch_001 应被跳过（空正文），只包含 ch_002
        assert "ch_002" in result or "有效内容" in result

    def test_budget_truncation(self, tmp_path: Path) -> None:
        """测试历史章节超出预算时被截断 (覆盖 649-653 行)。"""
        counter = TokenCounter()
        (tmp_path / "draft").mkdir()

        # 创建多个大章节
        for i in range(1, 5):
            chapter_file = tmp_path / "draft" / f"ch_{i:03d}.md"
            # 每章约 300+ tokens
            large_content = f"第{i}章内容。\n\n" + "这是很长的章节正文。" * 50
            chapter_file.write_text(
                f"---\nid: ch_{i:03d}\n---\n\n{large_content}",
                encoding="utf-8",
            )

        current_path = tmp_path / "draft" / "ch_005.md"
        current_path.write_text("# 第五章", encoding="utf-8")

        # 设置很小的预算，迫使截断
        result = _load_previous_chapters(current_path, tmp_path, counter, budget=200)

        # 应该有内容返回，但被截断
        if result:
            result_tokens = counter.count(result)
            assert result_tokens <= 200 + 50  # 允许少量容差

    def test_no_draft_directory(self, tmp_path: Path) -> None:
        """测试 draft 目录不存在时返回空字符串。"""
        counter = TokenCounter()
        current_path = tmp_path / "ch_001.md"

        result = _load_previous_chapters(current_path, tmp_path, counter, budget=10000)
        assert result == ""

    def test_only_current_chapter_in_draft(self, tmp_path: Path) -> None:
        """测试 draft 目录中只有当前章节时返回空字符串。"""
        counter = TokenCounter()
        (tmp_path / "draft").mkdir()

        current_path = tmp_path / "draft" / "ch_001.md"
        current_path.write_text("# 第一章\n\n内容。", encoding="utf-8")

        result = _load_previous_chapters(current_path, tmp_path, counter, budget=10000)
        assert result == ""

    def test_chapter_without_frontmatter(self, tmp_path: Path) -> None:
        """测试没有 Frontmatter 的章节文件正常读取。"""
        counter = TokenCounter()
        (tmp_path / "draft").mkdir()

        # 创建没有 Frontmatter 的章节
        no_fm_file = tmp_path / "draft" / "ch_001.md"
        no_fm_file.write_text("# 第一章\n\n没有 Frontmatter 的内容。", encoding="utf-8")

        current_path = tmp_path / "draft" / "ch_002.md"
        current_path.write_text("# 第二章", encoding="utf-8")

        result = _load_previous_chapters(current_path, tmp_path, counter, budget=10000)

        assert "没有 Frontmatter" in result
