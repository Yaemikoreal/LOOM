# OpenNovel 用户操作手册

>  — 本地优先的长篇小说叙事操作系统

---

## 目录

1. [简介](#简介)
2. [安装](#安装)
3. [快速开始](#快速开始)
4. [核心概念](#核心概念)
5. [命令详解](#命令详解)
6. [写作工作流](#写作工作流)
7. [常见问题](#常见问题)

---

## 简介

OpenNovel 是一个面向长篇小说创作者的 AI 辅助写作工具。它通过 CLI 命令行操作，帮助你：

- **沉浸式续写**：AI 根据你的世界观设定和角色状态续写剧情
- **状态追踪**：自动记录角色伤势、情绪变化、物品获取等事件
- **一致性校验**：检测"写伤没写治"等逻辑漏洞
- **灵感管理**：随时存入灵感碎片，AI 续写时自动召回

### 设计哲学

OpenNovel 遵循四条核心原则：

1. **你写正文，AI 写元数据** — 你只管创作，AI 负责提取和管理状态
2. **AI 只能提议** — 所有状态变更都需你确认后才生效
3. **一切可回滚** — 每次操作前自动创建快照，随时可以撤销
4. **本地优先** — 所有数据存储在本地 Markdown + SQLite 文件中，可 Git 追踪

---

## 安装

### 前置条件

- Python 3.10 或更高版本
- pip（Python 包管理器）

### 安装步骤

```bash
# 1. 克隆或下载项目
git clone <repository-url>
cd OpenNovel

# 2. 创建虚拟环境
python -m venv .venv

# 3. 激活虚拟环境
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 4. 安装项目及依赖
pip install -e ".[dev]"
```

### 配置 LLM

OpenNovel 使用 LiteLLM 调用 LLM，支持 OpenAI、Anthropic、DeepSeek 等模型。你需要设置对应的 API Key：

```bash
# OpenAI（默认模型）
export OPENAI_API_KEY="sk-..."

# 或使用 DeepSeek
export DEEPSEEK_API_KEY="sk-..."

# 或使用 Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
```

Windows 用户请使用 `set` 命令或在系统环境变量中设置。

---

## 快速开始

### 第一步：初始化项目

```bash
novel init my_novel
```

这会创建如下目录结构：

```
my_novel/
├── canon/              # 世界观设定（不可变规则）
│   └── world_rules.md
├── characters/         # 角色档案
│   └── char_001.md
├── draft/              # 章节正文
│   └── ch_001.md
├── outlines/           # 大纲
├── subconscious/       # 灵感潜意识池
├── .snapshots/         # 快照（自动生成）
├── .index/             # 向量索引（自动生成）
├── novel.yaml           # 项目配置
└── .novel.db            # 事件账本（自动生成）
```

### 第二步：编写世界观设定

编辑 `canon/world_rules.md`，写入不可违反的世界观规则：

```markdown
# 世界观设定

魔法需要消耗寿命，施法者每使用一次魔法，寿命减少一年。

北境王国的国王已失踪三年，王位由摄政王代管。
```

### 第三步：创建角色

编辑 `characters/char_001.md`，或创建新的角色文件：

```markdown
---
id: char_001
name: 艾拉
aliases: [北境之女]
physical:
  injuries: []
  buffs: []
  debuffs: []
emotional:
  grief: 0.3
  anger: 0.0
  fear: 0.2
  joy: 0.1
  determination: 0.8
---

# 角色背景

艾拉是北境王国的公主，三年前父亲失踪后，
她独自支撑着摇摇欲坠的王室。
```

### 第四步：开始写作

```bash
novel write ch_001.md
```

进入交互式写作模式：

- **输入空行** → 触发 AI 续写
- **输入文字** → 你自己的创作（AI 不会覆盖）
- **输入 `:q`** → 退出写作模式

### 第五步：提交状态

写完一段后，提取并固化角色状态变更：

```bash
novel commit ch_001.md
```

系统会自动：
1. 创建快照
2. AI 分析正文，提取事件（伤势、情绪变化等）
3. 展示变更预览（Diff）
4. 等待你确认（输入 `y` 确认 / `n` 取消）

### 第六步：检查一致性

```bash
# 检查正文与状态的一致性
novel diff

# 诊断项目健康度
novel doctor
```

---

## 核心概念

### 三层架构

```
你写的正文（Markdown）    ← 你编辑这一层
    ↓
YAML 状态 + SQLite 事件   ← AI 自动维护
    ↓
向量索引（语义检索）       ← 自动构建
```

- **Human Layer**：你写的 Markdown 正文，永远可被 Obsidian/VSCode 打开
- **Machine Shadow**：YAML Frontmatter 中的角色状态 + SQLite 事件账本
- **Semantic Layer**：向量索引，用于语义检索历史设定和灵感

### 权威分级

当设定之间发生冲突时，系统按以下优先级处理：

1. **CANON**（最高）— `canon/` 目录中的世界观规则，不可违反
2. **STATE MEMORY** — 角色当前状态（伤势、情绪等）
3. **SUBCONSCIOUS**（最低）— 灵感碎片，仅作文风参考

### Token 预算

AI 续写时，系统根据模型的上下文窗口自动选择策略：

| 策略 | 适用模型 | 预算 | 特点 |
|------|---------|------|------|
| FRUGAL | < 32K | 8K | 精打细算，仅注入 POV 角色 |
| STANDARD | 32K-128K | 48K | 注入全部活跃角色 |
| PANORAMIC | > 128K | 128K | 全量设定 + 历史章节倒序注入 |

---

## 命令详解

### `novel init [路径]`

初始化一个新的小说项目。

```bash
novel init .              # 在当前目录初始化
novel init my_novel       # 在 my_novel/ 目录初始化
```

创建标准目录结构和模板文件。重复运行不会覆盖已有文件。

### `novel write <章节> [路径]`

启动交互式写作循环。

```bash
novel write ch_001.md                  # 使用默认模型 (gpt-4)
novel write ch_001.md -m deepseek-chat # 指定模型
```

交互操作：
- **空行** → AI 续写
- **任意文字** → 你自己的创作
- **`:q`** → 退出
- **Ctrl+C** → 退出

### `novel commit <章节> [路径]`

提取状态变更并固化。

```bash
novel commit ch_001.md
novel commit ch_001.md -m deepseek-chat
```

5 步审阅流程：
1. 生成快照
2. AI 提取事件（最多 3 次重试）
3. 展示变更 Diff
4. 人工确认（`y` 确认 / `n` 取消 / `edit` 手动编辑）
5. 写入固化

如果 AI 连续 3 次提取失败，进入急救模式：
- **`e`** — 手动修补 JSON
- **`s`** — 跳过（打 dirty_flag 标记）
- **`a`** — 终止

### `novel stash`

存入灵感到潜意识池。

```bash
novel stash "深渊不收我，因为我就是深渊。"
novel stash "主角在第三章获得一把生锈的钥匙" --tag 道具 --tag 伏笔
```

灵感会被写入 `subconscious/lines.md` 并自动索引。AI 续写时会语义检索相关灵感。

### `novel rollback <快照ID>`

回滚到指定快照。

```bash
novel rollback snap_ch_001_1698765432
```

快照 ID 可通过以下方式查看：
```bash
ls .snapshots/
```

### `novel diff [章节] [路径]`

检查正文与 Shadow 状态的一致性。

```bash
novel diff                # 扫描所有章节
novel diff ch_001.md      # 只检查指定章节
```

检测项目：
- 伤势一致性（写伤没写治 / 治了没写伤）
- 脏标记（`dirty_flag`）
- 角色引用（引用了不存在的角色）

### `novel doctor [路径]`

诊断项目健康度。

```bash
novel doctor
```

检查项目：
- 存在但未被任何章节引用的角色
- 章节中引用了不存在的角色
- 文件名与 ID 不一致
- 脏标记
- 事件账本完整性
- 快照统计

### `novel reindex [路径]`

重建搜索索引（FTS5 全文索引 + 向量索引）。

```bash
novel reindex                        # 重建当前项目索引
novel reindex novels/my_novel        # 重建指定项目索引
```

适用于：
- FTS5 或向量索引损坏时修复
- 手动修改了 canon/ 或 characters/ 文件后同步索引
- 首次使用搜索功能前初始化索引

### `novel auto <路径>`

四 Agent 全自动创作流水线（推荐的长篇创作方式）。

```bash
novel auto novels/my_novel
```

系统自动执行 Writer→Critic→Manager→Director 循环，逐章生成完整小说。详见[全自动写作](#全自动写作)章节。

---

## 写作工作流

### 推荐的日常流程

```
1. 编辑设定/角色（canon/、characters/）
        ↓
2. 写作（novel write）
        ↓
3. 存入灵感（novel stash）
        ↓
4. 提交状态（novel commit）
        ↓
5. 检查一致性（novel diff、novel doctor）
        ↓
6. 重复 2-5
```

### 长篇小说的最佳实践

1. **世界观规则放 canon/**：不可变的设定（魔法系统、地理、历史）放这里
2. **角色档案保持更新**：每次 commit 后检查角色状态是否正确
3. **善用 stash**：随时记录灵感，AI 续写时会自动召回
4. **定期 diff**：每写完一章就跑一次 `novel diff`，及早发现问题
5. **Git 版本控制**：整个项目目录可以用 Git 追踪

### 配置文件

`novel.yaml` 存储项目配置：

```yaml
version: "1.0.1"
model: "gpt-4"           # 默认 LLM 模型
token_budget: 8000        # Token 总预算
output_reserve: 2000      # 输出预留
```

你可以修改 `model` 来切换默认模型，支持 LiteLLM 的所有模型标识。

---

## 全自动写作（Gen2）

`novel auto` 是 OpenNovel Gen2 的核心特性。当你准备好了世界观设定、角色档案和大纲后，一条命令即可自动生成完整小说。

### 适用场景

- **长篇小说创作**：从大纲出发，逐章生成完整故事
- **快速原型**：测试不同故事方向的可行性
- **辅助创作**：先生成草稿，再人工精修

### 准备工作

1. 完成 `canon/world_rules.md`（世界观设定）
2. 创建角色档案（`characters/`）
3. 编写大纲（`outlines/story.md`，## 分隔各章）
4. 配置 `novel.yaml` 中的创作参数

```bash
# 启动全自动创作
novel auto novels/my_novel
```

### 流水线

```
每章循环:
  1. Writer.think()        → 结构化大纲（场景拆分）
  2. Writer.write()        → 创作正文
  3. Critic.evaluate()     → 五维评分（<80 分退回修订）
  4. Writer.revise()       → 锚定反馈修订
  5. Manager.update()      → 角色状态 + 事件记录
  6. Director.analyze()    → 全局策略注入下一章（可选）
```

### 创作配置

```yaml
# novel.yaml
creative_direction: "黑暗奇幻，克苏鲁元素"  # 创作方向
target_chapters: 10                   # 目标章节数
words_per_chapter: 3000               # 每章目标字数
director_enabled: true                # 启用 Director 分析
```

---

## 搜索系统

OpenNovel 内置三通道混合搜索系统，用于 Agent 创作过程中的知识召回。

### 搜索通道

| 通道 | 功能 | 存储 |
|:---|:---|:---|
| FTS5 全文索引 | 精确关键词匹配 | `.novel.fts5.db` |
| 向量语义检索 | 语义相似度搜索 | `.index/` |
| Cross-Encoder 重排序 | 精排融合结果 | 运行时 |

三通道结果通过 RRF（Reciprocal Rank Fusion）算法融合排序。

### 管理索引

```bash
# 重建全部索引（索引损坏或文件修改后执行）
novel reindex

# 重建指定项目索引
novel reindex novels/my_novel
```

---

## 常见问题

### Q: AI 续写质量不好怎么办？

1. **完善世界观设定**：`canon/` 中的规则越详细，AI 越不容易犯错
2. **写好角色档案**：`characters/` 中的角色背景、性格、目标越清晰越好
3. **用 stash 存灵感**：续写前存入你的想法，AI 会参考
4. **切换模型**：不同模型擅长不同风格，试试 `deepseek-chat` 或 `gpt-4`

### Q: commit 时 AI 提取的事件不对怎么办？

1. 在审阅步骤输入 `n` 取消
2. 手动编辑角色文件的 YAML Frontmatter
3. 重新 `novel commit`

### Q: 如何回滚错误的 commit？

```bash
# 查看快照列表
ls .snapshots/

# 回滚到指定快照
novel rollback snap_ch_001_1698765432
```

### Q: 如何添加新角色？

1. 在 `characters/` 目录创建新文件，如 `char_002.md`
2. 确保 YAML Frontmatter 中的 `id` 字段使用 `char_` 前缀
3. 在章节文件的 `active_characters` 中添加新角色 ID

### Q: 如何切换 LLM 模型？

临时切换：
```bash
novel write ch_001.md -m deepseek-chat
```

永久修改：编辑 `novel.yaml` 中的 `model` 字段。

### Q: 向量索引需要额外安装吗？

默认情况下使用 LiteLLM 内置的 embedding。如需本地 embedding（推荐，零成本、防泄漏）：

```bash
pip install sentence-transformers
```

### Q: 数据存储在哪里？

所有数据都在项目目录中：

| 文件 | 内容 |
|------|------|
| `*.md`（canon/characters/draft/） | 你写的正文和设定 |
| `.novel.db` | SQLite 事件账本 |
| `.snapshots/*.json` | 快照文件 |
| `.index/` | 向量索引 |
| `novel.yaml` | 项目配置 |

可以用 Git 追踪，也可以用 Obsidian/VSCode 直接编辑 Markdown 文件。

---

## 附录：完整示例

```bash
# 1. 初始化
novel init my_novel && cd my_novel

# 2. 编辑世界观（用你喜欢的编辑器）
# 编辑 canon/world_rules.md

# 3. 编辑角色
# 编辑 characters/char_001.md

# 4. 写作
novel write ch_001.md
# 输入空行触发 AI 续写，写完输入 :q 退出

# 5. 存入灵感
novel stash "北境的冬天比往年来得更早" --tag 环境

# 6. 提交状态
novel commit ch_001.md
# 审阅 AI 提取的事件，输入 y 确认

# 7. 检查一致性
novel diff
novel doctor

# 8. 继续下一章
cp draft/ch_001.md draft/ch_002.md
# 修改 ch_002.md 的 id 和内容
novel write ch_002.md
```
