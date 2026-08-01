# OpenNovel MCP 集成文档

> MCP (Model Context Protocol) 服务，让 LLM 客户端（Claude Desktop、Cursor、VS Code 等）直接操控 OpenNovel 创作工作流。

## 快速开始

### 启动服务

```bash
novel-mcp
```

服务以 stdio 模式启动，等待 MCP 客户端连接。

### 在 Claude Desktop 中配置

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "novel-mcp": {
      "command": "novel-mcp",
      "args": []
    }
  }
}
```

### 在 Cursor / VS Code 中配置

项目根目录 `.mcp.json`（已预设）：

```json
{
  "mcpServers": {
    "novel-mcp": {
      "command": "novel-mcp",
      "args": []
    }
  }
}
```

---

## Tools 参考

### 1. `init_project` — 初始化项目

**用途**：创建新的 OpenNovel 小说项目。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 否 | 项目路径，默认当前目录 |

**返回**：项目初始化状态文本。

**AI 使用示例**：
> "创建一个名为'星际迷航'的新小说项目"
> → 调用 `init_project`，然后引导用户配置 novel.yaml

---

### 2. `get_status` — 项目状态

**用途**：读取项目的完整状态：角色、章节、配置、健康检查。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 否 | 项目路径，默认当前目录 |

**返回**：结构化的项目状态文本（配置 + 角色列表 + 章节列表 + 大纲信息）。

**AI 使用示例**：
> "查看当前项目状态"
> → 调用 `get_status`，展示角色和章节给用户

---

### 3. `write_chapter` — 创作单章

**用途**：用 Writer Agent 创作单个章节（思考→创作→评分）。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 是 | 项目路径 |
| `chapter_id` | string | 是 | 章节 ID，如 `ch_001` |
| `chapter_hint` | string | 否 | 本章写作提示 |

**返回**：JSON，包含章节 ID、标题、字数、评分、维度分数。

```json
{
  "chapter_id": "ch_001",
  "title": "神秘来客",
  "word_count": 2450,
  "score": 85,
  "is_pass": true,
  "dimensions": {
    "文笔质量": 18,
    "情节逻辑": 17,
    "角色一致性": 16,
    "节奏把控": 17,
    "情感表达": 17
  }
}
```

**AI 使用示例**：
> "基于大纲第一章的提示，创作 ch_001"
> → 调用 `write_chapter`，根据评分决定是否要求修订

---

### 4. `auto_create` — 全自动创作

**用途**：运行四 Agent 全自动创作循环，按大纲完成全部章节。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 是 | 项目路径 |
| `chapters` | integer | 否 | 覆盖章节数（可选） |

**返回**：JSON，包含总章节数、成功/失败数、每章详情。

```json
{
  "total_chapters": 10,
  "successful": 10,
  "failed": 0,
  "total_words": 28500,
  "avg_score": 84.5,
  "chapters": [
    { "id": "ch_001", "score": 85, "word_count": 2450, "retries": 0 },
    { "id": "ch_002", "score": 82, "word_count": 3100, "retries": 1 }
  ]
}
```

**AI 使用示例**：
> "项目大纲已就绪，全自动生成全部 10 章"
> → 调用 `auto_create`，监控进度，完成后汇报

---

### 5. `commit` — 提取并固化状态

**用途**：运行 Auditor 提取章节中的角色/事件变更，固化到 EventStore。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 是 | 项目路径 |
| `chapter` | string | 是 | 章节文件名，如 `ch_001` |
| `model` | string | 否 | 用于 Auditor 的 LLM 模型 |

**返回**：JSON。

```json
{
  "status": "success",
  "events_committed": 3,
  "chapter_id": "ch_001",
  "event_ids": ["evt_001", "evt_002", "evt_003"]
}
```

**AI 使用示例**：
> "ch_003 写完了，提交状态变更"
> → 调用 `commit`，返回确认

---

### 6. `stash` — 存入灵感

**用途**：将灵感文本存入潜意识池，更新向量索引供后续检索。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 是 | 项目路径 |
| `content` | string | 是 | 灵感文本内容 |
| `source` | string | 否 | 来源标注（如 `user`） |

**返回**：JSON。

```json
{
  "status": "success",
  "file": "subconscious/stash_20260626_120000.json",
  "length": 156
}
```

**AI 使用示例**：
> "记录一个灵感：主角的怀表能在月圆之夜倒转时间"
> → 调用 `stash`，确认已存入

---

### 7. `diff` — 一致性校验

**用途**：检查章节正文与 Frontmatter 元数据之间的一致性。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 是 | 项目路径 |
| `chapter` | string | 否 | 章节文件名（可选，默认全部） |

**返回**：JSON。

```json
{
  "status": "issues_found",
  "mismatches": [
    {
      "severity": "WARNING",
      "category": "character_ref",
      "message": "正文中出现 char_003 但不在活跃角色列表中",
      "character_id": "char_003"
    }
  ]
}
```

**AI 使用示例**：
> "检查项目一致性"
> → 调用 `diff`，如果有问题就报告给用户

---

### 8. `doctor` — 健康诊断

**用途**：运行项目健康度诊断，返回完整的健康面板数据。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 是 | 项目路径 |

**返回**：JSON，包含 config / progress / characters / events / critic / diagnosis 六个区块。

```json
{
  "config": { "model": "deepseek/deepseek-v4-flash", "status": "valid" },
  "progress": { "chapters": 10, "latest": "ch_010" },
  "characters": { "total": 3, "ids": ["char_001", "char_002", "char_003"] },
  "events": { "total": 45 },
  "critic": { "total": 10, "avg_score": 84.5 },
  "diagnosis": { "errors": 0, "warnings": 2 }
}
```

**AI 使用示例**：
> "在开始创作之前，先检查项目健康状态"
> → 调用 `doctor`，如果配置有问题先引导修复

---

### 9. `foreshadow` — 伏笔管理

**用途**：查看、添加、回收伏笔线索。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `path` | string | 是 | 项目路径 |
| `action` | string | 是 | 操作：`list` / `add` / `resolve` |
| `description` | string | 否 | 伏笔描述（add 时必填） |
| `id` | string | 否 | 伏笔 ID（resolve 时必填，如 `F001`） |

**返回** JSON。

list 示例：
```json
{
  "total": 2,
  "items": [
    { "id": "F001", "description": "怀表能倒转时间", "status": "ACTIVE" },
    { "id": "F002", "description": "黑衣人真实身份", "status": "RESOLVED" }
  ]
}
```

add 返回：`{ "status": "success", "id": "F003" }`
resolve 返回：`{ "status": "success", "id": "F001", "action": "resolved" }`

**AI 使用示例**：
> "添加一个伏笔：船长的航海日志缺了三页"
> → 调用 `foreshadow` with action=add
> "列出所有未回收的伏笔"
> → 调用 `foreshadow` with action=list

---

## AI 协作工作流最佳实践

### 创作前检查

```python
# AI 在开始创作前，先检查项目状态
doctor_result = call_tool("doctor", {"path": project})
if doctor_result["config"]["status"] != "valid":
    # 引导用户修复配置
    return "请先配置 API Key"

# 检查是否有未处理的问题
diagnosis = doctor_result["diagnosis"]
if diagnosis["warnings"] > 5:
    return f"项目有 {diagnosis['warnings']} 个告警，建议先处理"
```

### 创作循环

```python
# 1. 创作章节
result = call_tool("write_chapter", {"path": project, "chapter_id": "ch_005"})
score = result["score"]

# 2. 评分低于 80 时建议修订
if score < 80:
    return f"ch_005 评分 {score}，需要修订后再提交"

# 3. 提交状态
commit_result = call_tool("commit", {"path": project, "chapter": "ch_005"})
event_count = commit_result["events_committed"]
```

### 灵感管理

```python
# 在对话中捕捉用户的想法，自动存入潜意识
if "我突然想到" in user_input:
    idea = extract_idea(user_input)
    call_tool("stash", {"path": project, "content": idea, "source": "user"})
```

### 质量监控

```python
# 每 5 章运行一次健康检查
if chapter_number % 5 == 0:
    report = call_tool("doctor", {"path": project})
    if report["critic"]["avg_score"] < 80:
        return f"近 5 章平均评分 {report['critic']['avg_score']}，质量在下降"
```

---

## 错误处理

所有 tool 在失败时返回结构化的错误信息：

```json
{
  "status": "error",
  "message": "错误描述"
}
```

| 常见错误 | 原因 | AI 处理 |
|:---|:---|:---|
| `项目已存在` | 目标路径已有 novel.yaml | 确认用户意图，使用现有项目 |
| `章节文件不存在` | draft/ 中无对应文件 | 先用 `write_chapter` 创作 |
| `大纲文件不存在` | outlines/story.md 缺失 | 引导用户先编写大纲 |
| `Auditor 提取失败` | LLM 未能解析事件 | 建议用户手动编辑或重试 |
