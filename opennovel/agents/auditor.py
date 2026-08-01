"""Auditor 代理 - 状态审阅与提取代理人格。

Auditor 是 OpenNovel 的"审稿官"，核心职责：
- 分析正文，提取结构化事件（伤势、物品、情绪变化等）
- 强制输出 JSON 格式的事件列表，经 Pydantic 校验
- 当 LLM 输出非法 JSON 时，自动重试（最多 3 次）并反馈错误信息
- 3 次失败后触发人类急救模式：[E]dit / [S]kip（脏提交）/ [A]bort
- 绝不直接写入状态，必须经过人工确认

铁律 3：AI 只能提议状态变更，人类拥有绝对否决权。
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from opennovel.core.llm import LLMBus, extract_text_from_response
from opennovel.core.state_manager import StateManager
from opennovel.schemas.event import EventCreate, EventDiff
from opennovel.storage.yaml_storage import YAMLStorage

logger = logging.getLogger(__name__)

MAX_RETRIES = 2  # 初始尝试外最多重试 2 次（共 3 次）


class AuditorAbortError(Exception):
    """用户选择终止 commit 时抛出。"""

    pass


@dataclass
class ExtractionResult:
    """Auditor 提取结果，包含事件列表和提取状态。"""

    events: list[EventCreate] = field(default_factory=list)
    success: bool = True
    dirty: bool = False
    error: str | None = None


class Auditor:
    """Auditor 代理 - 状态审阅与提取引擎。

    使用方式:
        auditor = Auditor(llm_bus=bus, state_manager=sm, project_root=root)
        result = auditor.extract_events_with_retry(chapter_id, chapter_text)
        if result.success:
            diffs = auditor.generate_diffs(result.events)
            # 展示 diffs 给用户审阅
            auditor.apply_confirmed_events(result.events, chapter_id)
    """

    def __init__(
        self,
        llm_bus: LLMBus,
        state_manager: StateManager,
        project_root: Path,
        prompt_path: Path | None = None,
        yaml_storage: YAMLStorage | None = None,
    ) -> None:
        """初始化 Auditor 代理。

        Args:
            llm_bus: LLM 调用总线实例
            state_manager: 状态管理器实例
            project_root: 项目根目录路径
            prompt_path: Auditor 人格 Prompt 文件路径
            yaml_storage: YAML 存储实例，用于写 dirty_flag
        """
        self.llm_bus = llm_bus
        self.state_manager = state_manager
        self.project_root = project_root
        self.prompt_path = prompt_path or project_root / "prompts" / "auditor.v1.md"
        self._yaml_storage = yaml_storage or YAMLStorage()

    def _load_prompt(self) -> str:
        """加载 Auditor 人格 Prompt。

        Returns:
            Prompt 文本内容
        """
        if not self.prompt_path.exists():
            logger.warning("Auditor Prompt 文件不存在: %s", self.prompt_path)
            return "你是一个叙事状态审计员。分析正文，提取结构化事件。"
        return self.prompt_path.read_text(encoding="utf-8")

    def extract_events(
        self,
        chapter_id: str,
        chapter_text: str,
        active_characters: list[str] | None = None,
    ) -> list[EventCreate]:
        """（向后兼容）从正文中提取结构化事件，旧版本单次调用接口。

        建议使用 extract_events_with_retry 获得完整重试逻辑。

        Args:
            chapter_id: 章节 ID
            chapter_text: 章节正文
            active_characters: 活跃角色 ID 列表

        Returns:
            提取到的事件列表
        """
        result = self.extract_events_with_retry(chapter_id, chapter_text, active_characters)
        return result.events

    def extract_events_with_retry(
        self,
        chapter_id: str,
        chapter_text: str,
        active_characters: list[str] | None = None,
    ) -> ExtractionResult:
        """从正文中提取结构化事件，含自动重试纠偏和人类急救模式。

        重试逻辑：
        - 最多 3 次尝试（1 初始 + 2 重试）
        - 每次重试将错误诊断和失败输出喂给 LLM 进行定向修复
        - 成功 → 返回 ExtractionResult(events, success=True)
        - 3 次全失败 → 进入人类急救模式

        Args:
            chapter_id: 章节 ID
            chapter_text: 章节正文
            active_characters: 活跃角色 ID 列表

        Returns:
            ExtractionResult：包含事件列表和提取状态
        """
        prompt = self._load_prompt()
        characters_info = ""
        if active_characters:
            characters_info = f"活跃角色 ID: {', '.join(active_characters)}"

        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"章节 ID: {chapter_id}\n"
                    f"{characters_info}\n\n"
                    f"正文内容:\n{chapter_text}\n\n"
                    f"请提取所有状态变更事件，输出 JSON 数组。"
                ),
            },
        ]

        last_error: str | None = None
        last_raw_text: str | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.llm_bus.chat(messages, temperature=0.1)
                raw_text = extract_text_from_response(response)

                if not raw_text:
                    last_error = "LLM 返回空文本"
                    last_raw_text = raw_text
                    logger.warning("Auditor 提取为空（第 %d 次）", attempt + 1)
                    if attempt < MAX_RETRIES:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "你的输出为空。请输出一个合法的 JSON 事件数组，"
                                    "如果没有事件则输出 []。"
                                ),
                            }
                        )
                    continue

                events = self._parse_events_from_text(raw_text, chapter_id)
                return ExtractionResult(events=events, success=True)

            except (json.JSONDecodeError, ValidationError) as e:
                last_error = f"{type(e).__name__}: {str(e)}"
                last_raw_text = raw_text
                logger.warning(
                    "Auditor 输出校验失败（第 %d 次）: %s",
                    attempt + 1,
                    last_error,
                )

                if attempt < MAX_RETRIES:
                    # 构建纠偏 Prompt，让 LLM 看着自己的错误修正
                    error_feedback = (
                        f"你上次输出的 JSON 格式校验失败。\n\n"
                        f"错误信息：{last_error}\n\n"
                        f"你输出的内容：\n{raw_text}\n\n"
                        f"请修正错误，只输出合法的 JSON 事件数组。"
                    )
                    messages.append({"role": "assistant", "content": raw_text})
                    messages.append({"role": "user", "content": error_feedback})
                else:
                    # 超过最大重试次数，进入人类急救模式
                    return self._trigger_rescue_mode(chapter_id, last_raw_text or "", last_error)

        # 不应该到达这里，但防御性返回
        return ExtractionResult(events=[], success=False, error=last_error)

    def _parse_events_from_text(self, text: str, chapter_id: str) -> list[EventCreate]:
        """解析 LLM 输出的 JSON 事件列表。

        Args:
            text: LLM 输出的文本
            chapter_id: 章节 ID（用于补充缺失字段）

        Returns:
            校验通过的事件列表

        Raises:
            json.JSONDecodeError: JSON 解析失败
            ValidationError: Pydantic 校验失败
        """
        # 清理可能的 markdown 代码块标记
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # 移除 ```json 或 ``` 标记行
            cleaned = "\n".join(line for line in lines if not line.strip().startswith("```"))

        raw_events = json.loads(cleaned)
        if not isinstance(raw_events, list):
            raw_events = [raw_events]

        events: list[EventCreate] = []
        for raw in raw_events:
            raw.setdefault("chapter_id", chapter_id)
            event = EventCreate(**raw)
            events.append(event)

        return events

    def _trigger_rescue_mode(
        self,
        chapter_id: str,
        failed_output: str,
        error_msg: str,
    ) -> ExtractionResult:
        """当 LLM 连续 3 次无法输出合法 JSON 时，将控制权交还给人类。

        Args:
            chapter_id: 章节 ID
            failed_output: 最后一次失败输出的文本
            error_msg: 最后一次失败的错误信息

        Returns:
            ExtractionResult 根据用户选择
        """
        from rich.console import Console
        from rich.panel import Panel
        from rich.prompt import Prompt

        console = Console()

        console.print(
            Panel(
                f"[bold red]Auditor 提取失败（连续 {MAX_RETRIES + 1} 次）[/bold red]\n"
                f"错误信息: {error_msg}\n\n"
                f"最后的输出:\n{failed_output}",
                title="🚨 系统警告",
            )
        )

        choice = Prompt.ask(
            "请选择操作",
            choices=["e", "s", "a"],
            default="a",
        )

        if choice == "e":
            # [E]dit：手动修补 JSON
            return self._rescue_edit(chapter_id, failed_output)

        elif choice == "s":
            # [S]kip：脏提交
            return self._rescue_skip(chapter_id)

        else:
            # [A]bort：终止 commit
            raise AuditorAbortError("用户终止 commit")

    def _rescue_edit(
        self,
        chapter_id: str,
        failed_output: str,
    ) -> ExtractionResult:
        """[E]dit 模式：用户手动修补 JSON。

        Args:
            chapter_id: 章节 ID
            failed_output: 失败输出的文本

        Returns:
            修补后校验通过的结果
        """
        from rich.console import Console

        console = Console()
        console.print("[yellow]请输入修补后的合法 JSON 数组（或输入空行取消）:[/yellow]")

        # 使用临时编辑器或直接输入
        # 简单方案：直接粘贴 JSON
        console.print("[dim]将你的 JSON 粘贴到下面，输入 END 结束:[/dim]")

        lines: list[str] = []
        while True:
            try:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)
            except (EOFError, KeyboardInterrupt):
                break

        if not lines:
            console.print("[yellow]未提供有效 JSON，放弃修补[/yellow]")
            return ExtractionResult(events=[], success=False, error="用户取消手动编辑")

        try:
            fixed = "\n".join(lines)
            events = self._parse_events_from_text(fixed, chapter_id)
            console.print(f"[green]✓ 手动 JSON 校验通过，{len(events)} 个事件[/green]")
            return ExtractionResult(events=events, success=True)
        except (json.JSONDecodeError, ValidationError) as e:
            console.print(f"[red]手动编辑的 JSON 仍然无效: {e}[/red]")
            return ExtractionResult(events=[], success=False, error=str(e))

    def _rescue_skip(self, chapter_id: str) -> ExtractionResult:
        """[S]kip 模式：脏提交，在 Frontmatter 中打上 dirty_flag。

        Args:
            chapter_id: 章节 ID

        Returns:
            脏提交结果
        """
        from rich.console import Console

        console = Console()
        console.print(
            "[bold yellow]执行脏提交：章节将被打上 'extraction_failed' 标记[/bold yellow]"
        )

        # 在章节文件的 Frontmatter 中写入 dirty_flag
        chapter_path = self.project_root / "draft" / f"{chapter_id}.md"
        if chapter_path.exists():
            try:
                self._yaml_storage.update_frontmatter(
                    chapter_path,
                    {"dirty_flag": "extraction_failed"},
                )
                logger.info("脏标记已写入: %s", chapter_path)
            except Exception as e:
                logger.warning("脏标记写入失败: %s", e)

        return ExtractionResult(
            events=[],
            success=False,
            dirty=True,
            error="脏提交：Auditor 提取失败",
        )

    def generate_diffs(self, events: list[EventCreate]) -> list[EventDiff]:
        """将提取的事件转换为 Diff 格式，用于终端展示。

        Args:
            events: 提取到的事件列表

        Returns:
            事件变更 Diff 列表
        """
        return [EventDiff(action="add", event=event) for event in events]

    def apply_confirmed_events(self, events: list[EventCreate], chapter_id: str) -> list[str]:
        """将经过人工确认的事件写入状态。

        Args:
            events: 人工确认后的事件列表
            chapter_id: 章节 ID

        Returns:
            写入的事件 ID 列表
        """
        event_ids: list[str] = []
        for event in events:
            self.state_manager.apply_event(event)
            event_ids.append(event.event_id)
        return event_ids
