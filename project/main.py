#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass
from graphviz import Digraph
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Sequence,
    Set,
    Tuple,
    Union,
)

# TODO log instead of print

#   .--node visitor--------------------------------------------------------.
#   |                        _              _     _ _                      |
#   |        _ __   ___   __| | ___  __   _(_)___(_) |_ ___  _ __          |
#   |       | '_ \ / _ \ / _` |/ _ \ \ \ / / / __| | __/ _ \| '__|         |
#   |       | | | | (_) | (_| |  __/  \ V /| \__ \ | || (_) | |            |
#   |       |_| |_|\___/ \__,_|\___|   \_/ |_|___/_|\__\___/|_|            |
#   |                                                                      |
#   '----------------------------------------------------------------------'


ImportContext = Tuple[Union[ast.ClassDef, ast.FunctionDef], ...]


class ImportSTMT(NamedTuple):
    context: ImportContext
    node: ast.Import


class ImportFromSTMT(NamedTuple):
    context: ImportContext
    node: ast.ImportFrom

    # TODO use context in graph (nested import stmt)


class NodeVisitorImports(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._imports_stmt: List[ImportSTMT] = []
        self._imports_from_stmt: List[ImportFromSTMT] = []
        self._context: ImportContext = tuple()

    @property
    def imports_stmt(self) -> Sequence[ImportSTMT]:
        return self._imports_stmt

    @property
    def imports_from_stmt(self) -> Sequence[ImportFromSTMT]:
        return self._imports_from_stmt

    def visit_Import(self, node: ast.Import) -> None:
        self._imports_stmt.append(ImportSTMT(self._context, node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._imports_from_stmt.append(ImportFromSTMT(self._context, node))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._context += (node,)
        for child in ast.iter_child_nodes(node):
            ast.NodeVisitor.visit(self, child)
        self._context = self._context[:-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._context += (node,)
        for child in ast.iter_child_nodes(node):
            ast.NodeVisitor.visit(self, child)
        self._context = self._context[:-1]


# .
#   .--contents------------------------------------------------------------.
#   |                                _             _                       |
#   |                 ___ ___  _ __ | |_ ___ _ __ | |_ ___                 |
#   |                / __/ _ \| '_ \| __/ _ \ '_ \| __/ __|                |
#   |               | (_| (_) | | | | ||  __/ | | | |_\__ \                |
#   |                \___\___/|_| |_|\__\___|_| |_|\__|___/                |
#   |                                                                      |
#   '----------------------------------------------------------------------'


def _get_python_files(path: Path) -> Iterable[Path]:
    if path.is_symlink():
        return

    if path.is_file() and path.suffix == ".py":
        yield path.resolve()
        return

    for f in path.iterdir():
        if f.is_dir():
            yield from _get_python_files(f)
            continue

        if f.suffix == ".py":
            yield f.resolve()


def _load_python_contents(files: Set[Path]) -> Mapping[Path, str]:
    raw_contents: Dict[Path, str] = {}
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                raw_contents.setdefault(path, f.read())
        except UnicodeDecodeError as e:
            print("Cannot read python file %s: %s" % (path, e))

    return raw_contents


def _visit_python_contents(
    python_contents: Mapping[Path, str]
) -> Sequence[NodeVisitorImports]:
    visitors: List[NodeVisitorImports] = []
    for path, python_content in python_contents.items():
        try:
            tree = ast.parse(python_content)
        except SyntaxError as e:
            print("Cannot visit python file %s: %s" % (path, e))
            continue

        visitor = NodeVisitorImports(path)
        visitor.visit(tree)
        visitors.append(visitor)
    return visitors


# .
#   .--graph---------------------------------------------------------------.
#   |                                           _                          |
#   |                      __ _ _ __ __ _ _ __ | |__                       |
#   |                     / _` | '__/ _` | '_ \| '_ \                      |
#   |                    | (_| | | | (_| | |_) | | | |                     |
#   |                     \__, |_|  \__,_| .__/|_| |_|                     |
#   |                     |___/          |_|                               |
#   '----------------------------------------------------------------------'


def _make_graph(
    path: Path,
    import_edges: Sequence[ImportEdge],
    import_cycles: Sequence[ImportCycle],
) -> Digraph:
    # TODO
    target_dir = (
        Path(os.path.abspath(__file__))
        .parent.parent.joinpath("outputs")
        .joinpath(path.name)
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    d = Digraph("unix", filename=target_dir.joinpath("import-cycles.gv"))

    with d.subgraph() as ds:
        for edge in import_edges:
            ds.node(edge.module)
            ds.node(edge.imports)

            # TODO use different colors for different cycles
            if _is_in_cycle(edge, import_cycles):
                color = "red"
            else:
                color = "black"

            ds.attr("edge", color=color)
            ds.edge(edge.module, edge.imports)

    return d


def _is_in_cycle(edge: ImportEdge, import_cycles: Sequence[ImportCycle]) -> bool:
    for import_cycle in import_cycles:
        try:
            idx = import_cycle.cycle.index(edge.module)
        except ValueError:
            continue

        if import_cycle.cycle[idx + 1] == edge.imports:
            return True
    return False


# .
#   .--cycles--------------------------------------------------------------.
#   |                                     _                                |
#   |                      ___ _   _  ___| | ___  ___                      |
#   |                     / __| | | |/ __| |/ _ \/ __|                     |
#   |                    | (__| |_| | (__| |  __/\__ \                     |
#   |                     \___|\__, |\___|_|\___||___/                     |
#   |                          |___/                                       |
#   '----------------------------------------------------------------------'


class ImportEdge(NamedTuple):
    module: str
    imports: str


class ImportCycle(NamedTuple):
    cycle: Tuple[str, ...]
    chain: Sequence[str]


def _get_edges_and_imports(
    base_path: Path,
    visitors: Sequence[NodeVisitorImports],
) -> Tuple[Sequence[ImportEdge], Mapping[str, Sequence[str]]]:
    import_edges: Set[ImportEdge] = set()
    module_imports: Dict[str, List[str]] = {}
    for visitor in visitors:
        module = _get_import_name(base_path, visitor.path)

        for import_stmt in visitor.imports_stmt:
            for alias in import_stmt.node.names:
                if _is_builtin_or_stdlib(alias.name):
                    continue
                import_edges.add(ImportEdge(module, alias.name))
                module_imports.setdefault(module, []).append(alias.name)

        for import_modulestmt in visitor.imports_from_stmt:
            if not import_modulestmt.node.module:
                continue

            if _is_builtin_or_stdlib(import_modulestmt.node.module):
                continue

            import_edges.add(ImportEdge(module, import_modulestmt.node.module))
            module_imports.setdefault(module, []).append(import_modulestmt.node.module)

    return sorted(import_edges), module_imports


def _get_import_name(base_path: Path, path: Path) -> str:
    # TODO use importlib or inspect in order to get the right module name
    path = path.relative_to(base_path).with_suffix("")
    if path.name == "__init__":
        path = path.parent
    return str(path).replace("/", ".")


def _is_builtin_or_stdlib(name: str) -> bool:
    return (
        name in sys.builtin_module_names
    )  #  Avail in 3.10: or name in sys.stdlib_module_names


def _find_import_cycles(
    module_imports: Mapping[str, Sequence[str]]
) -> Sequence[ImportCycle]:
    detector = DetectImportCycles(module_imports)

    # TODO sort out duplicates
    cycles: Dict[Tuple[str, ...], List[ImportCycle]] = {}
    for chain in detector.detect_cycles():
        first_idx = chain.index(chain[-1])
        cycle = tuple(chain[first_idx:])
        cycles.setdefault(tuple(sorted(cycle[:-1])), []).append(
            ImportCycle(
                cycle=cycle,
                chain=chain,
            )
        )

    return [ic for ics in cycles.values() for ic in ics]


@dataclass(frozen=True)
class DetectImportCycles:
    _module_imports: Mapping[str, Sequence[str]]

    def detect_cycles(self) -> Iterable[Sequence[str]]:
        for module in self._get_main_modules():
            yield from self._detect_cycles([module], module)

    def _get_main_modules(self) -> Set[str]:
        imported_modules = set(
            imported_module
            for imported_modules in self._module_imports.values()
            for imported_module in imported_modules
        )
        if main_modules := set(
            name for name in self._module_imports if name not in imported_modules
        ):
            return main_modules
        return set(self._module_imports)

    def _detect_cycles(
        self,
        base_chain: List[str],
        module: str,
    ) -> Iterable[Sequence[str]]:
        for imported_module in self._module_imports.get(module, []):
            if imported_module in base_chain:
                yield base_chain + [imported_module]
                break

            yield from self._detect_cycles(
                base_chain + [imported_module], imported_module
            )


# .


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("path", help="Path to project folder")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = _parse_arguments(argv)

    path = Path(args.path)
    if not path.exists() or not path.is_dir():
        print("No such directory: %s" % path)
        return 1

    python_files = _get_python_files(path)

    loaded_python_files = _load_python_contents(set(python_files))

    visitors = _visit_python_contents(loaded_python_files)

    import_edges, module_imports = _get_edges_and_imports(path, visitors)

    import_cycles = _find_import_cycles(module_imports)

    graph = _make_graph(path, import_edges, import_cycles)
    graph.view()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
