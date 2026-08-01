# 贡献指南

感谢你对 OpenNovel 项目感兴趣！本文档指导你如何参与贡献。

## 行为准则

请阅读并遵守我们的 [行为准则](CODE_OF_CONDUCT.md)。

## 安全漏洞

如果发现安全漏洞，**请不要**在 GitHub Issues 中公开报告。请参考 [SECURITY.md](SECURITY.md) 的流程。

## 开发环境搭建

```bash
# 克隆仓库
git clone https://github.com/Yaemikoreal/OpenNovel.git
cd OpenNovel

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate      # Windows

# 安装开发依赖
pip install -e ".[dev]"

# 验证安装
novel --help
```

## 代码规范

本项目严格执行代码质量要求：

### Python 风格

- **类型注解**: 所有公共函数必须有完整类型注解（`mypy` strict 模式）
- **格式化**: 使用 `ruff format`，行宽 100
- **Lint**: `ruff check` 检查，无警告
- **命名**: 遵循 PEP 8，类用 `PascalCase`，函数/变量用 `snake_case`

### 运行检查

```bash
# 格式化
ruff format loom/ tests/

# 静态检查
ruff check loom/ tests/

# 类型检查
mypy loom/

# 测试
pytest -v --tb=short
```

### 预提交检查

提交前请确保：
1. `ruff check` 无错误
2. `ruff format` 无差异
3. `mypy` 通过
4. `pytest` 全部通过
5. 新的功能有对应的测试覆盖

## 贡献流程

### 1. 创建 Issue

在开始编码前，先创建 Issue 描述你要解决的问题或功能：

- **Bug 修复**: 使用 Bug Report 模板
- **新功能**: 使用 Feature Request 模板
- **文档改进**: 直接在文档相关文件上修改即可

### 2. 分支策略

```
main          # 稳定发布分支
├── dev       # 开发主分支
├── feat/*    # 新功能分支
├── fix/*     # 修复分支
└── docs/*    # 文档分支
```

- `main` 分支受保护，不能直接推送
- 从 `main` 创建你的功能/修复分支
- 分支命名：`feat/<描述>` / `fix/<描述>` / `docs/<描述>`

### 3. 开发步骤

```bash
# 创建分支
git checkout -b feat/my-feature

# 开发、提交
git add .
git commit -m "[feat]：添加XXX功能"

# 保持与上游同步
git fetch origin
git rebase origin/main

# 推送到你的 fork
git push origin feat/my-feature
```

### 4. 提交信息格式

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 风格，使用中文描述：

```
[类型]：精炼概要

- 变更点：（做了什么改动）
- 优化点：（改进了什么）
- 解决问题：（修复了什么）
```

类型：`feat` `fix` `refactor` `test` `docs` `style` `chore`

### 5. 创建 Pull Request

- PR 标题简明描述变更
- PR 描述中关联相关 Issue（`Fixes #123`）
- 确保 CI 检查全部通过
- 至少需要一名维护者 review 通过

### 6. Review 流程

1. 提交 PR 后，CI 自动运行测试
2. 维护者进行 Code Review
3. 可能需要你根据反馈修改
4. 至少一名维护者 Approve 后方可合并

## 文档贡献

- 使用中文编写注释和文档
- 技术术语保留英文
- `docs/adr/` 目录存储架构决策记录
- 新功能需要更新 README.md 和相应的 CLAUDE.md

## 测试要求

- 新功能必须包含测试
- Bug 修复应添加对应的回归测试
- 测试使用 `pytest`，测试文件放在 `tests/` 目录
- 测试命名：`test_<模块>_<功能>.py`

## 架构说明

在贡献前，建议阅读：

1. [CLAUDE.md](CLAUDE.md) — 项目架构和约定
2. `设计文档/设计方案文档.md` — 完整技术方案
3. `docs/adr/` — 架构决策记录

### 核心原则

- **Human-first**: 作者只写 Markdown，不碰 YAML
- **Zero-Trust AI**: 所有 AI 输出须经人工审阅
- **操作可逆**: 破坏性操作前自动快照
- **ID 即锚点**: 全局 Canonical IDs，不用角色名关联

## 常见问题

**Q: 需要 GPU 才能运行吗？**
A: 不需要。本地嵌入（BGE-M3）为可选依赖，核心功能纯 CPU 可用。

**Q: 支持哪些 LLM？**
A: 通过 LiteLLM 总线支持几乎所有模型：OpenAI、Anthropic、DeepSeek、本地 Ollama 等。

**Q: 必须联网吗？**
A: 核心功能完全本地运行。LLM 调用可配置为本地模型或远程 API。

---

再次感谢你的贡献！ 🎉
