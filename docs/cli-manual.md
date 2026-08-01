# OpenNovel CLI 操作手册

> 版本 2.0.0 — CLI 驱动，Markdown 创作，AI 辅助的长篇小说叙事操作系统。

## 快速入门

```bash
# 安装
pip install -e .

# 创建项目
novel init my_story

# 开始创作
cd novels/my_story
novel write ch_001

# 提交状态
novel commit ch_001

# 查看项目状态
novel doctor
```

---

## 命令总览

| 命令 | 功能 | 适用场景 |
|:---|:---|:---|
| `novel init` | 初始化小说项目 | 开始一个新项目 |
| `novel write` | 交互式写作循环 | 手动逐章创作 |
| `novel auto` | 四 Agent 全自动创作 | AI 全自动生成 |
| `novel commit` | 提取状态并固化 | 保存角色/事件变更 |
| `novel stash` | 存入灵感碎片 | 记录创作灵感 |
| `novel diff` | 一致性校验 | 检查正文与元数据 |
| `novel doctor` | 项目健康诊断 | 查看项目全景状态 |
| `novel list` | 列出所有项目 | 工作区管理 |
| `novel config` | 全局配置管理 | 设置默认模型/workpace |
| `novel rollback` | 回滚错误提交 | 撤销误操作 |
| `novel foreshadow` | 伏笔管理 | 追踪情节线索 |
| `novel-mcp` | 启动 MCP 服务 | 集成 IDE/AI 客户端 |

---

## 一、项目初始化

### `novel init` — 创建小说项目

```bash
novel init                    # 交互式创建（推荐新手）
novel init my_story           # 在 workspace 下创建
novel init .                  # 在当前目录创建
```

**交互式模式**（不传项目名时）：
1. 输入项目名称
2. 选择模板（standard / minimal）
3. 检测 API Key（DeepSeek / OpenAI）
4. 选择模式（quick / expert）
   - **quick**: 默认配置，立即开始
   - **expert**: 自定义模型名称

**创建的项目结构**：

```
my_story/
├── canon/                # 世界观设定（最高权威）
│   └── world_rules.md    # 不可违反的世界规则
├── characters/           # 角色档案
│   └── char_001.md       # 角色模板
├── draft/                # 章节正文
│   └── ch_001.md         # 第一章模板
├── outlines/             # 大纲文件
├── subconscious/         # 灵感碎片池
├── foreshadowing/        # 伏笔追踪
├── summaries/            # 章节摘要
├── timeline/             # 事件时间线
├── .snapshots/           # 增量快照（用于回滚）
├── .novel.db             # 事件账本
├── .novel.metrics.db     # 运行指标
└── novel.yaml            # 项目配置
```

### `novel list` — 列出工作区项目

```bash
novel list
```

输出示例：
```
工作区: E:\Pythonproject\OpenNovel\novels
├── demo_novel      (3 章, 3 角色, 5 事件)
├── my_story        (1 章, 2 角色, 0 事件)
└── time_paradox    (0 章, 1 角色, 0 事件)
```

---

## 二、创作命令

### `novel write` — 交互式写作

```bash
novel write ch_001        # 创作第一章
novel write ch_002        # 续写第二章
```

**流程**：
1. 加载上一章结尾和角色状态
2. Actor Agent 生成续写（流式输出）
3. 用户审阅正文
4. 提取角色状态变更（需要 LLM）

> 适合**手动控制的逐章创作**。建议配合 `novel commit` 一起使用。

### `novel auto` — 全自动创作

```bash
novel auto                              # 按大纲全部章节
novel auto --chapters 5                 # 只创作前 5 章
novel auto --no-director                # 跳过 Director 分析
```

**四 Agent 流水线**（每章循环）：

```
Writer.think()     → 结构化大纲
Writer.write()     → 创作正文
Critic.evaluate()  → 五维评分
                     ↓ 不合格（<80分）
Writer.revise()    → 修订（最多 3 次重试）
Manager.update()   → 提取角色状态 + 事件记录
Director.analyze() → 全局策略指导（可选，高潮章节强制）
StateManager       → 快照 + 一致性校验
```

> 适合 **批量创作**。需先准备 `outlines/story.md` 大纲文件。

### `novel stash` — 存入灵感

```bash
novel stash "主角在雨中遇见神秘人，他手中的怀表...\n似乎与百年前的诅咒有关"
novel stash --file idea.txt             # 从文件导入
```

灵感存入 `subconscious/` 目录，自动更新向量索引，后续创作时可被检索引用。

---

## 三、状态管理

### `novel commit` — 提取并固化状态

```bash
novel commit ch_001                     # 提交第一章
novel commit ch_001 --model gpt-4       # 指定提取模型
```

**5 步流程**：

```
Step 1: 生成快照（文件级增量备份）
Step 2: Auditor 提取事件（最多 3 次自省纠偏）
Step 3: Diff 展示（变更预览）
Step 4: 人工审阅（逐事件确认 ✅/❌/详情）
Step 5: 写入固化（EventStore + 摘要）
```

**逐事件确认**（Step 4 示例）：

```
事件 1/3: INJURY - char_001 左臂骨折 (压强 0.8)
  角色: char_001  |  因果压强: 0.8
  应用此事件? [y/n/detail] (y): y        ← 确认
  ✓ 已确认

事件 2/3: EMOTION_SHIFT - char_001 恐惧 (压强 0.9)
  应用此事件? [y/n/detail] (y): detail   ← 查看详情
  前置事件: evt_001_injury
  应用此事件? [y/n/detail] (y): y

事件 3/3: ITEM_GAIN - char_001 获得古老钥匙 (压强 0.6)
  应用此事件? [y/n/detail] (y): n        ← 跳过
  — 已跳过

确认: 2/3 个事件将写入
```

### `novel rollback` — 回滚

```bash
novel rollback ch_001                   # 回滚到提交前状态
novel rollback ch_001 --force           # 强制覆盖手动修改
```

回滚利用 `.snapshots/` 中的增量快照，仅恢复受影响的文件，其他文件不受影响。

---

## 四、诊断与分析

### `novel doctor` — 项目健康面板

```bash
novel doctor                            # 完整健康面板
novel doctor --no-dashboard             # 仅诊断列表
novel doctor --calibration              # Critic 评分校准分析
```

**健康面板**（默认模式）输出示例：

```
OpenNovel doctor - demo_novel

┌─ 📋 配置 ──────────────────────────────┐
│ 模型: deepseek/deepseek-v4-flash        │
│ Token 预算: 8000  |  API Key: ✅ 已配置  │
│ 方向: 黑暗奇幻，克苏鲁元素               │
└─────────────────────────────────────────┘

┌─ 📝 章节进度 ───────────────────────────┐
│ 已完成: 3 章  |  最新章节: ch_003       │
└──────────────────────────────────────────┘

┌─ 👤 角色 & 📋 事件 ─────────────────────┐
│ 活跃角色: 2  |  已记录事件: 15          │
│ 事件类型: INJURY, HEAL, EMOTION_SHIFT   │
└──────────────────────────────────────────┘

┌─ 📊 评分趋势 ───────────────────────────┐
│ 总评分次数: 3 | 平均分: 86.0 | 最新: 85 │
└──────────────────────────────────────────┘

┌─ 🔍 诊断摘要 ───────────────────────────┐
│ ✅ ERROR: 0  |  WARNING: 1  |  INFO: 2  │
└──────────────────────────────────────────┘
```

**校准分析**（`--calibration`）：

```
Critic 评分校准报告
共 12 次评分
总分均值: 84.5

各维度统计:
  文笔质量: 均值 16.2/20  标准差 1.1  范围 14-18
  情节逻辑: 均值 14.8/20  标准差 2.3  范围 12-18  ⚠
  角色一致: 均值 17.1/20  标准差 0.9  范围 15-18
  节奏把控: 均值 13.5/20  标准差 1.8  范围 11-16
  情感表达: 均值 15.9/20  标准差 1.5  范围 13-18

告警:
  ⚠ 情节逻辑 标准差 2.3 偏高（>2.0），可能存在评分漂移
  ⚠ 评分标准差 2.1 偏低（<3.0），所有章节评分过于集中
```

### `novel diff` — 正文与元数据一致性

```bash
novel diff                              # 检查所有章节
novel diff ch_001                       # 检查特定章节
```

检测内容：角色引用一致性、POV 设置匹配、Active Characters 与实际出场角色。

### `novel foreshadow` — 伏笔管理

```bash
novel foreshadow                        # 列出所有伏笔
novel foreshadow --add "钥匙是打开封印的关键"  # 添加伏笔
novel foreshadow --resolve F003         # 标记伏笔已回收
```

---

## 五、全局配置

### `novel config` — 查看/设置全局配置

```bash
novel config                            # 查看当前配置
novel config --set-model deepseek/deepseek-v4-flash
novel config --set-workspace E:/novels
```

全局配置文件 `.opennovel.yaml`（搜索优先级：当前目录 → 逐级向上 → 用户目录）：

```yaml
default_model: "deepseek/deepseek-v4-flash"
workspace_dir: "novels"
default_api_base: ""
```

### 项目配置 `novel.yaml`

```yaml
version: "1.0.1"
model: "deepseek/deepseek-v4-flash"
token_budget: 8000
output_reserve: 2000
creative_direction: "黑暗奇幻，克苏鲁元素"
target_chapters: 10
words_per_chapter: 3000
outline: "outlines/story.md"
director_enabled: true

# 每 Agent 独立模型配置
agents:
  writer:
    think_model: "deepseek/deepseek-chat"      # 思考阶段（廉价）
    write_model: "deepseek/deepseek-chat"       # 创作阶段
    write_model_climax: "deepseek/deepseek-r1" # 高潮章节（更强）
    revise_model: "deepseek/deepseek-chat"      # 修订阶段
```

### 三层模型路由

```
优先级高 → 低:
  novel.yaml agents.writer.model     # Agent 级（最优先）
  → novel.yaml model                 # 项目级
  → .opennovel.yaml default_model    # 全局级
  → deepseek/deepseek-v4-flash       # 硬编码默认值
```

---

## 六、MCP 服务

### `novel-mcp` — 启动 MCP 协议服务

```bash
novel-mcp                              # stdio 模式启动
```

提供 9 个 tools，供 Claude Desktop / Cursor 等 MCP 客户端集成：

| Tool | 功能 |
|:---|:---|
| `init_project` | 初始化项目 |
| `get_status` | 项目状态 |
| `write_chapter` | Writer 创作单章 |
| `auto_create` | 全自动创作 |
| `commit` | 提取固化状态 |
| `stash` | 存入潜意识 |
| `diff` | 一致性校验 |
| `doctor` | 健康诊断 |
| `foreshadow` | 伏笔管理 |

---

## 七、简易故障排除

| 现象 | 原因 | 解决 |
|:---|:---|:---|
| `module not found` | 依赖未安装 | `pip install -e .` |
| `API key not found` | 环境变量未设置 | 设置 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` |
| `novel.yaml 配置错误` | YAML 格式错误 | 运行 `novel init` 重新生成，或检查字段类型 |
| `ContextStrategy.STANDARD` 覆盖 | 非预期策略 | `novel doctor` 检查模型窗口，默认模型使用 128K+ |

---

## 八、工作流示例

### 新手快速开始

```bash
# 1. 设置 API Key
set DEEPSEEK_API_KEY=sk-xxx

# 2. 创建项目
novel init my_first_novel

# 3. 创作第一章
cd novels/my_first_novel
novel write ch_001

# 4. 提交状态变更
novel commit ch_001

# 5. 查看项目健康
novel doctor

# 6. 用全自动创作继续
novel auto
```

### 专业创作者流程

```bash
# 1. 编写大纲 outlines/story.md
# 2. 交互式创作关键章节
novel write ch_001

# 3. 全自动批量生成日常章节
novel auto --chapters 5

# 4. 定期检查一致性
novel doctor

# 5. 管理伏笔线索
novel foreshadow --add "怀表的指针倒转"
novel foreshadow

# 6. 发现问题时回滚
novel rollback ch_005
```
