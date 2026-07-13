# 0012 — 实时自愈体系：在线守护、分阶段评估与自动修复闭环

将 `novel doctor` 从离线诊断工具升级为在线守护进程，建立跨生成全流程的
分阶段评估体系，实现"检测 → 修复 → 验证"的自动闭环。

## 背景

当前系统的质量保障依赖三个分散机制，缺少统一调度：

1. **离线 doctor**：仅在作者主动运行 `novel doctor` 时检查，无法在问题发生时
   立即响应。
2. **Critic 打分**：仅在每章完成后评分，无法监控生成过程中的中间质量。
3. **快照回滚**：仅支持手动回滚，无法自动判断何时需要回滚。

此外，缺少检索质量的监控（RAG 的 Recall@K / MRR），无法评估搜索管道优化
的实际收益。

## 决策

### 1. 在线守护进程（GuardianDaemon）

将 Doctor 升级为在线守护，在关键节点自动执行检查：

**触发节点**：

| 节点 | 检查内容 | 频率 |
|------|---------|------|
| 每章生成后 | 角色状态一致性、事件时间线连续性 | 每次 |
| 每 3 章 | CANON 规则违反复核、伏笔状态追踪 | 周期 |
| 每 5 章 | 全局因果图完整性、角色弧线偏差 | 周期 |
| 异常发生时 | 自动诊断 + 修复建议 | 事件驱动 |

**自动修复能力**：

| 问题类型 | 自动修复策略 | 需要人工介入 |
|---------|-------------|------------|
| 角色状态未更新 | 重新调用 Manager 提取状态 | 否 |
| 事件遗漏 | 调用 Auditor 从正文补充提取 | 否 |
| FTS5 索引过期 | 自动触发增量更新 | 否 |
| CANON 轻微冲突 | 标记警告 + 提示修复建议 | 是（需作者确认） |
| 时序矛盾 | 自动回滚到上一安全节点 | 是（需确认回滚） |
| 连续 3 章评分 < 60 | 暂停流水线 + 诊断报告 | 是 |

修改文件：`core/doctor.py` → 新增 `GuardianDaemon` 类。
CLI：`novel guardian start` 启动守护模式。

### 2. 分阶段评估体系（StagedEvaluation）

建立贯穿检索 → 生成 → 全局三阶段的评估指标：

```
检索阶段（SearchPipeline）
├── Recall@K：关键信息是否被召回（≥ 0.8 为目标）
├── MRR：排序质量（平均倒数排名，≥ 0.6 为目标）
└── 精确命中率：专有名词搜索准确率（≥ 0.95 为目标）

生成阶段（Writer + Critic）
├── 忠实度：生成内容是否基于提供的上下文（逆向验证）
├── 幻觉率：生成内容与 CANON 冲突的比例（< 5% 为目标）
├── AnchoredIssue 解决率：上一章的 Issue 是否在新章中修复
└── Retry 收敛率：重试后是否真的提升了分数

全局阶段（Director + Guardian）
├── 叙事连贯性：Director 张力曲线是否平滑
├── 角色一致性：Manager 状态转移是否合理
├── 因果完整性：CausalGraph 是否存在悬空节点
└── Token 效率：每千字消耗 Token 数的趋势
```

**A/B 对照实验框架**（用于验证优化策略的实际收益）：
```python
class ABTestRunner:
    """在相同章节上使用不同配置运行，对比评估指标。"""
    
    def compare_reranker(self, chapter_id: str) -> dict:
        """对比开启/关闭 Reranker 的检索质量差异。"""
    
    def compare_models(self, chapter_id: str, models: list[str]) -> dict:
        """对比不同模型的生成质量。"""
```

配置：通过 `novel doctor --abtest <dimension>` 触发对照实验。

新增组件：`core/staged_evaluation.py` — `StagedEvaluator` 类
+ `ABTestRunner` 类。

### 3. 检查点恢复（CheckpointRecovery）

在关键生成阶段保存检查点，失败后从最近检查点继续：

**检查点位置**：
1. Writer.think() 完成后（大纲已生成，不丢失思考结果）
2. Writer.write() 完成后（正文已生成，不丢失创作内容）
3. Critic.evaluate() 完成后（评分已记录）

**恢复逻辑**：
```python
class CheckpointManager:
    def save(self, chapter_id: str, phase: str, data: dict) -> None
    def restore(self, chapter_id: str) -> dict | None
    def clear(self, chapter_id: str) -> None
```

检查点数据存储于 `.snapshots/checkpoints/` 目录（与快照机制复用目录），
JSON 格式序列化。每章完成后清除该章检查点（正常完成），
异常中断时保留供下次恢复。

修改文件：`core/auto_runner.py` — 在关键阶段间插入检查点保存逻辑。

### 4. 故障分析报告（FaultAnalyzer）

当生成失败或评分异常时，自动生成诊断报告：

```python
class FaultAnalyzer:
    def analyze_failure(self, chapter_id: str, error: Exception) -> FaultReport:
        """分析失败原因并生成报告。"""
    
    def suggest_recovery(self, report: FaultReport) -> list[RecoveryAction]:
        """基于故障类型推荐恢复操作。"""
```

报告内容：
- 错误上下文（章节 ID、当前阶段、Agent 调用链）
- 可能原因排名（LLM 超时 / Token 超预算 / CANON 冲突 / 代码异常）
- 建议恢复操作（重试 / 切换模型 / 回滚 / 缩减预算）
- 相关日志和推理链位置

CLI：`novel doctor --diagnose-failure <chapter_id>` 查看历史故障报告。

新增组件：`core/fault_analyzer.py` — `FaultAnalyzer` 类。

### 5. 数据同步校验（CrossSourceValidator）

定期检查多源数据之间的一致性：

```python
class CrossSourceValidator:
    def validate_character_state(self, char_id: str) -> list[Discrepancy]:
        """比对 YAML FM、SQLite EventStore、最近正文中的角色状态。"""
    
    def validate_timeline(self) -> list[Discrepancy]:
        """检查事件时间线是否线性可排序。"""
    
    def validate_canon_compliance(self, chapter_id: str) -> list[Discrepancy]:
        """检查章节内容是否违反 CANON 规则。"""
```

校验策略：
- 轻度不一致（如 EventStore 有记录但 YAML FM 未更新）→ 自动修复
- 中度不一致（如两处数据冲突但差异小）→ 标记警告 + 建议修复
- 严重不一致（如角色已标记死亡但正文中仍在行动）→ 报警 + 暂停流水线

集成于 GuardianDaemon 的定时检查中。

新增组件：`core/cross_source_validator.py` — `CrossSourceValidator` 类。

## 考虑过的替代方案

- **全自动修复**（不经过人工确认）：对于 CANON 冲突等创造性决策，自动修复可能
  违背作者意图。必须保留人工闸门。
- **仅增强 Doctor 不做实时守护**：离线检查无法防止问题在流水线中传播——比如
  第 3 章的角色状态错误可能导致第 4-10 章全部偏离。在线检测是必需的。
- **引入外部监控系统（Prometheus/Grafana）**：过度工程化。小说创作工具的
  监控需求远低于生产系统，内置 SQLite + Rich 终端即可满足。

## 影响

- `core/doctor.py` 大幅扩展（新增 GuardianDaemon, CheckpointManager, FaultAnalyzer）
- 新增 3 个模块：`staged_evaluation.py`、`fault_analyzer.py`、`cross_source_validator.py`
- 新增 CLI 命令：`novel guardian start`、`novel doctor --abtest`、
  `novel doctor --diagnose-failure`
- 检查点数据存储于 `.snapshots/checkpoints/`，需加入 `.gitignore`
- 守护模式增加后台开销（每章约 50-200ms 校验时间），不影响创作流程的关键路径

## 依赖

- ADR 0001（Snapshots）— 检查点复用快照存储机制
- ADR 0004（MetricsStore）— 评估指标的持久化存储
- ADR 0007（SearchPipeline）— 检索阶段评估的数据源
- ADR 0008（ContextAssembler 一致性校验）— 共享 CrossSourceValidator
