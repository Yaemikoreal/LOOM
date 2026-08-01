"""因果图分析 — 基于 networkx 的事件因果 DAG 分析。

从 EventStore 的事件记录构建有向图（DAG），
提供因果路径分析、中心性计算、子图提取等功能。

使用方式:
    analyzer = CausalGraphAnalyzer(event_store)
    stats = analyzer.get_stats()
    path = analyzer.get_causal_path("evt_001", "evt_005")
    important = analyzer.get_central_events(top_k=5)

依赖: networkx（可选依赖 phase2，pip install opennovel[phase2]）
"""

import json
import logging
from typing import Any

from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)


class CausalGraphAnalyzer:
    """因果图分析器。

    从 EventStore 构建 networkx DiGraph，提供图分析能力。
    所有方法在 networkx 不可用时返回空值/空列表。
    """

    def __init__(self, event_store: EventStore | None = None) -> None:
        self._event_store = event_store
        self._graph: Any = None
        self._nx = None  # networkx module reference

    # ── 图构建 ─────────────────────────────────────────────────────────

    def _ensure_import(self) -> bool:
        """确保 networkx 可用。

        Returns:
            True 表示可用
        """
        if self._nx is not None:
            return True
        try:
            import networkx as nx

            self._nx = nx
            return True
        except ImportError:
            logger.warning(
                "networkx 未安装，因果图分析不可用。请执行: pip install networkx"
            )
            return False

    def build_graph(self) -> bool:
        """从 EventStore 构建因果图。

        从 EventStore 加载所有事件，构建有向图：
        - 节点：事件（包含 event_id, chapter_id, character_id, event_type, causal_pressure）
        - 有向边：caused_by 关系（前因 → 后果）
        - 无向边（预留）：related_event_ids 关联关系

        Returns:
            True 表示构建成功
        """
        if not self._ensure_import():
            return False
        if self._event_store is None:
            logger.warning("EventStore 不可用，无法构建因果图")
            return False

        nx = self._nx

        try:
            events = self._event_store.get_all_events()
        except Exception as e:
            logger.error("从 EventStore 加载事件失败: %s", e)
            return False

        if not events:
            logger.info("无事件数据，因果图为空")
            self._graph = nx.DiGraph()
            return True

        self._graph = nx.DiGraph()

        # 添加节点
        for event in events:
            self._graph.add_node(
                event.event_id,
                event_id=event.event_id,
                chapter_id=event.chapter_id,
                character_id=event.character_id,
                event_type=event.event_type,
                causal_pressure=event.causal_pressure,
                description=event.description,
                timestamp=event.timestamp,
            )

        # 添加有向边（caused_by）
        for event in events:
            if event.caused_by:
                # caused_by 指向前置事件，边方向：前因 → 后果
                if event.caused_by in self._graph:
                    self._graph.add_edge(
                        event.caused_by,
                        event.event_id,
                        relation="causal",
                        weight=event.causal_pressure,
                    )

        # 添加无向边（related_event_ids）
        for event in events:
            if event.related_event_ids:
                try:
                    related_ids = json.loads(event.related_event_ids)
                    for rid in related_ids:
                        if rid in self._graph:
                            self._graph.add_edge(
                                event.event_id,
                                rid,
                                relation="related",
                                weight=0.3,
                            )
                except (json.JSONDecodeError, TypeError):
                    pass

        logger.info(
            "因果图构建完成: %d 节点, %d 条边",
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
        )
        return True

    @property
    def graph(self) -> Any:
        """获取底层 networkx DiGraph。"""
        return self._graph

    # ── 分析接口 ───────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取因果图统计信息。

        Returns:
            包含节点数、边数、平均压强等的字典
        """
        if self._graph is None:
            return {"error": "图未构建"}

        nx = self._nx
        stats: dict[str, Any] = {
            "nodes": self._graph.number_of_nodes(),
            "edges": self._graph.number_of_edges(),
            "is_dag": nx.is_directed_acyclic_graph(self._graph) if nx and self._graph else False,
        }

        if self._graph.number_of_nodes() > 0:
            # 因果压强统计
            pressures = [
                data.get("causal_pressure", 0.5)
                for _, data in self._graph.nodes(data=True)
            ]
            stats["avg_pressure"] = round(sum(pressures) / len(pressures), 2)
            stats["max_pressure"] = round(max(pressures), 2)

            # 入度出度统计
            in_degrees = [d for _, d in self._graph.in_degree()]
            out_degrees = [d for _, d in self._graph.out_degree()]
            stats["max_in_degree"] = max(in_degrees) if in_degrees else 0
            stats["max_out_degree"] = max(out_degrees) if out_degrees else 0
            stats["avg_in_degree"] = round(sum(in_degrees) / len(in_degrees), 2) if in_degrees else 0.0

        return stats

    def get_causal_path(self, source_id: str, target_id: str) -> list[str]:
        """获取两个事件之间的因果路径。

        使用 Dijkstra 最短路径算法，按 causal_pressure 加权。

        Args:
            source_id: 起始事件 ID
            target_id: 目标事件 ID

        Returns:
            事件 ID 列表（从 source 到 target），无路径时返回空列表
        """
        if self._graph is None or not self._ensure_import():
            return []
        if source_id not in self._graph or target_id not in self._graph:
            return []

        nx = self._nx
        try:
            # 使用 weight 作为边权重（causal_pressure），找最短路径
            # 权重越低表示因果关联越紧密
            path = nx.shortest_path(
                self._graph, source=source_id, target=target_id, weight="weight"
            )
            return list(path)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def get_central_events(self, top_k: int = 5) -> list[dict[str, Any]]:
        """获取因果图中最核心的事件（按介数中心性）。

        Args:
            top_k: 返回前 K 个事件

        Returns:
            [{"event_id": str, "betweenness": float, "description": str}, ...]
        """
        if self._graph is None or not self._ensure_import():
            return []
        if self._graph.number_of_nodes() < 2:
            return []

        nx = self._nx
        try:
            betweenness = nx.betweenness_centrality(self._graph, weight="weight")
            sorted_nodes = sorted(betweenness.items(), key=lambda x: -x[1])[:top_k]
            return [
                {
                    "event_id": nid,
                    "betweenness": round(centrality, 4),
                    "description": self._graph.nodes[nid].get("description", ""),
                }
                for nid, centrality in sorted_nodes
                if centrality > 0
            ]
        except Exception as e:
            logger.warning("中心性计算失败: %s", e)
            return []

    def get_character_subgraph(self, character_id: str) -> dict[str, Any]:
        """获取指定角色的因果子图。

        Args:
            character_id: 角色 ID

        Returns:
            包含角色事件和因果关系的摘要
        """
        if self._graph is None:
            return {"error": "图未构建"}

        # 找到该角色参与的所有节点
        char_nodes = [
            n
            for n, data in self._graph.nodes(data=True)
            if data.get("character_id") == character_id
        ]

        if not char_nodes:
            return {"character_id": character_id, "events": [], "chains": []}

        # 提取事件列表
        events = []
        for nid in char_nodes:
            data = self._graph.nodes[nid]
            events.append(
                {
                    "event_id": nid,
                    "event_type": data.get("event_type", ""),
                    "description": data.get("description", ""),
                    "causal_pressure": data.get("causal_pressure", 0.5),
                }
            )

        # 提取角色相关的因果链（正向追溯）
        chains = []
        for nid in char_nodes:
            if self._graph.out_degree(nid) > 0:
                successors = list(self._graph.successors(nid))
                chain = [nid]
                for s in successors:
                    chain.append(s)
                chains.append(chain)

        # 按压强降序排列
        events.sort(key=lambda e: -e["causal_pressure"])

        return {
            "character_id": character_id,
            "total_events": len(events),
            "events": events[:20],  # 限制返回数量
            "chains": chains[:5],
        }

    def get_upstream_chain(self, event_id: str, max_depth: int = 10) -> list[str]:
        """从指定事件向上游追溯因果链。

        Args:
            event_id: 起始事件 ID
            max_depth: 最大追溯深度

        Returns:
            上游事件 ID 列表（从远到近）
        """
        if self._graph is None:
            return []

        if event_id not in self._graph:
            return []

        chain: list[str] = []
        visited: set[str] = set()
        current = event_id

        while current and len(chain) < max_depth:
            if current in visited:
                break
            visited.add(current)
            predecessors = list(self._graph.predecessors(current))
            if not predecessors:
                break
            # 按 causal_pressure 取最高的前驱
            predecessor = max(
                predecessors,
                key=lambda n: self._graph.nodes[n].get("causal_pressure", 0.5),
            )
            chain.append(predecessor)
            current = predecessor

        return chain  # 从远到近

    def get_downstream_chain(self, event_id: str, max_depth: int = 10) -> list[str]:
        """从指定事件向下游追溯因果链。

        Args:
            event_id: 起始事件 ID
            max_depth: 最大追溯深度

        Returns:
            下游事件 ID 列表（从近到远）
        """
        if self._graph is None:
            return []

        if event_id not in self._graph:
            return []

        chain: list[str] = []
        visited: set[str] = set()
        current = event_id

        while current and len(chain) < max_depth:
            if current in visited:
                break
            visited.add(current)
            successors = list(self._graph.successors(current))
            if not successors:
                break
            successor = max(
                successors,
                key=lambda n: self._graph.nodes[n].get("causal_pressure", 0.5),
            )
            chain.append(successor)
            current = successor

        return chain

    def get_high_impact_events(self, threshold: float = 0.7) -> list[dict[str, Any]]:
        """获取高因果压强事件。

        Args:
            threshold: 压强阈值

        Returns:
            事件列表，按压强降序排列
        """
        if self._graph is None:
            return []

        result = []
        for nid, data in self._graph.nodes(data=True):
            pressure = data.get("causal_pressure", 0.5)
            if pressure >= threshold:
                result.append(
                    {
                        "event_id": nid,
                        "causal_pressure": pressure,
                        "description": data.get("description", ""),
                        "character_id": data.get("character_id", ""),
                        "chapter_id": data.get("chapter_id", ""),
                    }
                )

        result.sort(key=lambda e: -e["causal_pressure"])
        return result
