"""parser 模块测试 - Markdown 文本切分。"""

from opennovel.core.parser import (
    count_text_tokens,
    split_chapter_into_scenes,
    truncate_to_budget,
)


class TestSplitChapterIntoScenes:
    """split_chapter_into_scenes 切分逻辑测试。"""

    def test_single_scene_no_headings(self) -> None:
        """测试无标题的纯文本。"""
        text = "这是第一段。\n\n这是第二段。"
        scenes = split_chapter_into_scenes(text, max_tokens=5000)
        assert len(scenes) == 1
        assert "这是第一段。" in scenes[0]

    def test_split_by_headings(self) -> None:
        """测试按 # 标题切分。"""
        text = "# 场景一\n\n内容一\n\n# 场景二\n\n内容二"
        scenes = split_chapter_into_scenes(text, max_tokens=5000)
        assert len(scenes) == 2
        assert "场景一" in scenes[0]
        assert "场景二" in scenes[1]

    def test_split_by_token_budget(self) -> None:
        """测试按 Token 预算截断。"""
        # "hello world" 约 2 个 token，设置 max_tokens=3 每行
        text = "# A\n\nhello world\n\n# B\n\nfoo bar baz"
        scenes = split_chapter_into_scenes(text, max_tokens=10)
        assert len(scenes) >= 1
        # 应该能切出至少一个场景
        assert any("hello" in s for s in scenes)

    def test_empty_text(self) -> None:
        """测试空文本。"""
        scenes = split_chapter_into_scenes("", max_tokens=1000)
        assert scenes == []

    def test_single_heading(self) -> None:
        """测试只有标题没有正文。"""
        scenes = split_chapter_into_scenes("# 孤立的标题", max_tokens=1000)
        assert len(scenes) == 1
        assert "孤立的标题" in scenes[0]

    def test_subheadings_not_split(self) -> None:
        """测试 ## 和 ### 子标题不触发切分。"""
        text = "# 主场景\n\n## 子场景\n\n### 细节\n\n正文内容"
        scenes = split_chapter_into_scenes(text, max_tokens=5000)
        # 只有 # 一级标题触发切分，所以全部在一个场景里
        assert len(scenes) == 1
        assert "子场景" in scenes[0]
        assert "细节" in scenes[0]

    def test_chinese_heading(self) -> None:
        """测试中文标题切分。"""
        text = "# 第一幕\n\n内容一\n\n# 第二幕\n\n内容二"
        scenes = split_chapter_into_scenes(text, max_tokens=5000)
        assert len(scenes) == 2
        assert "第一幕" in scenes[0]
        assert "第二幕" in scenes[1]

    def test_whitespace_only_text(self) -> None:
        """测试纯空白文本返回空列表。"""
        scenes = split_chapter_into_scenes("   \n\n  \n  ", max_tokens=1000)
        assert scenes == []

    def test_multiple_headings_no_body(self) -> None:
        """测试多个标题无正文。"""
        text = "# 标题一\n\n# 标题二\n\n# 标题三"
        scenes = split_chapter_into_scenes(text, max_tokens=5000)
        assert len(scenes) == 3

    def test_unknown_encoding_fallback(self) -> None:
        """测试未知编码模型回退。"""
        text = "# 场景\n\n内容"
        scenes = split_chapter_into_scenes(text, max_tokens=5000, encoding_model="nonexistent")
        assert len(scenes) == 1


class TestCountTextTokens:
    """count_text_tokens 测试。"""

    def test_count_simple_text(self) -> None:
        """测试简单文本的 Token 计数。"""
        count = count_text_tokens("hello world")
        assert count > 0
        assert isinstance(count, int)

    def test_count_empty_text(self) -> None:
        """测试空文本计数。"""
        count = count_text_tokens("")
        assert count == 0

    def test_count_chinese_text(self) -> None:
        """测试中文本 Token 计数。"""
        count = count_text_tokens("你好世界")
        assert count > 0


class TestTruncateToBudget:
    """truncate_to_budget 测试。"""

    def test_truncate_short_text(self) -> None:
        """测试短文本不截断。"""
        result = truncate_to_budget("hello", max_tokens=100)
        assert result == "hello"

    def test_truncate_long_text(self) -> None:
        """测试长文本截断。"""
        text = "hello world foo bar baz " * 20
        original_count = count_text_tokens(text)
        result = truncate_to_budget(text, max_tokens=10)
        result_count = count_text_tokens(result)
        assert result_count <= 10
        assert result_count < original_count

    def test_unknown_encoding_fallback(self) -> None:
        """测试未知编码回退。"""
        result = truncate_to_budget("hello world", max_tokens=100, encoding_model="nonexistent")
        assert result == "hello world"


class TestCountTextTokensEdgeCases:
    """count_text_tokens 边界测试。"""

    def test_unknown_encoding_fallback(self) -> None:
        """测试未知编码模型回退。"""
        count = count_text_tokens("hello", encoding_model="nonexistent")
        assert count > 0

    def test_long_text(self) -> None:
        """测试长文本计数。"""
        text = "这是一段很长的文本。" * 1000
        count = count_text_tokens(text)
        assert count > 1000


class TestSplitByBudgetEdgeCases:
    """split_chapter_into_scenes 按 Token 预算切分的边界场景。"""

    def test_single_line_exceeds_max_tokens(self) -> None:
        """测试单行文本超过 max_tokens 时的切分行为。

        覆盖 parser.py 第 66-68 行：当累积行的 token 超过 max_tokens
        且 current_scene_lines 非空时，先将已有内容切分为一个场景。
        """
        # 用足够多的重复文本生成超过 max_tokens 的单行
        long_line = "这是一段非常长的文本内容" * 50  # ~600+ tokens
        text = f"短行\n{long_line}"
        # max_tokens 设得很小，第一行放进去后，第二行必然超限
        scenes = split_chapter_into_scenes(text, max_tokens=50)
        # 应该切分出至少两个场景
        assert len(scenes) >= 2

    def test_budget_split_without_headings(self) -> None:
        """测试无标题文本按 Token 预算切分。

        多行文本累积超过 max_tokens，不依赖标题触发切分。
        """
        # 构建多行文本，每行 ~30 tokens，max_tokens=50 只能放 1 行
        lines = [f"这是第{i}行的测试内容。" for i in range(10)]
        text = "\n".join(lines)
        scenes = split_chapter_into_scenes(text, max_tokens=50)
        # 应切分为多个场景
        assert len(scenes) >= 2
        # 所有行都应出现在某个场景中
        all_text = "\n".join(scenes)
        for i in range(10):
            assert f"第{i}行" in all_text

    def test_single_line_exactly_at_max_tokens(self) -> None:
        """测试单行恰好等于 max_tokens 时不切分。"""
        # 构造恰好约 20 tokens 的文本
        line = "hello world " * 5  # ~15 tokens
        text = f"prefix\n{line}"
        # 设置足够大的 max_tokens
        total = count_text_tokens(text)
        scenes = split_chapter_into_scenes(text, max_tokens=total)
        assert len(scenes) == 1
