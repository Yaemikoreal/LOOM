# Code Review 报告 — ADR 0007 RAG 优化

审查范围: 6 个新文件 + 8 个修改文件  
审查模式: 10 角度并行查找 → 1-vote 验证 → 1 轮扫漏  
审查日期: 2026-06-29

---

## P0 — 已修复（审查过程中发现并修复）

| # | 文件 | 行 | 问题 | 修复 |
|---|------|-----|------|------|
| 1 | `storage/fts5.py` | 259 | **孤儿 except 导致 SyntaxError** — `if not fts5_query: return []` 的缩进将 SQL 块吞入 if 分支，except 无匹配 try，模块无法导入 | 还原缩进，添加正确 try |
| 2 | `storage/fts5.py` | 204 | **FTS5 delete 无效 SQL** — `INSERT ... VALUES('delete', rowid, ...)` 中 `rowid` 无表引用可解析 | 改为子查询 `SELECT rowid FROM chunks_fts WHERE chunk_id=?` |
| 3 | `core/chunker.py` | 231 | **Overlap 机制无效** — `remaining_parts = parts[parts.index(part)+1:]` 创建切片副本，修改被丢弃 | 改用 `enumerate` 索引直接修改原始 `parts` |
| 4 | `core/search_pipeline.py` | 533 | **_find_chunk_index 静默返回 0** — 未找到 chunk_id 时返回索引 0（首个条目），数据张冠李戴 | 改为 `raise ValueError` |
| 5 | `cli/reindex.py` | 161 | **`except Exception: pass` 静默吞错误** — 统计摘要失败时无任何日志 | 改为 `logger.debug(...)` |

## P1 — 高

| # | 文件 | 行 | 问题 | 状态 |
|---|------|-----|------|------|
| 6 | `core/hybrid_retriever.py` | 111 | **search() fallback 丢弃 subconscious 和 causal_chain** — 当 `_pipeline is None` 时，仅转换 `canon_content`，其他通道数据静默丢失 | 未修复 |
| 7 | `core/reranker.py` | 61 | **模块级单例非线程安全** — `_MODEL_INSTANCE` 惰性初始化无为 `threading.Lock`，并发环境下存在 race condition | 未修复 |
| 8 | `core/state_manager.py` | 149 | **rollback 不还原 FTS5 索引** — commit → FTS5 增量 → rollback 后，FTS5 残留已回滚状态的数据，搜索返回过期内容 | 未修复 |

## P2 — 中

| # | 文件 | 行 | 问题 | 状态 |
|---|------|-----|------|------|
| 9 | `storage/fts5.py` | 456 | **check_staleness 忽略 chapter_count** — 文档承诺"5+ 章时提示重建"，但只实现了 7 天检查，`chapter_count` 参数从未使用 | 未修复 |
| 10 | `core/search_pipeline.py` | 241 | **_search_vector 按行分块** — `text.split("\n")` 将段落碎片化为孤立单行，丢失段落上下文 | 未修复 |
| 11 | `core/search_pipeline.py` | 344 | **_search_events 全表扫描** — `get_high_pressure_events(threshold=0.0)` 无 LIMIT，返回全部事件 | 未修复 |
| 12 | `core/tool_registry.py` | 126 | **SearchPipeline 路径硬编码 relevance=1.0** — 无视实际 RRF/Reranker 分数，使阈值过滤失效 | 未修复 |
| 13 | `.gitignore` | 31 | **`.novel.fts5.db` 未加入 gitignore** — 运行时生成数据将被 git 追踪 | 未修复 |
| 14 | `core/chunker.py` | 246 | **_split_by_h1 丢弃 Frontmatter** — 第一个 `#` 之前的内容（角色 id/name/aliases）不进入 FTS5 索引 | 未修复 |

## P3 — 低（优化建议）

| # | 文件 | 行 | 问题 | 状态 |
|---|------|-----|------|------|
| 15 | `core/tool_registry.py` | 104 | **_query_canon 和 _query_subconscious 95% 复制粘贴** — 约 86 行重复，仅 source 枚举不同 | 未修复 |
| 16 | `cli/commit.py/stash.py` | 185/45 | **FTS5 增量更新模式重复** — 相同 import/instance/chunk/add/except 模式在两文件重复 | 未修复 |

---

## 修复建议优先级

1. **P1-6 HybridRetriever.search() fallback 数据丢失** — 低风险（无生产调用者），但接口设计缺陷建议修复
2. **P1-8 rollback+FTS5 不一致** — 影响用户数据完整性，建议实现 `rollback_snapshot()` 中的 FTS5 清理
3. **P2-13 .gitignore** — 一行改动，立即生效，防止开发中的 `.fts5.db` 被误提交
4. **P2-14 chunker Frontmatter 丢失** — 影响角色名/ID 的搜索覆盖，建议分块前剥离 frontmatter 并单独索引 metadata
