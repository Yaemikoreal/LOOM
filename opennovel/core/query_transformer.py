"""查询转换模块 — Multi-Query / HyDE / Query Decomposition。

在 SearchPipeline 入口前对查询进行智能改写，弥补"查询-文档语义鸿沟"：
- Multi-Query：短查询改写为多个不同角度查询
- HyDE：生成假想答案后用答案向量检索
- Query Decomposition：拆解复合查询为子查询

详见 docs/adr/0009-advanced-retrieval-optimization.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TransformedQuery:
    """转换后的查询（可能包含多个子查询）。"""

    original: str
    """原始查询"""

    variants: list[str] = field(default_factory=list)
    """改写后的查询变体列表"""

    strategy: str = "none"
    """使用的转换策略：none / multi_query / hyde / decompose"""

    hyde_document: str = ""
    """HyDE 模式生成的假想答案"""


class QueryTransformer:
    """查询转换器。

    根据查询特征自动选择转换策略，为 SearchPipeline 提供增强的查询变体。

    使用方式:
        transformer = QueryTransformer()
        result = transformer.transform("主角的性格特点")
        # result.variants = ["主角的决策模式", "主角的对话风格", "主角的核心动机"]
    """

    # 抽象概念关键词（触发 HyDE 策略）
    _ABSTRACT_KEYWORDS = {
        "性格", "特点", "氛围", "主题", "风格", "气质", "基调",
        "感觉", "印象", "形象", "人格", "本质",
    }

    # 复合查询连接词（触发 Decomposition 策略）
    _COMPOUND_MARKERS = {"和", "与", "以及", "为什么", "如何", "怎么", "因为", "所以"}

    def __init__(self, llm_bus=None) -> None:
        """初始化查询转换器。

        Args:
            llm_bus: LLMBus 实例（HyDE 策略需要 LLM 生成假想答案）
        """
        self._llm_bus = llm_bus

    def transform(
        self,
        query: str,
        enable_hyde: bool = True,
        enable_multi_query: bool = True,
        enable_decompose: bool = True,
    ) -> TransformedQuery:
        """对查询进行智能转换。

        策略选择逻辑：
        - 查询 < 5 字 → Multi-Query（短查询语义不足）
        - 包含抽象概念词 → HyDE
        - 包含复合连接词 → Decomposition
        - 默认 → 不做转换

        Args:
            query: 原始查询文本
            enable_hyde: 是否启用 HyDE
            enable_multi_query: 是否启用 Multi-Query
            enable_decompose: 是否启用 Decomposition

        Returns:
            TransformedQuery 转换结果
        """
        query = query.strip()
        if not query:
            return TransformedQuery(original=query)

        # 检测适用策略
        query_len = len(query)

        # 规则 1：短查询 → Multi-Query
        if enable_multi_query and query_len < 5:
            variants = self._multi_query_expand(query)
            return TransformedQuery(
                original=query,
                variants=variants,
                strategy="multi_query",
            )

        # 规则 2：抽象概念 → HyDE
        if enable_hyde and self._is_abstract(query):
            hyde_doc = self._generate_hyde_document(query)
            return TransformedQuery(
                original=query,
                variants=[hyde_doc] if hyde_doc else [],
                strategy="hyde",
                hyde_document=hyde_doc,
            )

        # 规则 3：复合查询 → Decomposition
        if enable_decompose and self._is_compound(query):
            variants = self._decompose_query(query)
            if variants:
                return TransformedQuery(
                    original=query,
                    variants=variants,
                    strategy="decompose",
                )

        # 默认：不做转换
        return TransformedQuery(original=query, variants=[query], strategy="none")

    def _is_abstract(self, query: str) -> bool:
        """检测查询是否包含抽象概念。"""
        return any(kw in query for kw in self._ABSTRACT_KEYWORDS)

    def _is_compound(self, query: str) -> bool:
        """检测查询是否为复合查询。"""
        return any(marker in query for marker in self._COMPOUND_MARKERS) and len(query) > 10

    def _multi_query_expand(self, query: str, n_variants: int = 3) -> list[str]:
        """将短查询扩展为多个不同角度的查询变体。

        基于同义词替换和角度扩展，不需要 LLM 调用。

        Args:
            query: 短查询
            n_variants: 生成的变体数量

        Returns:
            查询变体列表
        """
        # 基于规则的简单扩展（不依赖 LLM）
        variants = [query]

        # 添加语义相近的变体提示
        expansions: dict[str, list[str]] = {
            "魔法": ["魔力体系", "施法规则", "法术效果"],
            "角色": ["人物关系", "性格特点", "行为动机"],
            "世界": ["地理设定", "历史背景", "文化习俗"],
            "战斗": ["打斗场景", "能力对决", "战术策略"],
            "感情": ["情感描写", "内心独白", "关系发展"],
        }

        query_lower = query
        for key, exps in expansions.items():
            if key in query_lower:
                variants.extend(exps[:n_variants - 1])
                break

        # 如果无匹配的扩展，添加通配变体
        if len(variants) == 1:
            variants.append(f"{query} 相关")
            variants.append(f"{query} 设定")

        return variants[:n_variants]

    def _generate_hyde_document(self, query: str) -> str:
        """使用 LLM 生成假想答案（HyDE 模式）。

        Args:
            query: 抽象查询

        Returns:
            假想答案文本，LLM 不可用时返回空字符串
        """
        if self._llm_bus is None:
            logger.debug("HyDE 跳过：LLMBus 不可用")
            return ""

        try:
            prompt = (
                f"请用一段文字（约100字）回答以下问题，"
                f"描述一个虚构世界观中可能的情况：\n{query}\n\n"
                f"假想答案："
            )
            messages = [{"role": "user", "content": prompt}]
            response = self._llm_bus.chat(messages, temperature=0.3, max_tokens=150)
            hyde_text = response.choices[0].message.content.strip()
            logger.debug("HyDE 生成: %s → %s", query[:30], hyde_text[:50])
            return hyde_text
        except Exception as e:
            logger.debug("HyDE 生成失败: %s", e)
            return ""

    def _decompose_query(self, query: str) -> list[str]:
        """将复合查询拆解为子查询。

        基于简单规则，不依赖 LLM。

        Args:
            query: 复合查询

        Returns:
            子查询列表
        """
        sub_queries = []

        # 按连接词拆解
        separators = ["和", "与", "以及", "为什么", "如何", "怎么"]
        remaining = query
        for sep in separators:
            if sep in remaining:
                parts = remaining.split(sep, 1)
                if parts[0].strip():
                    sub_queries.append(parts[0].strip())
                remaining = parts[1].strip() if len(parts) > 1 else ""

        if remaining and remaining.strip():
            sub_queries.append(remaining.strip())

        # 至少保留原始查询
        if not sub_queries:
            sub_queries.append(query)
        elif query not in sub_queries:
            sub_queries.insert(0, query)

        return sub_queries[:5]
