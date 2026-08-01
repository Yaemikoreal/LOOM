# 0001 — 文件级增量快照替代全局全量 Dump

快照只记录参与 `novel commit` 的文件各自的 `fm_before` 和 `fm_after`，拒绝全局遍历 `characters/` 目录的全量 Dump，也拒绝脆弱的 field_path 级 JSON Patch。

## 背景

初始实现中 `StateManager.create_snapshot()` 会遍历 `characters/` 下所有 `*.md` 的 Frontmatter。当两个 commit 分别修改不同角色，或人类在 Obsidian 中手动编辑了未参与 commit 的角色文件时，全量回滚会静默擦除这些外部修改，违反 Human-first 原则。

## 决策

1. **粒度**：文件级增量。快照只记录该次 commit 实际涉及的文件（章节文件 + 被 Auditor 提取到的角色文件），不扫描无关文件。
2. **内容**：每个文件记录 `fm_before`（commit 前的完整 Frontmatter）和 `fm_after`（commit 后的完整 Frontmatter），不记录字段级 Diff。
3. **回滚安全校验**：写入 `fm_before` 前，比对文件当前 Frontmatter 与快照中 `fm_after` 是否一致。若不一致说明人类/外部程序在 commit 后修改过该文件，触发冲突处理而非静默覆盖。

## 考虑过的替代方案

- **全局全量 Dump**：简单粗暴，但会摧毁 Git/Obsidian 多端的并发局部修改。拒绝。
- **field_path 级 JSON Patch**：理论上精确，但 Schema 漂移（如数组变对象）会导致历史补丁索引错位。拒绝。

## 影响

- 快照体积显著减小（不存无关文件）
- `StateManager` 需追踪本次 commit 具体涉及哪些文件
- 回滚操作从"全量覆盖"变为"逐文件校验后选择性覆写"
