"""Manager Agent - 叙事状态管理代理。

负责从已确认合格的章节中提取角色状态变更，
通过 StateManager 应用到 YAML Frontmatter 和 SQLite 事件账本。
"""

import json
import logging
from pathlib import Path

from opennovel.core.llm import LLMBus
from opennovel.core.state_manager import StateManager
from opennovel.schemas.event import EventCreate
from opennovel.schemas.manager_update import ManagerUpdateResult
from opennovel.storage.yaml_storage import YAMLStorage

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class Manager:
    """Manager Agent — 叙事状态管理员。

    使用方式:
        manager = Manager(llm_bus=llm_bus, state_manager=sm, project_root=root)
        result = manager.update("ch_001", chapter_text, ["char_001", "char_002"])
    """

    def __init__(
        self,
        llm_bus: LLMBus,
        state_manager: StateManager,
        project_root: Path,
        prompt_path: Path | None = None,
        yaml_storage: YAMLStorage | None = None,
    ) -> None:
        self.llm_bus = llm_bus
        self.state_manager = state_manager
        self.project_root = project_root
        self.prompt_path = prompt_path or (
            Path(__file__).parent.parent / "prompts" / "manager.v1.md"
        )
        self._yaml_storage = yaml_storage

    @property
    def yaml_storage(self) -> YAMLStorage:
        if self._yaml_storage is None:
            self._yaml_storage = YAMLStorage()
        return self._yaml_storage

    def _load_prompt(self) -> str:
        """加载 Manager Prompt，文件不存在时返回硬编码兜底。"""
        if not self.prompt_path.exists():
            logger.warning("Manager Prompt 文件不存在: %s", self.prompt_path)
            return "你是叙事状态管理员。从章节中提取角色状态变更。"
        return self.prompt_path.read_text(encoding="utf-8")

    def _get_character_states(self, active_characters: list[str]) -> str:
        """获取活跃角色的当前状态文本。"""
        summaries = []
        for char_id in active_characters:
            char_path = self.project_root / "characters" / f"{char_id}.md"
            if not char_path.exists():
                continue
            try:
                char = self.yaml_storage.read_character_file(char_path)
                fm = char.frontmatter
                summary = (
                    f"角色: {fm.name} (ID: {fm.id})\n"
                    f"  位置: {fm.location or '未知'}\n"
                    f"  情绪: grief={fm.emotional.grief} anger={fm.emotional.anger} "
                    f"fear={fm.emotional.fear} joy={fm.emotional.joy} "
                    f"determination={fm.emotional.determination}\n"
                    f"  物品: {', '.join(fm.inventory) if fm.inventory else '无'}\n"
                    f"  知识: {', '.join(fm.knowledge) if fm.knowledge else '无'}"
                )
                if fm.physical.injuries:
                    summary += f"\n  伤势: {', '.join(fm.physical.injuries)}"
                if fm.physical.buffs:
                    summary += f"\n  增益: {', '.join(fm.physical.buffs)}"
                if fm.physical.debuffs:
                    summary += f"\n  减益: {', '.join(fm.physical.debuffs)}"
                summaries.append(summary)
            except Exception as e:
                logger.warning("读取角色文件失败 %s: %s", char_path, e)

        return "\n\n".join(summaries)

    def _get_recent_events_context(self, limit: int = 10) -> str:
        """获取近期高因果压强事件，用于因果链推断。"""
        try:
            store = self.state_manager.event_store
            events = store.get_high_pressure_events(threshold=0.5)
            if not events:
                return "暂无历史事件记录。"

            # 取最近 N 条
            recent = events[:limit]
            lines = []
            for evt in recent:
                chain_info = ""
                if evt.caused_by:
                    chain_info = f" [因果: 由 {evt.caused_by} 引起]"
                lines.append(
                    f"- {evt.event_id} ({evt.event_type}): {evt.description} "
                    f"[压强={evt.causal_pressure}]{chain_info}"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning("获取历史事件上下文失败: %s", e)
            return "无法获取历史事件。"

    def _build_messages(
        self,
        chapter_id: str,
        chapter_text: str,
        active_characters: list[str],
    ) -> list[dict[str, str]]:
        """组装 Manager 消息列表，包含因果链上下文。"""
        prompt = self._load_prompt()
        states = self._get_character_states(active_characters)
        recent_events = self._get_recent_events_context()

        user_content = f"""## 状态提取任务

请从章节 `{chapter_id}` 中提取所有角色状态变更。

### 活跃角色
{", ".join(active_characters)}

### 角色当前状态
{states or "无角色状态信息"}

### 历史事件（用于因果链推断）
{recent_events}

### 章节正文

{chapter_text}

请输出合法的 JSON 对象：
{{
  "character_updates": [
    {{
      "character_id": "char_001",
      "field": "emotional.grief",
      "value": 0.8,
      "reason": "变更原因"
    }}
  ],
  "events": [
    {{
      "event_id": "evt_{chapter_id}_001",
      "character_id": "char_001",
      "event_type": "INJURY",
      "description": "事件描述",
      "causal_pressure": 0.7,
      "timestamp": "故事内时间",
      "caused_by": "evt_xxx（前置事件 ID，可选）",
      "related_event_ids": ["evt_yyy（关联事件 ID 列表，可选）"]
    }}
  ],
  "chapter_summary": "本章摘要（300字以内）"
}}

### 因果链规则
- `caused_by`: 仅当本事件**直接由**某个已有事件引起时填写（如：受伤 → 后续治疗）
- `related_event_ids`: 叙事上相关但无直接因果的事件（如：同一场战斗中的多个事件）
- 如果无法确定因果关系，这两个字段留 null"""

        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]

    def _parse_update_from_text(self, text: str) -> ManagerUpdateResult:
        """从 LLM 输出中解析 ManagerUpdateResult JSON。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)

        data = json.loads(cleaned)
        return ManagerUpdateResult(**data)

    def _apply_updates(self, result: ManagerUpdateResult) -> list[str]:
        """将 Manager 更新结果应用到文件系统。

        Returns:
            已应用的事件 ID 列表
        """
        applied_event_ids = []

        # 应用角色状态更新
        for update in result.character_updates:
            try:
                # 解析字段路径 (如 "emotional.grief" → 更新 emotional 子对象)
                updates = self._build_nested_update(update.field, update.value)
                self.state_manager.apply_character_diff(update.character_id, updates)
                logger.info(
                    "角色更新: %s.%s = %s (%s)",
                    update.character_id,
                    update.field,
                    update.value,
                    update.reason,
                )
            except Exception as e:
                logger.error("角色更新失败 %s.%s: %s", update.character_id, update.field, e)

        # 写入事件账本
        for event in result.events:
            try:
                event_create = EventCreate(
                    event_id=event.event_id,
                    chapter_id="",  # 由 StateManager 填充
                    timestamp=event.timestamp,
                    character_id=event.character_id,
                    event_type=event.event_type,
                    description=event.description,
                    causal_pressure=event.causal_pressure,
                    caused_by=event.caused_by,
                    related_event_ids=event.related_event_ids,
                )
                self.state_manager.apply_event(event_create)
                applied_event_ids.append(event.event_id)
                logger.info("事件记录: %s - %s", event.event_id, event.description)
            except Exception as e:
                logger.error("事件记录失败 %s: %s", event.event_id, e)

        return applied_event_ids

    def _build_nested_update(self, field_path: str, value: object) -> dict:
        """将扁平的字段路径转为嵌套字典。

        例如: "emotional.grief" + 0.8 → {"emotional": {"grief": 0.8}}
        """
        parts = field_path.split(".")
        result: dict = {}
        current = result
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                current[part] = value
            else:
                current[part] = {}
                current = current[part]
        return result

    def update(
        self,
        chapter_id: str,
        chapter_text: str,
        active_characters: list[str],
    ) -> ManagerUpdateResult:
        """从章节中提取状态变更并应用。

        Args:
            chapter_id: 章节 ID
            chapter_text: 章节正文
            active_characters: 活跃角色 ID 列表

        Returns:
            ManagerUpdateResult 更新结果
        """
        messages = self._build_messages(chapter_id, chapter_text, active_characters)

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.llm_bus.chat(messages, temperature=0.1)
                text = response.choices[0].message.content
                if not text:
                    raise ValueError("LLM 返回空文本")
                result = self._parse_update_from_text(text)

                # 应用更新
                event_ids = self._apply_updates(result)
                logger.info(
                    "Manager 更新完成: %s, %d 个角色更新, %d 个事件",
                    chapter_id,
                    len(result.character_updates),
                    len(event_ids),
                )
                return result
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                logger.warning(
                    "Manager 提取 JSON 解析失败 (尝试 %d/%d): %s", attempt + 1, MAX_RETRIES + 1, e
                )
                if attempt < MAX_RETRIES:
                    messages.append({"role": "assistant", "content": text or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": f"你的输出 JSON 格式有误: {e}\n请重新输出合法的 JSON 对象。",
                        }
                    )

        raise RuntimeError(f"Manager 更新失败，已重试 {MAX_RETRIES} 次: {last_error}")
