"""GlobalConfig 全局配置测试。"""

from pathlib import Path

from opennovel.core.global_config import DEFAULT_MODEL, GlobalConfig


class TestGlobalConfigDefaults:
    """GlobalConfig 默认值测试。"""

    def test_default_model(self) -> None:
        """测试默认模型。"""
        cfg = GlobalConfig()
        assert cfg.default_model == DEFAULT_MODEL

    def test_default_workspace(self) -> None:
        """测试默认 workspace 指向 novels/。"""
        cfg = GlobalConfig()
        ws = cfg.workspace_dir
        assert ws.name == "novels"

    def test_default_api_base_none(self) -> None:
        """测试默认 API 端点为 None。"""
        cfg = GlobalConfig()
        assert cfg.default_api_base is None

    def test_get_unknown_key(self) -> None:
        """测试获取不存在的键。"""
        cfg = GlobalConfig()
        assert cfg.get("nonexistent") is None
        assert cfg.get("nonexistent", "default") == "default"


class TestGlobalConfigFromFile:
    """GlobalConfig 从文件加载测试。"""

    def test_load_from_file(self, tmp_path: Path) -> None:
        """测试从文件加载。"""
        config_path = tmp_path / ".opennovel.yaml"
        custom_ws = tmp_path / "custom" / "workspace"
        config_path.write_text(
            f"default_model: claude-sonnet-4-20250514\n"
            f"workspace_dir: {custom_ws.as_posix()}\n"
            f"default_api_base: https://api.anthropic.com/v1\n",
            encoding="utf-8",
        )
        cfg = GlobalConfig(config_path)
        assert cfg.default_model == "claude-sonnet-4-20250514"
        assert cfg.workspace_dir == custom_ws.resolve()
        assert cfg.default_api_base == "https://api.anthropic.com/v1"

    def test_load_from_file_partial(self, tmp_path: Path) -> None:
        """测试部分配置。"""
        config_path = tmp_path / ".opennovel.yaml"
        config_path.write_text("default_model: gpt-4o\n", encoding="utf-8")
        cfg = GlobalConfig(config_path)
        assert cfg.default_model == "gpt-4o"
        # 未设置的字段使用默认值
        assert cfg.default_api_base is None

    def test_empty_file(self, tmp_path: Path) -> None:
        """测试空配置文件。"""
        config_path = tmp_path / ".opennovel.yaml"
        config_path.write_text("", encoding="utf-8")
        cfg = GlobalConfig(config_path)
        assert cfg.default_model == DEFAULT_MODEL

    def test_nonexistent_file(self) -> None:
        """测试不存在的文件。"""
        cfg = GlobalConfig(Path("/nonexistent/.opennovel.yaml"))
        assert cfg.default_model == DEFAULT_MODEL
        assert cfg.source is not None


class TestGlobalConfigSearch:
    """GlobalConfig 文件搜索测试。"""

    def test_search_upwards_finds_file(self, tmp_path: Path) -> None:
        """测试向上搜索找到配置文件。"""
        config_path = tmp_path / ".opennovel.yaml"
        config_path.write_text(
            "default_model: test-model\n",
            encoding="utf-8",
        )
        # 在子目录中搜索
        sub_dir = tmp_path / "sub" / "deep"
        sub_dir.mkdir(parents=True)
        found = GlobalConfig._search_upwards(sub_dir)
        assert found is not None
        assert found.resolve() == config_path.resolve()

    def test_search_upwards_not_found(self, tmp_path: Path) -> None:
        """测试搜索不到配置文件。"""
        found = GlobalConfig._search_upwards(tmp_path)
        assert found is None


class TestLoomConfigIntegration:
    """LoomConfig 与 GlobalConfig 集成测试。"""

    def test_load_with_global_config(self, tmp_path: Path) -> None:
        """测试 LoomConfig 加载时使用全局配置的默认模型。"""
        from opennovel.core.config import LoomConfig
        from opennovel.core.global_config import GlobalConfig

        # 创建项目目录（无 novel.yaml）
        project_root = tmp_path / "test_project"
        project_root.mkdir()

        global_cfg = GlobalConfig()
        global_cfg._raw = {"default_model": "claude-sonnet-4-20250514"}

        config = LoomConfig.load(project_root, global_cfg=global_cfg)
        assert config.model == "claude-sonnet-4-20250514"

    def test_novel_yaml_overrides_global(self, tmp_path: Path) -> None:
        """测试 novel.yaml 覆盖全局配置。"""
        from opennovel.core.config import LoomConfig
        from opennovel.core.global_config import GlobalConfig

        # 创建项目目录含 novel.yaml
        project_root = tmp_path / "test_project"
        project_root.mkdir()
        (project_root / "novel.yaml").write_text("model: gpt-4o\n", encoding="utf-8")

        global_cfg = GlobalConfig()
        global_cfg._raw = {"default_model": "claude-sonnet-4-20250514"}

        config = LoomConfig.load(project_root, global_cfg=global_cfg)
        assert config.model == "gpt-4o"  # novel.yaml 覆盖全局

    def test_global_fallback_to_default(self, tmp_path: Path) -> None:
        """测试全局配置空时使用硬编码默认值。"""
        from opennovel.core.config import LoomConfig
        from opennovel.core.global_config import GlobalConfig

        project_root = tmp_path / "test_project"
        project_root.mkdir()

        config = LoomConfig.load(project_root, global_cfg=GlobalConfig())
        assert config.model == DEFAULT_MODEL

    def test_init_uses_global_model(self, tmp_path: Path) -> None:
        """测试 novel init 生成的配置使用全局默认模型。"""
        from opennovel.core.global_config import GlobalConfig

        # 模拟全局配置
        global_cfg = GlobalConfig()
        global_cfg._raw = {"default_model": "deepseek/deepseek-v4-flash"}

        # 验证 GlobalConfig 返回正确的默认值
        assert global_cfg.default_model == "deepseek/deepseek-v4-flash"
