"""Experimental Python AST relationship index; not a Graphify dependency."""

import ast
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str
    provenance: str = "ast-extracted"


@dataclass(frozen=True)
class Graph:
    revision: str
    edges: tuple[Edge, ...]


def scan_python(source: bytes, module: str) -> Graph:
    """Only syntactically present names/calls; dynamic semantics not inferred."""
    if len(source) > 65536 or not module.isidentifier():
        raise ValueError("graph_input_invalid")
    tree = ast.parse(source.decode("utf-8"), filename=module + ".py")
    edges = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            edges.append(Edge(module, node.name, "declares"))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for imported in node.names:
                edges.append(Edge(module, imported.name, "imports"))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for descendant in ast.walk(node):
                if isinstance(descendant, ast.Call) and isinstance(descendant.func, ast.Name):
                    edges.append(Edge(node.name, descendant.func.id, "calls-name"))
    return Graph(
        revision=hashlib.sha256(source).hexdigest(),
        edges=tuple(sorted(set(edges), key=lambda x: (x.source, x.target, x.relation))),
    )


def search_graph(graph: Graph, query: str, current_source: bytes) -> tuple[Edge, ...]:
    """A stale revision must never be presented as current evidence."""
    if graph.revision != hashlib.sha256(current_source).hexdigest():
        raise ValueError("graph_stale")
    if not query.isidentifier():
        raise ValueError("invalid_query")
    return tuple(edge for edge in graph.edges if edge.target == query or edge.source == query)


def plain_search(source: bytes, query: str) -> bool:
    return query.encode() in source
