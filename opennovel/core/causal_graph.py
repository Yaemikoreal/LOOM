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

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)


class CausalGraphAnalyzer:
    """因果图分析器。

    从 EventStore 构建 networkx DiGraph，提供图分析能力。
    所有方法在 networkx 不可用时返回空值/空列表。
    """

    # 全局因果分析缓存文件名与默认有效期
    CACHE_FILENAME = ".novel.causal.cache.json"
    CACHE_TTL_DAYS = 7

    def __init__(self, event_store: EventStore | None = None) -> None:
        self._event_store = event_store
        self._graph: Any = None
        self._nx = None  # networkx module reference

    # ── 缓存辅助 ─────────────────────────────────────────────────────────

    def _get_project_root(self, project_root: Path | None = None) -> Path | None:
        """推断项目根目录。

        优先使用传入的 project_root；否则尝试从 EventStore 的 db_path 推断。
        """
        if project_root is not None:
            return project_root
        if self._event_store is not None and hasattr(self._event_store, "db_path"):
            return Path(self._event_store.db_path).parent
        return None

    def _compute_events_hash(self, events: list[Any]) -> str:
        """基于事件总数与最新事件 ID 计算简单 hash。

        Args:
            events: EventLog 列表

        Returns:
            hash 字符串
        """
        count = len(events)
        latest_id = "none"
        if events:
            # 使用自增主键 id 判断最新事件，比 event_id 更稳定
            # 兼容 mock 对象：id 不存在或不可比较时回退到 event_id
            def _event_key(e: Any) -> str:
                # 统一转为字符串比较，避免 int 与 str 混用导致 TypeError
                eid = getattr(e, "id", None)
                if eid is not None:
                    return str(eid)
                return str(getattr(e, "event_id", ""))

            latest = max(events, key=_event_key)
            latest_id = str(getattr(latest, "id", None) or getattr(latest, "event_id", "none"))
        raw = f"{count}:{latest_id}".encode()
        return hashlib.md5(raw).hexdigest()

    def _get_cache_path(self, project_root: Path) -> Path:
        """获取缓存文件路径。"""
        return project_root / self.CACHE_FILENAME

    def _load_cache(self, project_root: Path) -> dict[str, Any] | None:
        """从磁盘加载缓存。"""
        cache_path = self._get_cache_path(project_root)
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取因果分析缓存失败: %s", e)
            return None

    def _save_cache(self, project_root: Path, analysis: dict[str, Any], events_hash: str) -> bool:
        """将分析结果写入磁盘缓存。"""
        cache_path = self._get_cache_path(project_root)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "events_hash": events_hash,
            "analysis": analysis,
        }
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            logger.warning("写入因果分析缓存失败: %s", e)
            return False

    def is_cache_valid(self, project_root: Path) -> bool:
        """检查全局因果分析缓存是否仍有效。

        校验项：
        - 缓存文件存在且可解析
        - events_hash 与当前 EventStore 一致
        - 缓存未超过默认 7 天有效期

        Args:
            project_root: 项目根目录

        Returns:
            True 表示缓存有效
        """
        cache = self._load_cache(project_root)
        if cache is None:
            return False

        # 检查有效期
        try:
            cached_time = datetime.fromisoformat(cache.get("timestamp", ""))
            if cached_time.tzinfo is None:
                cached_time = cached_time.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - cached_time > timedelta(days=self.CACHE_TTL_DAYS):
                return False
        except (ValueError, TypeError):
            return False

        # 检查事件 hash 一致性
        if self._event_store is None:
            return False
        try:
            events = self._event_store.get_all_events()
        except Exception as e:
            logger.warning("读取事件账本失败: %s", e)
            return False

        current_hash = self._compute_events_hash(events)
        return cache.get("events_hash") == current_hash

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
            logger.warning("networkx 未安装，因果图分析不可用。请执行: pip install networkx")
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
            if event.caused_by and event.caused_by in self._graph:
                # caused_by 指向前置事件，边方向：前因 → 后果
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
                data.get("causal_pressure", 0.5) for _, data in self._graph.nodes(data=True)
            ]
            stats["avg_pressure"] = round(sum(pressures) / len(pressures), 2)
            stats["max_pressure"] = round(max(pressures), 2)

            # 入度出度统计
            in_degrees = [d for _, d in self._graph.in_degree()]
            out_degrees = [d for _, d in self._graph.out_degree()]
            stats["max_in_degree"] = max(in_degrees) if in_degrees else 0
            stats["max_out_degree"] = max(out_degrees) if out_degrees else 0
            stats["avg_in_degree"] = (
                round(sum(in_degrees) / len(in_degrees), 2) if in_degrees else 0.0
            )

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

    # ── 全局后台分析 ───────────────────────────────────────────────────

    def run_global_analysis(
        self,
        project_root: Path | None = None,
        use_cache: bool = True,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """执行全局因果图后台分析并缓存结果。

        计算内容：
        - 图规模统计
        - 介数中心性（betweenness centrality）
        - 社区发现（greedy_modularity_communities / label_propagation_communities）
        - 高风险事件（高因果压强）
        - 关键路径（DAG 最长路径）

        单角色子图、单事件链等实时查询不经过本方法。

        Args:
            project_root: 项目根目录；未提供时尝试从 EventStore 推断
            use_cache: 是否优先使用缓存
            top_k: 返回的核心事件数量

        Returns:
            分析结果字典；networkx 不可用或图未构建时返回降级结果
        """
        root = self._get_project_root(project_root)

        # 尝试读取缓存
        if use_cache and root is not None and self.is_cache_valid(root):
            cache = self._load_cache(root)
            if cache and "analysis" in cache:
                logger.info("命中因果分析缓存")
                return cache["analysis"]

        # 构建图
        if self._graph is None and not self.build_graph():
            return {"error": "图构建失败", "nodes": 0, "edges": 0}

        if not self._ensure_import() or self._nx is None:
            return {"error": "networkx 未安装", "nodes": 0, "edges": 0}

        nx = self._nx
        graph = self._graph

        analysis: dict[str, Any] = {
            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges(),
            "is_dag": nx.is_directed_acyclic_graph(graph) if graph else False,
        }

        if graph is None or graph.number_of_nodes() == 0:
            analysis["central_events"] = []
            analysis["communities"] = []
            analysis["high_impact_events"] = []
            analysis["critical_path"] = []
            analysis["risk_score"] = 0.0
            self._maybe_save_cache(root, analysis)
            return analysis

        # 1. 中心性分析
        try:
            betweenness = nx.betweenness_centrality(graph, weight="weight")
            sorted_nodes = sorted(betweenness.items(), key=lambda x: -x[1])[:top_k]
            analysis["central_events"] = [
                {
                    "event_id": nid,
                    "betweenness": round(centrality, 4),
                    "description": graph.nodes[nid].get("description", ""),
                }
                for nid, centrality in sorted_nodes
                if centrality > 0
            ]
        except Exception as e:
            logger.warning("中心性计算失败: %s", e)
            analysis["central_events"] = []

        # 2. 社区发现（在底层无向图上进行）
        analysis["communities"] = self._detect_communities(graph, nx)

        # 3. 高风险事件
        analysis["high_impact_events"] = self.get_high_impact_events(threshold=0.7)[:top_k]

        # 4. 关键路径：DAG 最长路径
        try:
            if analysis["is_dag"]:
                critical_path = nx.dag_longest_path(graph)
                analysis["critical_path"] = critical_path
                analysis["critical_path_length"] = len(critical_path)
            else:
                # 非 DAG 时退化为按出度排序的关键节点链
                critical_path = sorted(
                    graph.nodes(),
                    key=lambda n: graph.out_degree(n),
                    reverse=True,
                )[:top_k]
                analysis["critical_path"] = critical_path
                analysis["critical_path_length"] = len(critical_path)
        except Exception as e:
            logger.warning("关键路径计算失败: %s", e)
            analysis["critical_path"] = []
            analysis["critical_path_length"] = 0

        # 5. 聚合风险分数：高风险事件占比 + 中心性集中度
        high_impact_count = len(analysis["high_impact_events"])
        risk_score = min(
            1.0,
            high_impact_count / max(1, graph.number_of_nodes())
            + 0.1 * len(analysis["communities"]),
        )
        analysis["risk_score"] = round(risk_score, 4)
        analysis["community_count"] = len(analysis["communities"])

        self._maybe_save_cache(root, analysis)
        return analysis

    def _maybe_save_cache(self, project_root: Path | None, analysis: dict[str, Any]) -> None:
        """保存缓存（如果项目根目录已知）。"""
        if project_root is None or self._event_store is None:
            return
        try:
            events = self._event_store.get_all_events()
            events_hash = self._compute_events_hash(events)
            self._save_cache(project_root, analysis, events_hash)
        except Exception as e:
            logger.warning("保存因果分析缓存失败: %s", e)

    def _detect_communities(self, graph: Any, nx: Any) -> list[list[str]]:
        """执行社区发现，优先使用 greedy_modularity，失败后回退 label_propagation。

        Args:
            graph: networkx 图对象
            nx: networkx 模块

        Returns:
            社区列表，每个社区是事件 ID 列表
        """
        if graph.number_of_nodes() < 2:
            return []

        undirected = graph.to_undirected()
        communities: list[list[str]] = []

        try:
            if hasattr(nx, "community") and hasattr(nx.community, "greedy_modularity_communities"):
                for community in nx.community.greedy_modularity_communities(
                    undirected, weight="weight"
                ):
                    communities.append(sorted(community))
                return communities
        except Exception as e:
            logger.warning("greedy_modularity_communities 失败: %s", e)

        try:
            if hasattr(nx, "community") and hasattr(nx.community, "label_propagation_communities"):
                for community in nx.community.label_propagation_communities(undirected):
                    communities.append(sorted(community))
                # 去重
                seen: set[str] = set()
                unique: list[list[str]] = []
                for comm in communities:
                    key = ",".join(comm)
                    if key not in seen:
                        seen.add(key)
                        unique.append(comm)
                return unique
        except Exception as e:
            logger.warning("label_propagation_communities 失败: %s", e)

        return []
