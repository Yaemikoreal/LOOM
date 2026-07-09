"""项目配置加载器 - 读取 novel.yaml 项目配置。

负责：
- 加载 novel.yaml 项目配置文件
- 提供合理的默认值（三层 fallback：novel.yaml > GlobalConfig > 硬编码）
- 类型安全的配置访问
- per-agent LLM 配置覆盖
- 配置 Schema 校验（字段类型/范围/格式）

三层模型路由（ADR 0010 — Agent 自治基础设施）：
    novel.yaml agents.writer.model → novel.yaml model → .opennovel.yaml default_model → 硬编码

使用方式:
    config = LoomConfig.load(project_root)
    print(config.model)  # "deepseek/deepseek-v4-flash"（或 novel.yaml 的值）
    print(config.token_budget)  # 8000

    # 获取 Agent 专用 LLM 配置
    writer_cfg = config.get_agent_llm_config("writer")
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from opennovel.core.global_config import DEFAULT_MODEL, GlobalConfig
from opennovel.core.safety_fence import (
    DEFAULT_TOOL_PERMISSIONS,
)
from opennovel.core.safety_fence import (
    SafetyFenceConfig as _SafetyFenceConfig,
)

logger = logging.getLogger(__name__)


# ── 配置 Schema 校验 ──


class _NovelConfigSchema(BaseModel):
    """novel.yaml 的 Pydantic 校验 Schema。

    字段规则：
    - version: 语义化版本号
    - model: 非空模型名称
    - token_budget / output_reserve: 合理的 Token 范围
    - creative_direction: 可选，不超过 500 字
    - target_chapters: 可选，1-1000
    - words_per_chapter: 可选，100-50000
    """

    version: str = Field(default="1.0", pattern=r"^\d+\.\d+(\.\d+)?$")
    model: str = Field(default="", min_length=0)  # 空值允许（使用 fallback）
    token_budget: int | None = Field(default=None, ge=1000, le=1_000_000)
    output_reserve: int | None = Field(default=None, ge=0, le=100_000)
    api_base: str | None = None
    api_key: str | None = None
    creative_direction: str | None = Field(default=None, max_length=500)
    target_chapters: int | None = Field(default=None, ge=1, le=1000)
    words_per_chapter: int | None = Field(default=None, ge=100, le=50_000)
    outline: str | None = None
    director_enabled: bool | None = None
    agents: dict[str, dict] = Field(default_factory=dict)

    # 搜索配置 (ADR 0007)
    embedding_model: str | None = None
    reranker_enabled: bool | None = None
    reranker_model: str | None = None
    reranker_device: str | None = None
    search_top_k: int | None = Field(default=None, ge=1, le=100)

    # Agent 自治配置 (Phase 3)
    supports_native_tool_use: bool | None = None
    max_tool_call_history: int | None = Field(default=None, ge=1, le=50)

    # 安全围栏配置 (ADR 0010)
    safety_fence: dict | None = None

    # LLM 输入缓存配置 (P1)
    llm_cache_enabled: bool | None = None
    llm_cache_path: str | None = None

    # 快照清理策略 (P3)
    snapshot_max_count: int | None = Field(default=None, ge=1, le=10000)
    snapshot_max_days: int | None = Field(default=None, ge=1, le=3650)


class ConfigValidationError(Exception):
    """配置校验失败时抛出的异常，携带精确的错误详情。

    Attributes:
        errors: Pydantic 校验错误列表，每项含字段路径、错误消息、错误类型
        config_path: 配置文件路径（可选）
    """

    def __init__(
        self,
        errors: list[dict],
        config_path: str | None = None,
    ) -> None:
        self.errors = errors
        self.config_path = config_path
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = []
        if self.config_path:
            parts.append(f"配置文件 {self.config_path} 校验失败:")
        for err in self.errors:
            loc = " → ".join(str(x) for x in err["loc"])
            parts.append(f"  - [{loc}] {err['msg']} ({err['type']})")
        return "\n".join(parts)


# 默认配置值
DEFAULT_TOKEN_BUDGET = 8000
DEFAULT_OUTPUT_RESERVE = 2000
DEFAULT_VERSION = "1.0.1"


@dataclass
class AgentConfig:
    """单个 Agent 的 LLM 配置覆盖。

    未设置的字段继承 LoomConfig 的默认值。
    支持 stage 级模型路由（ADR 0009 执行层成本优化器）：
    - think_model: 思考阶段用便宜模型（如 gpt-4o-mini）
    - write_model: 创作阶段用主力模型（如 gpt-4）
    - revise_model: 修订阶段用主力模型（不设置则继承 model）
    """

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    think_model: str | None = None
    write_model: str | None = None
    write_model_climax: str | None = None
    revise_model: str | None = None


@dataclass
class LoomConfig:
    """OpenNovel 项目配置。

    Attributes:
        version: 项目版本号
        model: 默认 LLM 模型名称
        token_budget: Token 总预算
        output_reserve: 输出预留 Token 数
        api_base: 自定义 API 端点（用于 OpenAI 兼容接口）
        api_key: API 密钥（优先级高于环境变量）
        creative_direction: 创作方向描述 (loom auto 用)
        target_chapters: 目标章节数 (loom auto 用)
        words_per_chapter: 每章目标字数 (loom auto 用)
        outline: 大纲文件路径 (loom auto 用)
        agent_writer: Writer Agent 的 LLM 配置覆盖
        agent_critic: Critic Agent 的 LLM 配置覆盖
        agent_manager: Manager Agent 的 LLM 配置覆盖
        extra: 其他自定义配置
    """

    version: str = DEFAULT_VERSION
    model: str = DEFAULT_MODEL
    token_budget: int = DEFAULT_TOKEN_BUDGET
    output_reserve: int = DEFAULT_OUTPUT_RESERVE
    api_base: str | None = None
    api_key: str | None = None

    # loom auto 创作配置
    creative_direction: str = ""
    target_chapters: int = 5
    words_per_chapter: int = 3000
    outline: str = "outlines/story.md"

    # per-agent LLM 配置覆盖
    agent_writer: AgentConfig = field(default_factory=AgentConfig)
    agent_critic: AgentConfig = field(default_factory=AgentConfig)
    agent_manager: AgentConfig = field(default_factory=AgentConfig)
    agent_director: AgentConfig = field(default_factory=AgentConfig)

    # Director 配置
    director_enabled: bool = True

    # 安全围栏配置 (ADR 0010)
    safety_fence: _SafetyFenceConfig = field(default_factory=_SafetyFenceConfig)

    # 搜索配置 (ADR 0007 — 混合语义-关键词检索 + 重排序)
    embedding_model: str = "local:BAAI/bge-m3"  # 语义检索嵌入模型
    reranker_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str = ""  # 空字符串 = 自动检测 (cuda > mps > cpu)
    search_top_k: int = 5  # 最终返回结果数

    # Agent 自治配置 (Phase 3)
    supports_native_tool_use: bool = False
    max_tool_call_history: int = 5

    # LLM 输入缓存配置 (P1)
    llm_cache_enabled: bool = True
    llm_cache_path: str = ".novel.cache.db"

    # 快照清理策略 (P3)
    snapshot_max_count: int = 50
    snapshot_max_days: int = 30

    extra: dict = field(default_factory=dict)

    @property
    def input_token_budget(self) -> int:
        """输入 Token 预算（总预算 - 输出预留）。"""
        return self.token_budget - self.output_reserve

    def get_agent_llm_config(self, agent_name: str) -> dict[str, str | None]:
        """获取指定 Agent 的 LLM 配置，三层 fallback。

        fallback 链:
            agents.{name}.model → self.model → GlobalConfig.default_model

        Args:
            agent_name: Agent 名称 ("writer", "critic", "manager")

        Returns:
            包含 model, api_base, api_key 的字典
        """
        agent_cfg_map = {
            "writer": self.agent_writer,
            "critic": self.agent_critic,
            "manager": self.agent_manager,
            "director": self.agent_director,
        }
        agent_cfg = agent_cfg_map.get(agent_name, AgentConfig())
        return {
            "model": agent_cfg.model or self.model,
            "api_base": agent_cfg.api_base or self.api_base,
            "api_key": agent_cfg.api_key or self.api_key,
        }

    @classmethod
    def load(
        cls,
        project_root: Path,
        global_cfg: "GlobalConfig | None" = None,
    ) -> "LoomConfig":
        """从项目根目录加载配置，支持三层 fallback。

        模型解析优先级：
            novel.yaml agents.{name}.model
            → novel.yaml model
            → .opennovel.yaml default_model
            → 硬编码默认值 (DEFAULT_MODEL)

        Args:
            project_root: 项目根目录路径
            global_cfg: 全局配置实例（可选，未提供时自动加载）

        Returns:
            LoomConfig 实例
        """
        if global_cfg is None:
            global_cfg = GlobalConfig.load_from_project_root(project_root)

        config_path = project_root / "novel.yaml"
        if not config_path.exists():
            logger.info("配置文件不存在，使用默认配置: %s", config_path)
            return cls(model=global_cfg.default_model)

        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("配置文件读取失败，使用默认配置: %s", e)
            return cls(model=global_cfg.default_model)

        # Schema 校验：在解析前验证字段类型和范围
        try:
            _NovelConfigSchema(**data)
        except PydanticValidationError as e:
            errors = []
            for err in e.errors():
                errors.append(
                    {
                        "loc": err["loc"],
                        "msg": err["msg"],
                        "type": err["type"],
                    }
                )
            raise ConfigValidationError(
                errors=errors,
                config_path=str(config_path),
            ) from e

        # 解析 per-agent 配置
        agents_data = data.get("agents", {})
        agent_writer = _parse_agent_config(agents_data.get("writer", {}))
        agent_critic = _parse_agent_config(agents_data.get("critic", {}))
        agent_manager = _parse_agent_config(agents_data.get("manager", {}))
        agent_director = _parse_agent_config(agents_data.get("director", {}))

        # 提取已知字段，其余放入 extra
        known_keys = {
            "version",
            "model",
            "token_budget",
            "output_reserve",
            "api_base",
            "api_key",
            "agents",
            "creative_direction",
            "target_chapters",
            "words_per_chapter",
            "outline",
            "director_enabled",
            "embedding_model",
            "reranker_enabled",
            "reranker_model",
            "reranker_device",
            "search_top_k",
            "supports_native_tool_use",
            "max_tool_call_history",
            "safety_fence",
            "llm_cache_enabled",
            "llm_cache_path",
            "snapshot_max_count",
            "snapshot_max_days",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}

        # 三层 fallback 解析 model
        model = data.get("model") or global_cfg.default_model

        # 三层 fallback 解析 api_base
        api_base = data.get("api_base") or global_cfg.default_api_base

        # 解析安全围栏配置：用户配置与默认权限矩阵 safe-merge
        safety_fence_data = data.get("safety_fence") or {}
        safety_fence = _parse_safety_fence_config(safety_fence_data)

        return cls(
            version=str(data.get("version", DEFAULT_VERSION)),
            model=str(model),
            token_budget=int(data.get("token_budget", DEFAULT_TOKEN_BUDGET)),
            output_reserve=int(data.get("output_reserve", DEFAULT_OUTPUT_RESERVE)),
            api_base=api_base,
            api_key=data.get("api_key"),
            creative_direction=str(data.get("creative_direction", "")),
            target_chapters=int(data.get("target_chapters", 5)),
            words_per_chapter=int(data.get("words_per_chapter", 3000)),
            outline=str(data.get("outline", "outlines/story.md")),
            agent_writer=agent_writer,
            agent_critic=agent_critic,
            agent_manager=agent_manager,
            agent_director=agent_director,
            director_enabled=bool(data.get("director_enabled", True)),
            embedding_model=str(data.get("embedding_model", "local:BAAI/bge-m3")),
            reranker_enabled=bool(data.get("reranker_enabled", True)),
            reranker_model=str(data.get("reranker_model", "BAAI/bge-reranker-v2-m3")),
            reranker_device=str(data.get("reranker_device", "")),
            search_top_k=int(data.get("search_top_k", 5)),
            supports_native_tool_use=bool(data.get("supports_native_tool_use", False)),
            max_tool_call_history=int(data.get("max_tool_call_history", 5)),
            safety_fence=safety_fence,
            llm_cache_enabled=bool(data.get("llm_cache_enabled", True)),
            llm_cache_path=str(data.get("llm_cache_path", ".novel.cache.db")),
            snapshot_max_count=int(data.get("snapshot_max_count", 50)),
            snapshot_max_days=int(data.get("snapshot_max_days", 30)),
            extra=extra,
        )

    def save(self, project_root: Path) -> None:
        """将配置保存到 novel.yaml。

        Args:
            project_root: 项目根目录路径
        """
        config_path = project_root / "novel.yaml"
        data: dict = {
            "version": self.version,
            "model": self.model,
            "token_budget": self.token_budget,
            "output_reserve": self.output_reserve,
        }
        if self.api_base:
            data["api_base"] = self.api_base
        if self.api_key:
            data["api_key"] = self.api_key

        # 创作配置
        if self.creative_direction:
            data["creative_direction"] = self.creative_direction
        data["target_chapters"] = self.target_chapters
        data["words_per_chapter"] = self.words_per_chapter
        data["outline"] = self.outline

        # Director 配置
        data["director_enabled"] = self.director_enabled

        # 搜索配置 (ADR 0007)
        if self.embedding_model != "local:BAAI/bge-m3":
            data["embedding_model"] = self.embedding_model
        data["reranker_enabled"] = self.reranker_enabled
        if self.reranker_model != "BAAI/bge-reranker-v2-m3":
            data["reranker_model"] = self.reranker_model
        if self.reranker_device:
            data["reranker_device"] = self.reranker_device
        data["search_top_k"] = self.search_top_k

        # Agent 自治配置 (Phase 3)
        data["supports_native_tool_use"] = self.supports_native_tool_use
        if self.max_tool_call_history != 5:
            data["max_tool_call_history"] = self.max_tool_call_history

        # LLM 输入缓存配置（P1）
        if not self.llm_cache_enabled:
            data["llm_cache_enabled"] = self.llm_cache_enabled
        if self.llm_cache_path != ".novel.cache.db":
            data["llm_cache_path"] = self.llm_cache_path

        # 快照清理策略（P3，仅保存非默认值）
        if self.snapshot_max_count != 50:
            data["snapshot_max_count"] = self.snapshot_max_count
        if self.snapshot_max_days != 30:
            data["snapshot_max_days"] = self.snapshot_max_days

        # 安全围栏配置（仅保存非默认值或用户自定义部分）
        sf_data: dict = {}
        if self.safety_fence.forbidden_modifications:
            sf_data["forbidden_modifications"] = self.safety_fence.forbidden_modifications
        if self.safety_fence.tool_permissions:
            sf_data["tool_permissions"] = self.safety_fence.tool_permissions
        if self.safety_fence.llm_canon_audit_enabled:
            sf_data["llm_canon_audit_enabled"] = self.safety_fence.llm_canon_audit_enabled
        if sf_data:
            data["safety_fence"] = sf_data

        # per-agent 配置
        agents: dict = {}
        for name, cfg in [
            ("writer", self.agent_writer),
            ("critic", self.agent_critic),
            ("manager", self.agent_manager),
            ("director", self.agent_director),
        ]:
            agent_data: dict = {}
            if cfg.model:
                agent_data["model"] = cfg.model
            if cfg.api_base:
                agent_data["api_base"] = cfg.api_base
            if cfg.api_key:
                agent_data["api_key"] = cfg.api_key
            if cfg.think_model:
                agent_data["think_model"] = cfg.think_model
            if cfg.write_model:
                agent_data["write_model"] = cfg.write_model
            if cfg.revise_model:
                agent_data["revise_model"] = cfg.revise_model
            if agent_data:
                agents[name] = agent_data
        if agents:
            data["agents"] = agents

        data.update(self.extra)

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            logger.info("配置已保存: %s", config_path)
        except Exception as e:
            logger.error("配置保存失败: %s", e)


def _parse_agent_config(data: dict) -> AgentConfig:
    """从 YAML 数据解析 AgentConfig。

    Args:
        data: YAML 中 agents.writer / agents.critic / agents.manager 的值

    Returns:
        AgentConfig 实例
    """
    if not data:
        return AgentConfig()
    return AgentConfig(
        model=data.get("model"),
        api_base=data.get("api_base"),
        api_key=data.get("api_key"),
        think_model=data.get("think_model"),
        write_model=data.get("write_model"),
        write_model_climax=data.get("write_model_climax"),
        revise_model=data.get("revise_model"),
    )


def _parse_safety_fence_config(data: dict) -> _SafetyFenceConfig:
    """从 YAML 数据解析 SafetyFenceConfig，并与默认权限矩阵 safe-merge。

    合并规则：
    - 用户未提供 tool_permissions 时，使用 DEFAULT_TOOL_PERMISSIONS。
    - 用户提供了某个 agent 的权限时，完全覆盖该 agent 的默认权限（不逐字段合并）。
    - 其他安全围栏字段（max_recursion_depth 等）按用户值覆盖默认值。

    Args:
        data: YAML 中 safety_fence 字段的值

    Returns:
        SafetyFenceConfig 实例
    """
    if not data:
        return _SafetyFenceConfig(tool_permissions=dict(DEFAULT_TOOL_PERMISSIONS))

    tool_permissions: dict[str, dict[str, list[str]]] = dict(DEFAULT_TOOL_PERMISSIONS)
    user_tool_permissions = data.get("tool_permissions") or {}
    for agent, perms in user_tool_permissions.items():
        if isinstance(perms, dict):
            tool_permissions[agent] = {
                "allowed": list(perms.get("allowed", [])),
                "disallowed": list(perms.get("disallowed", [])),
            }

    return _SafetyFenceConfig(
        max_recursion_depth=int(
            data.get("max_recursion_depth", _SafetyFenceConfig().max_recursion_depth)
        ),
        max_tokens_per_call=int(
            data.get("max_tokens_per_call", _SafetyFenceConfig().max_tokens_per_call)
        ),
        timeout_seconds=int(data.get("timeout_seconds", _SafetyFenceConfig().timeout_seconds)),
        forbidden_modifications=list(
            data.get("forbidden_modifications", _SafetyFenceConfig().forbidden_modifications)
        ),
        canon_dir=data.get("canon_dir", _SafetyFenceConfig().canon_dir),
        enabled=bool(data.get("enabled", _SafetyFenceConfig().enabled)),
        llm_canon_audit_enabled=bool(
            data.get("llm_canon_audit_enabled", _SafetyFenceConfig().llm_canon_audit_enabled)
        ),
        tool_permissions=tool_permissions,
    )
