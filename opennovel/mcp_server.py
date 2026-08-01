"""OpenNovel MCP Server - 通过 MCP 协议暴露 OpenNovel 创作能力。

提供 4 个 tools:
- init_project: 初始化小说项目
- get_status: 读取项目状态
- write_chapter: Writer Agent 创作单章
- auto_create: 三 Agent 全自动创作

使用方式:
    loom-mcp                    # 启动 stdio MCP Server
    python -m loom.mcp_server   # 等效启动
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

from mcp import types

from opennovel.agents.critic import Critic
from opennovel.agents.writer import Writer
from opennovel.core.auto_runner import AutoRunner
from opennovel.core.config import LoomConfig
from opennovel.core.llm import LLMBus
from opennovel.core.retriever import Retriever
from opennovel.storage.yaml_storage import YAMLStorage
from mcp.server import Server
from mcp.server.stdio import stdio_server

logger = logging.getLogger(__name__)

server = Server("novel-mcp")


# ── Tool 定义 ────────────────────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """返回 OpenNovel MCP Server 暴露的所有 tools。"""
    return [
        types.Tool(
            name="init_project",
            description=(
                "初始化 OpenNovel 小说项目。创建标准目录结构（canon/characters/draft/"
                "outlines/subconscious/.snapshots）和模板文件。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径，默认当前目录",
                        "default": ".",
                    },
                },
            },
        ),
        types.Tool(
            name="get_status",
            description=(
                "读取 OpenNovel 项目状态：角色列表、章节列表、配置信息、"
                "健康检查结果。用于了解项目当前情况。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径，默认当前目录",
                        "default": ".",
                    },
                },
            },
        ),
        types.Tool(
            name="write_chapter",
            description=(
                "用 Writer Agent 创作单个章节。先进行思考规划（输出大纲），"
                "再创作正文，最后由 Critic 评分。返回章节正文和评分结果。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径",
                    },
                    "chapter_id": {
                        "type": "string",
                        "description": "章节 ID，如 ch_001",
                    },
                    "chapter_hint": {
                        "type": "string",
                        "description": "本章写作提示（可选，从大纲中提取）",
                    },
                },
                "required": ["path", "chapter_id"],
            },
        ),
        types.Tool(
            name="auto_create",
            description=(
                "运行三 Agent 自主创作循环（Writer→Critic→Manager）。"
                "全自动完成所有章节的创作。需要项目已初始化且大纲文件存在。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径",
                    },
                    "chapters": {
                        "type": "integer",
                        "description": "覆盖章节数（可选）",
                    },
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="commit",
            description=(
                "提取章节状态变更并固化。运行 Auditor 提取事件 → 展示 Diff → "
                "写入 EventStore。相当于 novel commit 命令。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径",
                    },
                    "chapter": {
                        "type": "string",
                        "description": "章节文件名，如 ch_001",
                    },
                    "model": {
                        "type": "string",
                        "description": "用于 Auditor 的 LLM 模型（可选，默认使用项目配置）",
                    },
                },
                "required": ["path", "chapter"],
            },
        ),
        types.Tool(
            name="stash",
            description=(
                "存入灵感到潜意识池。灵感内容会写入 subconscious/ 目录，"
                "并更新向量索引供后续检索使用。相当于 novel stash 命令。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "灵感文本内容",
                    },
                    "source": {
                        "type": "string",
                        "description": "来源标注（可选，如 'user'），便于追溯",
                    },
                },
                "required": ["path", "content"],
            },
        ),
        types.Tool(
            name="diff",
            description=(
                "检查正文与 Shadow（Frontmatter）之间的一致性。"
                "检测角色引用、POV 设置等差异。相当于 novel diff 命令。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径",
                    },
                    "chapter": {
                        "type": "string",
                        "description": "章节文件名（可选，默认检查所有章节）",
                    },
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="doctor",
            description=(
                "运行项目健康度诊断。返回项目健康面板，包含配置状态、"
                "章节进度、角色统计、事件统计和诊断问题列表。"
                "相当于 novel doctor 命令。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径",
                    },
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="foreshadow",
            description=(
                "查看/管理伏笔追踪表。支持 list（列出所有伏笔）、"
                "add（添加新伏笔）、resolve（标记伏笔已回收）三种操作。"
                "相当于 novel foreshadow 命令。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "resolve"],
                        "description": "操作类型: list（列出）/ add（添加）/ resolve（回收）",
                    },
                    "description": {
                        "type": "string",
                        "description": "伏笔描述（add 操作时必填）",
                    },
                    "id": {
                        "type": "string",
                        "description": "伏笔 ID（resolve 操作时必填，如 F001）",
                    },
                },
                "required": ["path", "action"],
            },
        ),
    ]


# ── Tool 实现 ────────────────────────────────────────────────────────────────


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """分发 tool 调用到对应的处理函数。"""
    handlers = {
        "init_project": _handle_init_project,
        "get_status": _handle_get_status,
        "write_chapter": _handle_write_chapter,
        "auto_create": _handle_auto_create,
        "commit": _handle_commit,
        "stash": _handle_stash,
        "diff": _handle_diff,
        "doctor": _handle_doctor,
        "foreshadow": _handle_foreshadow,
    }

    handler = handlers.get(name)
    if not handler:
        return [types.TextContent(type="text", text=f"未知 tool: {name}")]

    try:
        result = await handler(arguments)
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        logger.error("Tool %s 执行失败: %s", name, e)
        return [types.TextContent(type="text", text=f"执行失败: {e}")]


async def _handle_init_project(args: dict) -> str:
    """初始化 OpenNovel 项目。"""
    path = Path(args.get("path", ".")).resolve()

    storage = YAMLStorage()
    results = []

    # 创建目录
    directories = ["canon", "characters", "draft", "outlines", "subconscious", ".snapshots"]
    for dir_name in directories:
        dir_path = path / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        results.append(f"✓ 创建目录: {dir_name}/")

    # 角色模板
    char_template = {
        "id": "char_001",
        "name": "角色名",
        "aliases": [],
        "location": None,
        "physical": {"injuries": [], "buffs": [], "debuffs": []},
        "emotional": {"grief": 0.0, "anger": 0.0, "fear": 0.0, "joy": 0.0, "determination": 0.0},
        "inventory": [],
        "knowledge": [],
    }
    char_path = path / "characters" / "char_001.md"
    if not char_path.exists():
        storage.write_markdown_file(
            char_path,
            char_template,
            "# 角色背景\n\n在此自由书写角色的背景故事...",
        )
        results.append("✓ 创建角色模板: characters/char_001.md")

    # 设定模板
    canon_template = {"id": "world_rules", "type": "world_rules"}
    canon_path = path / "canon" / "world_rules.md"
    if not canon_path.exists():
        storage.write_markdown_file(
            canon_path,
            canon_template,
            "# 世界观设定\n\n在此书写不可违反的世界观规则...",
        )
        results.append("✓ 创建设定模板: canon/world_rules.md")

    # 章节模板
    chapter_template = {
        "id": "ch_001",
        "title": "第一章",
        "pov": "char_001",
        "active_characters": ["char_001"],
    }
    chapter_path = path / "draft" / "ch_001.md"
    if not chapter_path.exists():
        storage.write_markdown_file(chapter_path, chapter_template, "# 第一章\n\n")
        results.append("✓ 创建章节模板: draft/ch_001.md")

    # novel.yaml
    import yaml

    config_path = path / "novel.yaml"
    if not config_path.exists():
        config = {
            "version": "1.0.1",
            "model": "gpt-4",
            "token_budget": 8000,
            "output_reserve": 2000,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        results.append("✓ 创建配置文件: novel.yaml")

    return f"项目初始化完成: {path}\n" + "\n".join(results)


async def _handle_get_status(args: dict) -> str:
    """读取项目状态。"""
    path = Path(args.get("path", ".")).resolve()

    storage = YAMLStorage()
    config = LoomConfig.load(path)

    sections = []

    # 配置信息
    sections.append("## 配置")
    sections.append(f"- 模型: {config.model}")
    sections.append(f"- Token 预算: {config.token_budget}")
    sections.append(f"- 创作方向: {config.creative_direction or '未设置'}")
    sections.append(f"- 目标章节: {config.target_chapters}")
    sections.append(f"- 每章字数: {config.words_per_chapter}")

    # 角色列表
    chars_dir = path / "characters"
    if chars_dir.exists():
        char_files = sorted(chars_dir.glob("char_*.md"))
        sections.append(f"\n## 角色 ({len(char_files)} 个)")
        for cf in char_files:
            try:
                char = storage.read_character_file(cf)
                sections.append(
                    f"- {char.frontmatter.id}: {char.frontmatter.name} "
                    f"@ {char.frontmatter.location or '未知位置'}"
                )
            except Exception:
                sections.append(f"- {cf.stem}: (读取失败)")

    # 章节列表
    draft_dir = path / "draft"
    if draft_dir.exists():
        ch_files = sorted(draft_dir.glob("ch_*.md"))
        sections.append(f"\n## 章节 ({len(ch_files)} 个)")
        for cf in ch_files:
            try:
                meta, body = storage.read_markdown_file(cf)
                word_count = len(body)
                sections.append(
                    f"- {meta.get('id', cf.stem)}: {meta.get('title', '无标题')} ({word_count} 字)"
                )
            except Exception:
                sections.append(f"- {cf.stem}: (读取失败)")

    # 大纲
    outline_path = path / config.outline
    if outline_path.exists():
        content = outline_path.read_text(encoding="utf-8")
        chapter_count = sum(1 for line in content.split("\n") if line.startswith("## "))
        sections.append(f"\n## 大纲")
        sections.append(f"- 文件: {config.outline}")
        sections.append(f"- 章节数: {chapter_count}")
    else:
        sections.append(f"\n## 大纲")
        sections.append(f"- 未创建 ({config.outline})")

    return "\n".join(sections)


async def _handle_write_chapter(args: dict) -> str:
    """Writer Agent 创作单章。"""
    path = Path(args.get("path", ".")).resolve()
    chapter_id = args.get("chapter_id", "")
    chapter_hint = args.get("chapter_hint", "")

    if not chapter_id:
        return "错误: 必须提供 chapter_id"

    config = LoomConfig.load(path)

    # 初始化组件
    llm_bus = LLMBus(
        model=config.model,
        api_base=config.api_base,
        api_key=config.api_key,
    )
    retriever = Retriever(path)
    # 构建/加载向量索引
    index_dir = retriever._index_dir
    if not (index_dir / "canon").exists() or not any((index_dir / "canon").iterdir()):
        canon_dir = path / "canon"
        if canon_dir.exists() and any(canon_dir.glob("*.md")):
            retriever.build_canon_index()
    else:
        retriever._canon_store.load_index()
    if not (index_dir / "subconscious").exists() or not any(
        (index_dir / "subconscious").iterdir()
    ):
        sub_dir = path / "subconscious"
        if sub_dir.exists() and any(sub_dir.glob("*.md")):
            retriever.build_subconscious_index()
    else:
        retriever._subconscious_store.load_index()

    writer = Writer(
        llm_bus=llm_bus,
        retriever=retriever,
        project_root=path,
        creative_direction=config.creative_direction,
        words_per_chapter=config.words_per_chapter,
    )
    critic = Critic(llm_bus=llm_bus, project_root=path)

    # 获取前一章摘要（简单实现）
    storage = YAMLStorage()
    previous_summary = ""
    previous_text = ""

    # Writer 思考
    outline = writer.think(chapter_id, chapter_hint or f"创作 {chapter_id}")

    # Writer 创作
    chapter_text = writer.write(chapter_id, outline, previous_text)

    # Critic 评分
    evaluation = critic.evaluate(chapter_id, chapter_text, outline)

    # 写入文件
    chapter_path = path / "draft" / f"{chapter_id}.md"
    chapter_meta = {
        "id": chapter_id,
        "title": outline.title,
        "pov": outline.scenes[0].characters_involved[0] if outline.scenes else "",
        "active_characters": list(outline.character_arcs.keys()),
    }
    storage.write_markdown_file(chapter_path, chapter_meta, chapter_text)

    result = {
        "chapter_id": chapter_id,
        "title": outline.title,
        "word_count": len(chapter_text),
        "score": evaluation.total_score,
        "is_pass": evaluation.is_pass,
        "dimensions": {d.dimension: d.score for d in evaluation.dimensions},
        "file_path": str(chapter_path),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


async def _handle_auto_create(args: dict) -> str:
    """三 Agent 全自动创作。"""
    path = Path(args.get("path", ".")).resolve()
    chapters = args.get("chapters")

    config = LoomConfig.load(path)
    if chapters:
        config.target_chapters = chapters

    # 读取大纲
    outline_path = path / config.outline
    if not outline_path.exists():
        return f"错误: 大纲文件不存在 ({outline_path})"

    outline_text = outline_path.read_text(encoding="utf-8")
    if not outline_text.strip():
        return "错误: 大纲文件为空"

    # 执行创作
    runner = AutoRunner(path, config)
    report = runner.run(outline_text)

    # 汇总结果
    result = {
        "total_chapters": report.total_chapters,
        "successful": report.successful_chapters,
        "failed": report.failed_chapters,
        "chapters": [
            {
                "id": r.chapter_id,
                "title": r.outline.title,
                "word_count": r.word_count,
                "score": r.evaluation.total_score,
                "retries": r.retry_count,
            }
            for r in report.chapters
        ],
        "total_words": sum(r.word_count for r in report.chapters),
        "avg_score": (
            sum(r.evaluation.total_score for r in report.chapters) / len(report.chapters)
            if report.chapters
            else 0
        ),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


async def _handle_commit(args: dict) -> str:
    """提取章节状态变更并固化。"""
    path = Path(args.get("path", ".")).resolve()
    chapter = args.get("chapter", "")
    model_name = args.get("model")

    chapter_path = path / "draft" / chapter
    if not chapter_path.exists():
        return f"错误: 章节文件不存在 ({chapter_path})"

    from opennovel.agents.auditor import Auditor
    from opennovel.core.state_manager import StateManager

    storage = YAMLStorage()
    manager = StateManager(path)
    config = LoomConfig.load(path)

    # 生成快照
    chapter_meta, body = storage.read_markdown_file(chapter_path)
    chapter_id = chapter_meta.get("id", chapter.replace(".md", ""))
    active_chars = storage.extract_active_characters(chapter_path)
    affected_files = [chapter_path]
    for char_id in active_chars:
        char_path = path / "characters" / f"{char_id}.md"
        if char_path.exists():
            affected_files.append(char_path)

    snapshot = manager.create_snapshot(chapter_id, affected_files=affected_files)

    # Auditor 提取
    llm_bus = LLMBus(
        model=model_name or config.model,
        api_base=config.api_base,
        api_key=config.api_key,
    )
    auditor = Auditor(llm_bus=llm_bus, state_manager=manager, project_root=path, yaml_storage=storage)

    try:
        result = auditor.extract_events_with_retry(chapter_id, body, active_chars)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Auditor 提取失败: {e}"}, ensure_ascii=False)

    if result.dirty:
        return json.dumps({"status": "dirty", "message": "脏提交：Auditor 提取失败"}, ensure_ascii=False)

    if not result.events:
        return json.dumps({"status": "no_events", "message": "未检测到状态变更事件"}, ensure_ascii=False)

    # 写入固化
    event_ids = auditor.apply_confirmed_events(result.events, chapter_id)
    manager.update_snapshot_after(snapshot.snapshot_id, affected_files, event_ids)

    return json.dumps({
        "status": "success",
        "events_committed": len(event_ids),
        "chapter_id": chapter_id,
        "event_ids": event_ids,
    }, ensure_ascii=False, indent=2)


async def _handle_stash(args: dict) -> str:
    """存入灵感到潜意识池。"""
    path = Path(args.get("path", ".")).resolve()
    content = args.get("content", "")
    source = args.get("source", "mcp")

    if not content:
        return "错误: 必须提供 content"

    try:
        from opennovel.storage.yaml_storage import YAMLStorage
        from datetime import datetime

        storage = YAMLStorage()
        sub_dir = path / "subconscious"
        sub_dir.mkdir(parents=True, exist_ok=True)

        # 生成唯一文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        line_path = sub_dir / f"stash_{timestamp}.md"
        frontmatter = {"source": source, "created_at": timestamp}
        storage.write_markdown_file(line_path, frontmatter, content)

        # 尝试更新向量索引
        try:
            from opennovel.storage.vector import VectorStore

            vector = VectorStore(path)
            if vector.ensure_index(path / "subconscious"):
                logger.info("潜意识向量索引已更新")
        except Exception:
            pass

        return json.dumps({
            "status": "success",
            "file": str(line_path),
            "length": len(content),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


async def _handle_diff(args: dict) -> str:
    """检查正文与 Shadow 一致性。"""
    path = Path(args.get("path", ".")).resolve()
    chapter = args.get("chapter", "")

    try:
        from opennovel.core.diff_checker import DiffChecker
        from opennovel.storage.yaml_storage import YAMLStorage

        checker = DiffChecker(path, YAMLStorage())
        if chapter:
            chapter_path = path / "draft" / chapter
            if not chapter_path.exists():
                return f"错误: 章节文件不存在 ({chapter_path})"
            mismatches = checker.check_chapter(chapter_path)
        else:
            mismatches = checker.check_all()

        if not mismatches:
            return json.dumps({"status": "ok", "mismatches": []}, ensure_ascii=False)

        results = []
        for m in mismatches:
            results.append({
                "severity": m.severity.value if hasattr(m.severity, "value") else str(m.severity),
                "category": m.category,
                "character_id": m.character_id,
                "message": m.message,
                "source": m.source,
            })
        return json.dumps({"status": "issues_found", "mismatches": results}, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


async def _handle_doctor(args: dict) -> str:
    """运行项目健康度诊断。"""
    path = Path(args.get("path", ".")).resolve()

    try:
        from opennovel.core.doctor import Doctor

        doc = Doctor(path)
        data = doc.generate_dashboard()
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


async def _handle_foreshadow(args: dict) -> str:
    """查看/管理伏笔追踪表。"""
    path = Path(args.get("path", ".")).resolve()
    action = args.get("action", "list")

    try:
        from opennovel.storage.foreshadowing import ForeshadowStore

        store = ForeshadowStore(path)
        state = store.load()

        if action == "list":
            return json.dumps({
                "total": len(state.items),
                "items": [
                    {
                        "id": i.id,
                        "description": i.description,
                        "status": i.status.value if hasattr(i.status, "value") else i.status,
                        "type": i.type.value if hasattr(i.type, "value") else i.type,
                        "target_chapter": i.target_chapter,
                    }
                    for i in state.items
                ],
            }, ensure_ascii=False, indent=2)

        elif action == "add":
            description = args.get("description", "")
            if not description:
                return json.dumps({"status": "error", "message": "add 操作需要 description 参数"}, ensure_ascii=False)
            from opennovel.schemas.foreshadowing import ForeshadowItem, ForeshadowStatus, ForeshadowType
            max_num = 0
            for item in state.items:
                try:
                    num = int(item.id.lstrip("F"))
                    if num > max_num:
                        max_num = num
                except (ValueError, AttributeError):
                    pass
            new_id = f"F{max_num + 1:03d}"
            new_item = ForeshadowItem(
                id=new_id,
                description=description,
                status=ForeshadowStatus.ACTIVE,
                type=ForeshadowType.PLOT,
            )
            state.items.append(new_item)
            store.save(state)
            return json.dumps({"status": "success", "id": new_id}, ensure_ascii=False)

        elif action == "resolve":
            item_id = args.get("id", "")
            if not item_id:
                return json.dumps({"status": "error", "message": "resolve 操作需要 id 参数"}, ensure_ascii=False)
            from opennovel.schemas.foreshadowing import ForeshadowStatus
            for item in state.items:
                if item.id == item_id:
                    item.status = ForeshadowStatus.RESOLVED
                    break
            store.save(state)
            return json.dumps({"status": "success", "id": item_id, "action": "resolved"}, ensure_ascii=False)

        else:
            return json.dumps({"status": "error", "message": f"未知 action: {action}"}, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


# ── 入口 ─────────────────────────────────────────────────────────────────────


async def _async_main() -> None:
    """异步主函数，启动 stdio MCP Server。"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def cli_main() -> None:
    """入口点，供 pyproject.toml 的 console_scripts 使用。"""
    asyncio.run(_async_main())


if __name__ == "__main__":
    cli_main()
