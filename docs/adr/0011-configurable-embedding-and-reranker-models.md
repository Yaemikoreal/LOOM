# 0011 — 可配置嵌入与重排序模型

OpenNovel 的语义检索层支持在 `novel.yaml` 中切换本地 Embedding 与 Cross-Encoder Reranker 模型，默认保持 BGE-M3 + bge-reranker-v2-m3，可选 Qwen3-Embedding / Qwen3-Reranker 等 Hugging Face 模型。

## 背景

原有检索架构存在两个与模型选择相关的问题：

- **Embedding 模型硬编码**：`Retriever` 与 `VectorStore` 的默认 `embedding_model` 写死为 `local:BAAI/bge-m3`，用户无法在不改代码的情况下切换模型
- **Reranker 配置被忽略**：`novel.yaml` 中已有 `reranker_model` / `reranker_device` 字段，但 `Reranker` 类内部硬编码 `BAAI/bge-reranker-v2-m3`，配置字段实际不生效

随着 [Qwen3-Embedding 系列](https://qwenlm.github.io/blog/qwen3-embedding/)（0.6B / 4B / 8B，Apache 2.0）和 Qwen3-Reranker 系列发布，中文语义理解与重排序有了新的可选方案。项目需要一种机制让用户按硬件能力自主选择模型，而不是被默认模型绑定。

## 决策

### 配置字段

在 `novel.yaml` 中新增 / 启用以下字段：

```yaml
# 本地嵌入模型（需 pip install -e ".[local-embedding]"）
# "local:" 前缀表示通过 sentence-transformers / HuggingFaceEmbedding 加载
embedding_model: "local:Qwen/Qwen3-Embedding-0.6B"

# Cross-Encoder 重排序模型（Hugging Face 模型名）
reranker_model: "Qwen/Qwen3-Reranker-0.6B"
reranker_device: ""        # 空字符串 = 自动检测 (cuda > mps > cpu)
reranker_enabled: true     # 可关闭以降级为纯 RRF
```

未设置时回退到：

- `embedding_model`: `local:BAAI/bge-m3`
- `reranker_model`: `BAAI/bge-reranker-v2-m3`
- `reranker_device`: 自动检测

### 配置生效路径

1. `LoomConfig.load(project_root)` 从 `novel.yaml` 读取上述字段
2. `Retriever.__init__` 在未显式传入 `embedding_model` 时，自动加载 `LoomConfig` 并应用配置值
3. `VectorStore.__init__` 在未显式传入 `embedding_model` 时，同样自动加载 `LoomConfig`
4. `SearchPipeline.reranker` 属性在初始化 `Reranker` 时，从 `LoomConfig` 读取 `reranker_model` 与 `reranker_device` 传入
5. `Reranker` 改为按 `(model_name, device)` 缓存模型实例，不同配置之间互不干扰

### 支持的模型格式

- **Embedding**: `local:<huggingface-model-name>`，由 `llama_index.embeddings.huggingface.HuggingFaceEmbedding` 加载
  - 示例：`local:BAAI/bge-m3`、`local:Qwen/Qwen3-Embedding-0.6B`
- **Reranker**: Hugging Face 模型名，由 `sentence_transformers.CrossEncoder` 加载
  - 示例：`BAAI/bge-reranker-v2-m3`、`Qwen/Qwen3-Reranker-0.6B`

### Qwen3-Embedding 选项参考

根据 [Qwen3-Embedding 官方博客](https://qwenlm.github.io/blog/qwen3-embedding/)：

| 模型 | 参数量 | 维度 | 序列长度 | 适用场景 |
|---|---|---|---|---|
| Qwen3-Embedding-0.6B | 0.6B | 1024 | 32K | CPU / 低显存，平衡速度与效果 |
| Qwen3-Embedding-4B | 4B | 2560 | 32K | 中高端 GPU，更好语义 |
| Qwen3-Embedding-8B | 8B | 4096 | 32K | 高端 GPU，最佳效果 |
| Qwen3-Reranker-0.6B | 0.6B | - | 32K | CPU / 低显存重排序 |
| Qwen3-Reranker-4B | 4B | - | 32K | 中高端 GPU 重排序 |
| Qwen3-Reranker-8B | 8B | - | 32K | 高端 GPU 重排序 |

## 考虑过的选项

| 方案 | 否决原因 |
|---|---|
| 默认直接切换到 Qwen3-Embedding | 模型体积与硬件要求差异大，不能替所有用户做决定 |
| 引入专用向量数据库（Chroma/Milvus Lite） | 当前 LlamaIndex + SQLite 文件索引已满足需求；向量数据库是后续可选优化，非本 ADR 范围 |
| 在 `.opennovel.yaml` 全局配置 embedding_model | 不同小说可能使用不同模型；项目级 `novel.yaml` 更合理 |
| 为 Reranker 每个实例独立加载模型 | 同一配置多次加载浪费内存；改为按 `(model_name, device)` 缓存 |

## 后果

### 正面

- 用户可按硬件条件与语言场景选择 Embedding / Reranker 模型
- BGE-M3 与 bge-reranker-v2-m3 继续作为零配置默认，向后兼容
- `novel.yaml` 中的 `reranker_model` / `reranker_device` 字段真正生效
- 为后续引入更多本地 Embedding 模型（如 GTE-Qwen、Jina 等）建立统一配置入口

### 负面

- 切换模型后必须执行 `novel reindex` 重建向量索引，否则旧索引与新模型维度不匹配
- 大模型（4B/8B）首次下载与加载时间较长，低显存环境可能 OOM
- 不同模型的效果差异需要用户自行评估，项目不提供自动选型

## 相关文件

- `opennovel/core/config.py` — `LoomConfig` 新增 `embedding_model` 字段
- `opennovel/core/retriever.py` — 未传参时从配置加载 `embedding_model`
- `opennovel/storage/vector.py` — 未传参时从配置加载 `embedding_model`
- `opennovel/core/reranker.py` — 支持可配置 `model_name` / `device`，按配置缓存
- `opennovel/core/search_pipeline.py` — `Reranker` 初始化时传入配置
