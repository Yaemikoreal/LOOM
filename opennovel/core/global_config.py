"""全局配置加载器 — 跨项目的 OpenNovel 默认设置。

查找策略：从项目根目录（或 CWD）逐级向上搜索 `.opennovel.yaml`。
用于设置 workspace 目录、默认模型等全局性配置。

使用方式:
    cfg = GlobalConfig.load()
    model = cfg.get("default_model", "deepseek/deepseek-v4-flash")
    ws = cfg.workspace_dir  # Path object
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 全局配置文件名
GLOBAL_CONFIG_FILENAME = ".opennovel.yaml"

# 硬编码默认值（最底层 fallback）
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


class GlobalConfig:
    """全局配置 — 三层模型路由的中间层。

    优先级: novel.yaml > .opennovel.yaml > 硬编码默认值

    Attributes:
        workspace_dir: 小说项目工作区目录
        default_model: 全局默认模型名称
        default_api_base: 全局默认 API 端点
        _raw: 原始配置字典
        _source: 配置文件来源路径
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._source = config_path
        self._raw: dict[str, Any] = {}

        if config_path and config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    self._raw = yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning("全局配置读取失败: %s", e)

    @property
    def workspace_dir(self) -> Path:
        """小说项目工作区目录。

        如果配置中指定了 workspace_dir，解析为绝对路径；
        否则默认为 OpenNovel 项目根下的 novels/。
        """
        raw = self._raw.get("workspace_dir", "")
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else (self._find_project_root() / p)
        return self._find_project_root() / "novels"

    @property
    def default_model(self) -> str:
        """全局默认模型名称。"""
        return self._raw.get("default_model", DEFAULT_MODEL)

    @property
    def default_api_base(self) -> str | None:
        """全局默认 API 端点。"""
        return self._raw.get("default_api_base")

    @property
    def default_api_key(self) -> str | None:
        """全局默认 API 密钥。

        安全提示：建议使用环境变量而非配置文件存储密钥。
        """
        return self._raw.get("default_api_key")

    def get(self, key: str, default: Any = None) -> Any:
        """获取任意配置项。"""
        return self._raw.get(key, default)

    @property
    def source(self) -> Path | None:
        """配置文件来源路径。"""
        return self._source

    @classmethod
    def load(cls) -> "GlobalConfig":
        """加载全局配置。

        从当前工作目录开始逐级向上搜索 `.opennovel.yaml`，
        找到第一个即停止。

        Returns:
            GlobalConfig 实例（未找到时返回空配置）
        """
        config_path = cls._search_upwards(Path.cwd())
        if config_path:
            logger.debug("加载全局配置: %s", config_path)
        else:
            logger.debug("未找到全局配置，使用默认值")
        return cls(config_path)

    @classmethod
    def load_from_project_root(cls, project_root: Path) -> "GlobalConfig":
        """从指定项目根目录加载全局配置。

        先搜索项目根目录，再逐级向上搜索。

        Args:
            project_root: 项目根目录

        Returns:
            GlobalConfig 实例
        """
        config_path = cls._search_upwards(project_root)
        return cls(config_path)

    @staticmethod
    def _search_upwards(start: Path) -> Path | None:
        """从 start 目录开始逐级向上搜索全局配置文件。

        Args:
            start: 起始目录

        Returns:
            配置文件路径，未找到返回 None
        """
        current = start.resolve()
        for _ in range(32):  # 最多向上 32 层
            candidate = current / GLOBAL_CONFIG_FILENAME
            if candidate.is_file():
                return candidate
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None

    @staticmethod
    def _find_project_root() -> Path:
        """查找 OpenNovel 项目根目录。

        从当前目录向上搜索，找到包含 opennovel 包的目录。
        回退到当前工作目录。
        """
        current = Path.cwd().resolve()
        for _ in range(16):
            if (current / "opennovel").is_dir() or (current / ".opennovel.yaml").is_file():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        return Path.cwd().resolve()

    def __repr__(self) -> str:
        return (
            f"GlobalConfig(source={self._source}, "
            f"model={self.default_model}, "
            f"workspace={self.workspace_dir})"
        )
