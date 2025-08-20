# src/fullfathom5/bones/graph_manager.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Any, Set, Tuple

NodeId = str

@dataclass
class Node:
    id: NodeId
    fn: Optional[str] = None        # name of handler (string to avoid importing)
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Edge:
    src: NodeId
    dst: NodeId
    cond: Optional[str] = None      # name of condition fn

@dataclass
class Graph:
    name: str
    nodes: Dict[NodeId, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    start: NodeId = ""

class GraphManager:
    def __init__(self):
        self._graphs: Dict[str, Graph] = {}

    def create_graph(self, name: str, nodes: List[Node], edges: List[Edge], start: NodeId) -> None:
        g = Graph(name=name, start=start)
        for n in nodes:
            g.nodes[n.id] = n
        g.edges.extend(edges)
        if self.detect_cycle(g):
            raise ValueError("Graph has a cycle")
        self._graphs[name] = g

    def get(self, name: str) -> Graph:
        return self._graphs[name]

    def detect_cycle(self, g: Graph) -> bool:
        # classic DFS cycle check
        adj: Dict[NodeId, List[NodeId]] = {}
        for e in g.edges:
            adj.setdefault(e.src, []).append(e.dst)

        visiting: Set[NodeId] = set()
        visited: Set[NodeId] = set()

        def dfs(u: NodeId) -> bool:
            if u in visiting: return True
            if u in visited: return False
            visiting.add(u)
            for v in adj.get(u, []):
                if dfs(v): return True
            visiting.remove(u)
            visited.add(u)
            return False

        return dfs(g.start)

    def export_graph(self, name: str) -> Dict[str, Any]:
        g = self._graphs[name]
        return {
            "name": g.name,
            "start": g.start,
            "nodes": [{ "id": n.id, "fn": n.fn, "meta": n.meta } for n in g.nodes.values()],
            "edges": [{ "src": e.src, "dst": e.dst, "cond": e.cond } for e in g.edges],
        }

    def import_graph(self, data: Dict[str, Any]) -> None:
        nodes = [Node(**d) for d in data["nodes"]]
        edges = [Edge(**d) for d in data["edges"]]
        self.create_graph(data["name"], nodes, edges, data["start"])

# Provide a default chat graph spec; not used yet
def default_chat_graph() -> Dict[str, Any]:
    return {
        "name": "chat_default",
        "start": "SELECT",
        "nodes": [
            {"id": "SELECT", "fn": "select"},
            {"id": "CONTEXT", "fn": "context"},
            {"id": "SOLVE", "fn": "solve"},
            {"id": "ANSWER", "fn": "answer"},
        ],
        "edges": [
            {"src": "SELECT", "dst": "CONTEXT"},
            {"src": "CONTEXT", "dst": "SOLVE"},
            {"src": "SOLVE",  "dst": "ANSWER"},
        ],
    }:while