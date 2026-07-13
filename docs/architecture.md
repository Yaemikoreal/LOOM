# OpenNovel 架构图

## 一、系统全景

```mermaid
graph TB
    subgraph 入口层["🚪 入口层"]
        CLI["novel CLI<br/>(Typer)"]
        MCP["novel-mcp<br/>(stdio)"]
        GUI["novel-desktop<br/>(PySide6 · 规划中)"]
    end

    subgraph Agent层["🤖 四代理协作层"]
        Writer["Writer<br/>规划·创作·修订·变异"]
        Critic["Critic<br/>五维评分·锚定反馈"]
        Manager["Manager<br/>角色状态·事件提取"]
        Director["Director<br/>全局叙事·策略指导"]
        Actor["Actor<br/>(Gen1) 流式写作"]
        Auditor["Auditor<br/>自纠偏状态提取"]
    end

    subgraph 编排层["⚙️ 编排层"]
        AutoRunner["AutoRunner<br/>· 章节循环编排<br/>· 条件跳转路由<br/>· 重试/热修复<br/>· 调度提议应用"]
        SafetyFence["SafetyFence<br/>递归深度·Token·超时·Canon"]
        AgentAutonomy["AgentAutonomy<br/>ToolCallParser<br/>AutonomousWriteLoop"]
    end

    subgraph 上下文层["🧠 上下文层"]
        ContextAssembler["ContextAssembler<br/>三级策略 · Token熔断 · 权威注入"]
        SearchPipeline["SearchPipeline<br/>三通道 → RRF → Cross-Encoder"]
        ToolRegistry["ToolRegistry<br/>知识查询路由分发"]
    end

    subgraph LLM层["🔌 LLM 总线层"]
        LLMBus["LLMBus<br/>LiteLLM · Tenacity · Token追踪"]
    end

    subgraph 存储层["💾 存储层"]
        SQLite["EventStore<br/>.novel.db"]
        MetricsDB["MetricsStore<br/>.novel.metrics.db"]
        FTS5["Fts5Store<br/>.novel.fts5.db"]
        Vector["VectorStore<br/>BGE-M3 · .index/"]
        YAML["YAMLStorage<br/>Frontmatter 读写"]
        Snapshots["StateManager<br/>.snapshots/"]
    end

    subgraph 数据层["📁 三层数据架构"]
        Human["Human Layer<br/>canon/ · characters/ · draft/<br/>纯 Markdown"]
        Shadow["Machine Shadow<br/>YAML FM · SQLite · Snapshots"]
        Semantic["Semantic Layer<br/>LlamaIndex · 向量索引"]
    end

    CLI -->|子命令路由| AutoRunner
    CLI --> Actor
    CLI --> Auditor
    MCP -->|8 个工具| AutoRunner
    MCP --> Actor

    AutoRunner --> Writer
    AutoRunner --> Critic
    AutoRunner --> Manager
    AutoRunner --> Director
    AutoRunner --> SafetyFence
    AutoRunner --> AgentAutonomy

    Writer --> ContextAssembler
    Critic --> ContextAssembler
    Director --> ContextAssembler
    Actor --> ContextAssembler

    ContextAssembler --> SearchPipeline
    ContextAssembler --> ToolRegistry
    ContextAssembler --> SQLite
    ContextAssembler --> YAML

    SearchPipeline --> Vector
    SearchPipeline --> FTS5
    SearchPipeline --> SQLite

    ToolRegistry --> Vector
    ToolRegistry --> SQLite
    ToolRegistry --> YAML

    Writer --> LLMBus
    Critic --> LLMBus
    Manager --> LLMBus
    Director --> LLMBus
    Actor --> LLMBus
    Auditor --> LLMBus

    AutoRunner --> MetricsDB
    LLMBus --> MetricsDB

    Human -.->|读取| YAML
    Human -.->|读取| Vector
    Shadow --> SQLite
    Shadow --> YAML
    Shadow --> Snapshots
    Semantic --> Vector

    SQLite --> EventLog["EventLog<br/>因果 DAG · CausalGraphAnalyzer"]
```

## 二、四代理自主流水线（`novel auto`）

```mermaid
sequenceDiagram
    participant U as 👤 作者
    participant AR as AutoRunner
    participant W as Writer
    participant C as Critic
    participant M as Manager
    participant D as Director
    participant CA as ContextAssembler
    participant SF as SafetyFence
    participant DB as 存储层

    U->>AR: novel auto

    loop 逐章循环
        Note over AR: 章节类型检测<br/>CLIMAX / TRANSITION / ROUTINE

        AR->>W: think(outline_hint)
        W->>CA: assemble_context(strategy)
        CA-->>W: CANON + STATE + SUBCONSCIOUS
        W-->>AR: ChapterOutline

        Note over AR: 知识缺口检测<br/>ToolRegistry 自主查询

        AR->>SF: check_token_budget()
        SF-->>AR: ✅ / ⛔

        AR->>W: write(outline, knowledge)
        opt 自治模式
            W->>SF: autonomous_call()
            W->>ToolRegistry: query(KnowledgeNeed)
            Note over W: 多轮交互直到完成
        end
        W-->>AR: chapter_text

        AR->>W: write_with_autonomy() | write()

        AR->>C: evaluate(chapter, outline)
        C->>CA: assemble_context()
        C-->>AR: Evaluation { score, AnchoredIssue[] }

        alt 评分 < 80 (最多 5 次重试)
            Note over AR: 优先 hot_fix

            AR->>SF: check_recursion_depth()
            alt 安全围栏放行
                AR->>W: hot_fix(AnchoredIssue[])
                W-->>AR: fixed_text
            else 安全围栏阻断
                AR->>W: revise(feedback)
                W-->>AR: revised_text
            end

            AR->>C: evaluate(revised)
            C-->>AR: new_score
        end

        alt 评分 ≥ 90
            Note over AR: 跳过后处理 → 批处理
        else 评分 < 90
            AR->>M: update(chapter_text)
            M-->>AR: ManagerUpdate
        end

        AR->>DB: create_snapshot()
        AR->>DB: write_chapter()
        AR->>DB: DiffChecker.check()

        alt 章节类型 == CLIMAX 或 每 N 章
            AR->>D: analyze(results[])
            D->>CA: assemble_context()
            D-->>AR: DirectorAnalysis
            opt 调度提议 (INSERT/SKIP/MERGE)
                AR->>AR: _apply_scheduling_proposals()
            end
        end

        Note over AR: Checkpoint 完成
    end

    Note over AR: 批处理延迟的 Manager 更新
    AR->>DB: 写入 run_log.md
    AR-->>U: ✅ 全部章节完成
```

## 三、搜索管道（ADR 0007）

```mermaid
graph LR
    Q["🔍 Query"] --> V["VectorStore<br/>BGE-M3 语义"]
    Q --> F["Fts5Store<br/>unicode61 关键词"]
    Q --> E["EventStore<br/>SQL 事件查询"]

    V -->|"top 15"| RRF["RRF 融合<br/>k=30 · EventStore ×1.5"]
    F -->|"top 15"| RRF
    E -->|"top 15"| RRF

    RRF -->|"top 50 候选"| Early{"top1 > top2×2 ?"}

    Early -->|"否 (多义查询)"| CE["Cross-Encoder<br/>bge-reranker-v2-m3"]
    Early -->|"是 (压倒性优势)"| Top5

    CE -->|"精排"| Top5["→ top 5 结果"]

    Top5 --> CA2["ContextAssembler"]
    Top5 --> TR["ToolRegistry<br/>(use_reranker=False)"]
```

## 四、数据流与权威层级

```mermaid
graph TB
    subgraph 写入路径["✍️ 写入路径"]
        Author["作者写 Markdown"] --> Draft["draft/ 章节"]
        Draft --> Commit["novel commit<br/>5 步审阅流"]
        Commit --> Auditor2["Auditor 提取"]
        Auditor2 --> Diff["Diff 展示"]
        Diff --> Confirm["人工确认"]
        Confirm --> YAML2["YAML Frontmatter"]
        Confirm --> EventDB["SQLite EventStore"]
    end

    subgraph 读取路径["📖 读取路径 (Agent 创作时)"]
        CA_In["ContextAssembler"] --> Auth["权威分层注入"]
        Auth --> CANON["① CANON<br/>不可变世界观<br/>canon/ + 向量检索"]
        Auth --> STATE["② STATE MEMORY<br/>角色状态 · 事件链<br/>YAML FM + SQLite"]
        Auth --> SUBCON["③ SUBCONSCIOUS<br/>灵感碎片<br/>subconscious/ + 向量检索"]
        CANON --> LLM_In["→ LLM Prompt"]
        STATE --> LLM_In
        SUBCON --> LLM_In
    end

    Commit -.->|"每章前自动"| Snap["Snapshot<br/>增量 fm_before/fm_after"]
    Snap -.->|"rollback 时"| Rollback["逐文件校验后覆写"]
```

## 五、存储矩阵

```mermaid
graph LR
    subgraph 物理文件["📂 项目目录"]
        direction TB
        F1["canon/*.md"]
        F2["characters/*.md"]
        F3["draft/ch_*.md"]
        F4["subconscious/*.md"]
        F5["outlines/*.md"]
        F6["foreshadowing/*.md"]
        F7["novel.yaml"]
        F8[".snapshots/"]
    end

    subgraph SQLite["🗄️ SQLite 三库"]
        direction TB
        D1["。novel.db<br/>EventStore<br/>叙事真相"]
        D2["。novel.metrics.db<br/>MetricsStore<br/>运行遥测"]
        D3["。novel.fts5.db<br/>Fts5Store<br/>全文索引"]
    end

    subgraph 向量["🧮 向量索引"]
        direction TB
        I1[".index/canon/"]
        I2[".index/subconscious/"]
    end

    F1 --> I1
    F4 --> I2
    F1 --> D3
    F2 --> D3
    F3 --> D3
    F4 --> D3
    F2 --> D1
    F3 --> D1
    F1 --> F7
```

## 六、组件依赖关系

```mermaid
graph TB
    subgraph 对外接口
        CLI_Typer["CLI (Typer)"]
        MCP_Server["MCP Server"]
    end

    subgraph cli["opennovel/cli/"]
        cli_main["main.py"]
        cli_auto["auto.py"]
        cli_commit["commit.py"]
        cli_write["write.py"]
        cli_stash["stash.py"]
    end

    subgraph agents["opennovel/agents/"]
        ag_w["writer.py"]
        ag_c["critic.py"]
        ag_m["manager.py"]
        ag_d["director.py"]
        ag_a["actor.py"]
        ag_au["auditor.py"]
    end

    subgraph core["opennovel/core/"]
        co_ar["auto_runner.py"]
        co_ca["context_assembler.py"]
        co_llm["llm.py"]
        co_sf["safety_fence.py"]
        co_aa["agent_autonomy.py"]
        co_tr["tool_registry.py"]
        co_sp["search_pipeline.py"]
        co_ch["chunker.py"]
        co_rr["reranker.py"]
        co_sm["state_manager.py"]
        co_dc["diff_checker.py"]
        co_cc["canon_checker.py"]
        co_cg["causal_graph.py"]
        co_hr["hybrid_retriever.py"]
        co_re["retriever.py"]
        co_ms["mutation_strategy.py"]
        co_cu["chapter_utils.py"]
        co_pr["parser.py"]
        co_do["doctor.py"]
        co_ea["evaluation_auditor.py"]
        co_spj["state_projector.py"]
    end

    subgraph storage["opennovel/storage/"]
        st_sq["sqlite.py"]
        st_mt["metrics.py"]
        st_ft["fts5.py"]
        st_vc["vector.py"]
        st_ym["yaml_storage.py"]
        st_fs["foreshadowing.py"]
        st_tl["timeline.py"]
        st_su["summaries.py"]
    end

    subgraph schemas["opennovel/schemas/"]
        sc_ch["character.py"]
        sc_ev["event.py"]
        sc_ev2["evaluation.py"]
        sc_ol["outline.py"]
        sc_oe["outline_evaluation.py"]
        sc_di["director.py"]
        sc_mt["mutation.py"]
        sc_kn["knowledge.py"]
        sc_fs["foreshadowing.py"]
        sc_st["state.py"]
        sc_mu["manager_update.py"]
        sc_me["metrics.py"]
        sc_se["search.py"]
    end

    CLI_Typer --> cli_main
    MCP_Server --> co_ar
    MCP_Server --> ag_a

    cli_main --> cli_auto
    cli_main --> cli_commit
    cli_main --> cli_write
    cli_main --> cli_stash

    cli_auto --> co_ar
    cli_commit --> ag_au
    cli_write --> ag_a
    cli_stash --> co_re

    co_ar --> ag_w
    co_ar --> ag_c
    co_ar --> ag_m
    co_ar --> ag_d
    co_ar --> co_sf
    co_ar --> co_aa
    co_ar --> co_sm
    co_ar --> co_dc
    co_ar --> co_cu
    co_ar --> co_ms
    co_ar --> co_ea
    co_ar --> st_mt

    ag_w --> co_ca
    ag_w --> co_llm
    ag_w --> co_hr
    ag_w --> co_tr
    ag_w --> co_ms
    ag_c --> co_ca
    ag_c --> co_llm
    ag_c --> co_hr
    ag_d --> co_ca
    ag_d --> co_llm
    ag_a --> co_ca
    ag_a --> co_llm
    ag_m --> co_llm

    co_ca --> co_sp
    co_ca --> co_tr
    co_ca --> co_spj
    co_ca --> st_sq
    co_ca --> st_ym

    co_sp --> co_ch
    co_sp --> co_rr
    co_sp --> st_ft
    co_sp --> st_vc
    co_sp --> st_sq

    co_hr --> co_sp
    co_hr --> co_re
    co_hr --> st_sq

    co_tr --> co_re
    co_tr --> st_sq
    co_tr --> st_ym

    co_aa --> co_tr
    co_aa --> co_sf

    co_cg --> st_sq
    co_cc --> co_sf
    co_sm --> st_ym
    co_ea --> st_mt
    co_re --> st_vc
