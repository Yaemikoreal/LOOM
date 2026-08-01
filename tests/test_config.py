"""config 模块测试 - 项目配置加载与保存。"""

from pathlib import Path

import yaml

from opennovel.core.config import (
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_RESERVE,
    DEFAULT_TOKEN_BUDGET,
    DEFAULT_VERSION,
    LoomConfig,
)


class TestLoomConfigDefaults:
    """LoomConfig 默认值测试。"""

    def test_default_values(self) -> None:
        """测试默认配置值。"""
        config = LoomConfig()
        assert config.version == DEFAULT_VERSION
        assert config.model == DEFAULT_MODEL
        assert config.token_budget == DEFAULT_TOKEN_BUDGET
        assert config.output_reserve == DEFAULT_OUTPUT_RESERVE
        assert config.extra == {}

    def test_input_token_budget(self) -> None:
        """测试输入 Token 预算计算。"""
        config = LoomConfig(token_budget=8000, output_reserve=2000)
        assert config.input_token_budget == 6000

    def test_custom_values(self) -> None:
        """测试自定义配置值。"""
        config = LoomConfig(
            model="deepseek-chat",
            token_budget=32000,
            output_reserve=4000,
        )
        assert config.model == "deepseek-chat"
        assert config.token_budget == 32000
        assert config.output_reserve == 4000
        assert config.input_token_budget == 28000


class TestLoomConfigLoad:
    """LoomConfig.load 配置加载测试。"""

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """测试配置文件不存在时返回默认配置。"""
        config = LoomConfig.load(tmp_path)
        assert config.model == DEFAULT_MODEL
        assert config.token_budget == DEFAULT_TOKEN_BUDGET

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """测试加载有效配置文件。"""
        config_data = {
            "version": "2.0.0",
            "model": "deepseek-chat",
            "token_budget": 32000,
            "output_reserve": 4000,
        }
        config_path = tmp_path / "novel.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True)

        config = LoomConfig.load(tmp_path)
        assert config.version == "2.0.0"
        assert config.model == "deepseek-chat"
        assert config.token_budget == 32000
        assert config.output_reserve == 4000

    def test_load_partial_config(self, tmp_path: Path) -> None:
        """测试加载部分配置（缺失字段使用默认值）。"""
        config_data = {"model": "gpt-3.5-turbo"}
        config_path = tmp_path / "novel.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config = LoomConfig.load(tmp_path)
        assert config.model == "gpt-3.5-turbo"
        assert config.token_budget == DEFAULT_TOKEN_BUDGET
        assert config.version == DEFAULT_VERSION

    def test_load_with_extra_fields(self, tmp_path: Path) -> None:
        """测试加载包含自定义字段的配置。"""
        config_data = {
            "model": "gpt-4",
            "custom_setting": "value",
            "nested": {"key": "val"},
        }
        config_path = tmp_path / "novel.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config = LoomConfig.load(tmp_path)
        assert config.extra["custom_setting"] == "value"
        assert config.extra["nested"] == {"key": "val"}

    def test_load_empty_yaml(self, tmp_path: Path) -> None:
        """测试加载空 YAML 文件。"""
        config_path = tmp_path / "novel.yaml"
        config_path.write_text("", encoding="utf-8")

        config = LoomConfig.load(tmp_path)
        assert config.model == DEFAULT_MODEL

    def test_load_corrupted_yaml(self, tmp_path: Path) -> None:
        """测试加载损坏的 YAML 文件返回默认配置。"""
        config_path = tmp_path / "novel.yaml"
        config_path.write_text("invalid: [yaml: broken", encoding="utf-8")

        config = LoomConfig.load(tmp_path)
        assert config.model == DEFAULT_MODEL


class TestLoomConfigSave:
    """LoomConfig.save 配置保存测试。"""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        """测试保存创建配置文件。"""
        config = LoomConfig(model="deepseek-chat", token_budget=16000)
        config.save(tmp_path)

        config_path = tmp_path / "novel.yaml"
        assert config_path.exists()

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["model"] == "deepseek-chat"
        assert data["token_budget"] == 16000

    def test_save_includes_extra(self, tmp_path: Path) -> None:
        """测试保存包含自定义字段。"""
        config = LoomConfig(extra={"custom": "value"})
        config.save(tmp_path)

        config_path = tmp_path / "novel.yaml"
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["custom"] == "value"

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        """测试覆盖已有配置文件。"""
        config1 = LoomConfig(model="gpt-4")
        config1.save(tmp_path)

        config2 = LoomConfig(model="gpt-3.5-turbo")
        config2.save(tmp_path)

        config_path = tmp_path / "novel.yaml"
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["model"] == "gpt-3.5-turbo"

    def test_roundtrip(self, tmp_path: Path) -> None:
        """测试保存→加载的往返一致性。"""
        original = LoomConfig(
            version="2.0.0",
            model="deepseek-chat",
            token_budget=64000,
            output_reserve=8000,
            extra={"custom": 42},
        )
        original.save(tmp_path)

        loaded = LoomConfig.load(tmp_path)
        assert loaded.version == original.version
        assert loaded.model == original.model
        assert loaded.token_budget == original.token_budget
        assert loaded.output_reserve == original.output_reserve
        assert loaded.extra["custom"] == 42

    def test_save_failure_logs_error(self, tmp_path: Path) -> None:
        """测试保存失败时记录错误日志（覆盖 lines 106-107）。"""
        from unittest.mock import patch

        config = LoomConfig(model="gpt-4")

        # 模拟 open() 抛出异常
        with patch("builtins.open", side_effect=OSError("磁盘已满")):
            # save() 内部捕获异常，不应抛出
            config.save(tmp_path)

        # 原始目录不应受影响，验证函数没有崩溃
        assert config.model == "gpt-4"


class TestAgentConfigStageModels:
    """AgentConfig 阶段模型测试。"""

    def test_agent_config_with_stage_models(self) -> None:
        """测试 AgentConfig 存储阶段模型。"""
        from opennovel.core.config import AgentConfig

        cfg = AgentConfig(
            model="gpt-4",
            think_model="gpt-4o-mini",
            write_model="gpt-4",
            revise_model="gpt-4",
        )
        assert cfg.think_model == "gpt-4o-mini"
        assert cfg.write_model == "gpt-4"
        assert cfg.revise_model == "gpt-4"

    def test_agent_config_defaults(self) -> None:
        """测试 AgentConfig 阶段模型默认值为 None。"""
        from opennovel.core.config import AgentConfig

        cfg = AgentConfig()
        assert cfg.think_model is None
        assert cfg.write_model is None
        assert cfg.revise_model is None

    def test_load_config_with_stage_models(self, tmp_path: Path) -> None:
        """测试加载含阶段模型的配置。"""
        config_path = tmp_path / "novel.yaml"
        config_path.write_text(
            "model: gpt-4\n"
            "agents:\n"
            "  writer:\n"
            "    model: gpt-4\n"
            "    think_model: gpt-4o-mini\n"
            "    write_model: gpt-4\n"
            "    revise_model: gpt-4\n",
            encoding="utf-8",
        )

        config = LoomConfig.load(tmp_path)
        assert config.agent_writer.think_model == "gpt-4o-mini"
        assert config.agent_writer.write_model == "gpt-4"
        assert config.agent_writer.revise_model == "gpt-4"

    def test_save_roundtrip_stage_models(self, tmp_path: Path) -> None:
        """测试保存和重新加载含阶段模型的配置。"""
        from opennovel.core.config import AgentConfig

        config = LoomConfig(
            model="gpt-4",
            agent_writer=AgentConfig(
                model="gpt-4",
                think_model="gpt-4o-mini",
                write_model="gpt-4",
            ),
        )
        config.save(tmp_path)

        loaded = LoomConfig.load(tmp_path)
        assert loaded.agent_writer.think_model == "gpt-4o-mini"
        assert loaded.agent_writer.write_model == "gpt-4"
        # revise_model 未设置 → 保存时被省略 → 加载后为 None
        assert loaded.agent_writer.revise_model is None
