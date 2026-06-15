#!/usr/bin/env python3
"""Local CodeGraph v1 CLI for AgentFlow."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
TOOL_VERSION = "1.0.0"
CONFIDENCE_VALUES = {"exact", "inferred", "unknown"}
DEFAULT_INDEX_PATH = Path(".agent-work/codegraph/codegraph.sqlite")
DEFAULT_CONFIG_PATH = Path(".agent-work/codegraph/config.json")
LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}
TS_JS_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx"]
CALL_EXCLUDE = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "function",
    "return",
    "typeof",
    "new",
    "import",
    "require",
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def json_dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_json(data: Any) -> str:
    return content_hash(json_dump(data).encode("utf-8"))


def repo_relative(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )


def success(repo_root: Path, index_path: Path, data: Any, *, fresh: bool = True) -> dict[str, Any]:
    return {
        "ok": True,
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "repo_root": str(repo_root),
            "index_path": index_path.as_posix(),
            "fresh": fresh,
            "indexed_at": get_last_indexed_at(index_path),
        },
        "data": data,
    }


def error_envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


class CodeGraphError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def emit(payload: dict[str, Any], exit_code: int = 0) -> int:
    print(json_dump(payload))
    return exit_code


def load_default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "languages": ["python", "typescript", "javascript"],
        "include": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"],
        "exclude": [
            ".git/**",
            ".agent-work/**",
            "**/__pycache__/**",
            "**/node_modules/**",
            "**/.venv/**",
            "**/dist/**",
            "**/build/**",
        ],
        "test_patterns": {
            "python": ["test_*.py", "test-*.py", "*_test.py", "tests/**/*.py"],
            "typescript": ["*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx"],
            "javascript": ["*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx"],
        },
        "auto_reindex": True,
        "max_context_files": 12,
        "max_context_symbols": 40,
    }


def load_config(repo_root: Path) -> tuple[dict[str, Any], Path]:
    config_path = repo_root / DEFAULT_CONFIG_PATH
    default_config = load_default_config()
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(default_config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    changed = False
    for key, value in default_config.items():
        if key not in config:
            config[key] = value
            changed = True
    for language, patterns in default_config["test_patterns"].items():
        existing = config.setdefault("test_patterns", {}).setdefault(language, [])
        for pattern in patterns:
            if pattern not in existing:
                existing.append(pattern)
                changed = True
    if changed:
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config, config_path


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def is_relevant(path: str, config: dict[str, Any]) -> bool:
    suffix = Path(path).suffix
    if suffix not in LANGUAGE_BY_SUFFIX:
        return False
    if LANGUAGE_BY_SUFFIX[suffix] not in set(config.get("languages", [])):
        return False
    include = config.get("include", [])
    exclude = config.get("exclude", [])
    return matches_any(path, include) and not matches_any(path, exclude)


@dataclass
class FileRecord:
    path: str
    language: str
    content_hash: str
    size_bytes: int
    mtime_ns: int
    tracked: bool


@dataclass
class ImportFact:
    module: str
    imported_name: str | None
    alias: str | None
    kind: str
    is_relative: bool
    start_line: int
    confidence: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SymbolFact:
    kind: str
    name: str
    qualname: str
    start_line: int
    end_line: int
    signature: str | None
    exported: bool | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallFact:
    callee_name: str
    start_line: int
    caller_qualname: str | None
    confidence: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TypeUsageFact:
    type_name: str
    start_line: int
    symbol_qualname: str | None
    confidence: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    imports: list[ImportFact] = field(default_factory=list)
    symbols: list[SymbolFact] = field(default_factory=list)
    calls: list[CallFact] = field(default_factory=list)
    type_usages: list[TypeUsageFact] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)


def scan_repo(repo_root: Path, config: dict[str, Any]) -> list[FileRecord]:
    tracked_result = run_git(repo_root, ["ls-files", "-z"])
    candidate_result = run_git(repo_root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if candidate_result.returncode == 0:
        tracked = set(tracked_result.stdout.decode("utf-8", "replace").split("\0")) if tracked_result.returncode == 0 else set()
        raw_paths = [path for path in candidate_result.stdout.decode("utf-8", "replace").split("\0") if path]
        records: list[FileRecord] = []
        for rel_path in sorted(dict.fromkeys(raw_paths)):
            if not is_relevant(rel_path, config):
                continue
            path = repo_root / rel_path
            if not path.is_file():
                continue
            data = path.read_bytes()
            stat = path.stat()
            records.append(
                FileRecord(
                    path=rel_path,
                    language=LANGUAGE_BY_SUFFIX[path.suffix],
                    content_hash=content_hash(data),
                    size_bytes=len(data),
                    mtime_ns=stat.st_mtime_ns,
                    tracked=rel_path in tracked,
                )
            )
        return records

    records = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = repo_relative(path, repo_root)
        if not is_relevant(rel_path, config):
            continue
        data = path.read_bytes()
        stat = path.stat()
        records.append(
            FileRecord(
                path=rel_path,
                language=LANGUAGE_BY_SUFFIX[path.suffix],
                content_hash=content_hash(data),
                size_bytes=len(data),
                mtime_ns=stat.st_mtime_ns,
                tracked=False,
            )
        )
    return records


def connect(index_path: Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_sql() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY,
            repo_root TEXT NOT NULL,
            head_commit TEXT,
            git_status_hash TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            error_json TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            language TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER,
            tracked INTEGER NOT NULL,
            last_indexed_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            language TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            qualname TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            signature TEXT,
            exported INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_symbols_file_kind ON symbols(file_id, kind)",
        "CREATE INDEX IF NOT EXISTS idx_symbols_qualname ON symbols(qualname)",
        "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)",
        """
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            module TEXT NOT NULL,
            imported_name TEXT,
            alias TEXT,
            kind TEXT NOT NULL,
            is_relative INTEGER NOT NULL,
            resolved_file_id INTEGER,
            confidence TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            caller_symbol_id INTEGER,
            callee_name TEXT NOT NULL,
            callee_symbol_id INTEGER,
            start_line INTEGER NOT NULL,
            confidence TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS type_usages (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            symbol_id INTEGER,
            type_name TEXT NOT NULL,
            resolved_symbol_id INTEGER,
            start_line INTEGER NOT NULL,
            confidence TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY,
            test_file_id INTEGER NOT NULL,
            source_file_id INTEGER,
            symbol_id INTEGER,
            strategy TEXT NOT NULL,
            confidence TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS file_hashes (
            file_id INTEGER PRIMARY KEY,
            content_hash TEXT NOT NULL,
            mtime_ns INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY,
            src_kind TEXT NOT NULL,
            src_id INTEGER NOT NULL,
            dst_kind TEXT NOT NULL,
            dst_id INTEGER NOT NULL,
            edge_kind TEXT NOT NULL,
            confidence TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
    ]


def ensure_schema(conn: sqlite3.Connection) -> None:
    for statement in schema_sql():
        conn.execute(statement)
    existing = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    if existing and existing["value"] != str(SCHEMA_VERSION):
        raise CodeGraphError(
            "codegraph_schema_mismatch",
            "SQLite schema version does not match CodeGraph tool version",
            {"expected": SCHEMA_VERSION, "actual": existing["value"]},
        )
    timestamp = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.execute("INSERT OR IGNORE INTO schema_meta(key, value) VALUES('created_at', ?)", (timestamp,))
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('updated_at', ?)",
        (timestamp,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('tool_version', ?)",
        (TOOL_VERSION,),
    )
    conn.commit()


def reset_current_graph(conn: sqlite3.Connection) -> None:
    for table in ["edges", "tests", "type_usages", "calls", "imports", "symbols", "file_hashes", "files"]:
        conn.execute(f"DELETE FROM {table}")


def get_last_indexed_at(index_path: Path) -> str | None:
    if not index_path.exists():
        return None
    try:
        with connect(index_path) as conn:
            row = conn.execute(
                "SELECT finished_at FROM snapshots WHERE status='success' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["finished_at"] if row else None
    except Exception:
        return None


def head_commit(repo_root: Path) -> str | None:
    result = run_git(repo_root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace").strip() or None


def git_status_hash(repo_root: Path) -> str:
    result = run_git(repo_root, ["status", "--porcelain=v1", "-z"])
    if result.returncode != 0:
        return "no-git"
    return content_hash(result.stdout)


def parse_python(source: str, rel_path: str) -> ParseResult:
    result = ParseResult()
    try:
        tree_sitter_parse("python", source.encode("utf-8"))
    except Exception as exc:
        result.gaps.append({"kind": "tree_sitter", "message": str(exc), "confidence": "unknown"})
    try:
        module = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        result.gaps.append(
            {
                "kind": "syntax",
                "message": exc.msg,
                "line": exc.lineno,
                "confidence": "unknown",
            }
        )
        return result

    source_lines = source.splitlines()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def current_symbol(self) -> str | None:
            return ".".join(self.stack) if self.stack else None

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                result.imports.append(
                    ImportFact(
                        module=alias.name,
                        imported_name=None,
                        alias=alias.asname,
                        kind="import",
                        is_relative=False,
                        start_line=node.lineno,
                        confidence="exact",
                    )
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module_name = "." * node.level + (node.module or "")
            for alias in node.names:
                result.imports.append(
                    ImportFact(
                        module=module_name,
                        imported_name=alias.name,
                        alias=alias.asname,
                        kind="from_import",
                        is_relative=node.level > 0,
                        start_line=node.lineno,
                        confidence="exact",
                    )
                )

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            qualname = ".".join([*self.stack, node.name]) if self.stack else node.name
            result.symbols.append(symbol_from_python_node(node, "class", qualname, source_lines))
            for base in node.bases:
                type_name = type_name_from_ast(base)
                if type_name:
                    result.type_usages.append(
                        TypeUsageFact(type_name, getattr(base, "lineno", node.lineno), qualname, "inferred")
                    )
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node, "function" if not self.stack else "method")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node, "async_function" if not self.stack else "async_method")

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
            qualname = ".".join([*self.stack, node.name]) if self.stack else node.name
            result.symbols.append(symbol_from_python_node(node, kind, qualname, source_lines))
            for decorator in node.decorator_list:
                name = call_name_from_ast(decorator) or type_name_from_ast(decorator)
                if name:
                    result.calls.append(CallFact(name, getattr(decorator, "lineno", node.lineno), qualname, "inferred"))
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.annotation:
                    name = type_name_from_ast(arg.annotation)
                    if name:
                        result.type_usages.append(TypeUsageFact(name, arg.annotation.lineno, qualname, "inferred"))
            if node.returns:
                name = type_name_from_ast(node.returns)
                if name:
                    result.type_usages.append(TypeUsageFact(name, node.returns.lineno, qualname, "inferred"))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            name = type_name_from_ast(node.annotation)
            if name:
                result.type_usages.append(TypeUsageFact(name, node.lineno, self.current_symbol(), "inferred"))
            self.generic_visit(node)

        def visit_arg(self, node: ast.arg) -> None:
            return None

        def visit_Call(self, node: ast.Call) -> None:
            name = call_name_from_ast(node.func)
            if name:
                result.calls.append(CallFact(name, node.lineno, self.current_symbol(), "unknown"))
            self.generic_visit(node)

    Visitor().visit(module)
    return result


def symbol_from_python_node(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    kind: str,
    qualname: str,
    source_lines: list[str],
) -> SymbolFact:
    signature = None
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            args = [arg.arg for arg in node.args.args]
            signature = f"{node.name}({', '.join(args)})"
        except Exception:
            signature = None
    exported = not node.name.startswith("_")
    return SymbolFact(
        kind=kind,
        name=node.name,
        qualname=qualname,
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
        signature=signature,
        exported=exported,
        metadata={"decorators": [type_name_from_ast(item) for item in getattr(node, "decorator_list", [])]},
    )


def call_name_from_ast(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name_from_ast(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return call_name_from_ast(node.func)
    return None


def type_name_from_ast(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = type_name_from_ast(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Subscript):
        return type_name_from_ast(node.value)
    if isinstance(node, ast.Constant):
        return str(node.value) if isinstance(node.value, str) else None
    return None


def tree_sitter_parse(language: str, data: bytes) -> bool:
    from tree_sitter import Language, Parser
    import tree_sitter_javascript
    import tree_sitter_python
    import tree_sitter_typescript

    if language == "python":
        lang = Language(tree_sitter_python.language())
    elif language == "javascript":
        lang = Language(tree_sitter_javascript.language())
    elif language == "typescript":
        lang = Language(tree_sitter_typescript.language_typescript())
    elif language == "tsx":
        lang = Language(tree_sitter_typescript.language_tsx())
    else:
        raise ValueError(f"unsupported language: {language}")
    tree = Parser(lang).parse(data)
    return not tree.root_node.has_error


def parse_ts_js(source: str, rel_path: str, language: str) -> ParseResult:
    result = ParseResult()
    parser_language = "tsx" if rel_path.endswith((".tsx", ".jsx")) else language
    try:
        clean_parse = tree_sitter_parse(parser_language, source.encode("utf-8"))
        if not clean_parse:
            result.gaps.append({"kind": "tree_sitter_error", "confidence": "unknown"})
    except Exception as exc:
        result.gaps.append({"kind": "tree_sitter", "message": str(exc), "confidence": "unknown"})

    lines = source.splitlines()
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        parse_ts_js_import(stripped, lineno, result)
        parse_ts_js_symbol(stripped, lineno, result, language)
        parse_ts_js_type_usage(stripped, lineno, result)
        for match in re.finditer(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(", stripped):
            name = match.group(1)
            base = name.split(".")[0]
            if base in CALL_EXCLUDE:
                continue
            result.calls.append(CallFact(name, lineno, None, "unknown"))
    return result


def parse_ts_js_import(line: str, lineno: int, result: ParseResult) -> None:
    from_match = re.match(r"(?:import|export)\s+(?:type\s+)?(.+?)\s+from\s+['\"]([^'\"]+)['\"]", line)
    side_effect_match = re.match(r"import\s+['\"]([^'\"]+)['\"]", line)
    require_match = re.search(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", line)
    if from_match:
        imported = from_match.group(1).strip()
        module = from_match.group(2)
        result.imports.append(
            ImportFact(
                module=module,
                imported_name=imported,
                alias=None,
                kind="es_import" if line.startswith("import") else "re_export",
                is_relative=module.startswith("."),
                start_line=lineno,
                confidence="exact",
            )
        )
    elif side_effect_match:
        module = side_effect_match.group(1)
        result.imports.append(
            ImportFact(module, None, None, "side_effect_import", module.startswith("."), lineno, "exact")
        )
    elif require_match:
        module = require_match.group(1)
        result.imports.append(
            ImportFact(module, None, None, "commonjs_require", module.startswith("."), lineno, "exact")
        )


def parse_ts_js_symbol(line: str, lineno: int, result: ParseResult, language: str) -> None:
    patterns = [
        (r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", "function"),
        (r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)\b", "class"),
        (r"(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)\b", "interface"),
        (r"(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\b", "type_alias"),
        (r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^=]*?\)?\s*=>", "function"),
        (r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b", "function"),
    ]
    for pattern, kind in patterns:
        match = re.match(pattern, line)
        if match:
            name = match.group(1)
            result.symbols.append(
                SymbolFact(
                    kind=kind,
                    name=name,
                    qualname=name,
                    start_line=lineno,
                    end_line=lineno,
                    signature=None,
                    exported=line.startswith("export"),
                    metadata={"language": language},
                )
            )
            return


def parse_ts_js_type_usage(line: str, lineno: int, result: ParseResult) -> None:
    for pattern in [
        r":\s*([A-Z][A-Za-z0-9_$]*)",
        r"\bextends\s+([A-Z][A-Za-z0-9_$]*)",
        r"\bimplements\s+([A-Z][A-Za-z0-9_$]*)",
    ]:
        for match in re.finditer(pattern, line):
            result.type_usages.append(TypeUsageFact(match.group(1), lineno, None, "inferred"))


def parse_file(repo_root: Path, record: FileRecord) -> ParseResult:
    source = (repo_root / record.path).read_text(encoding="utf-8", errors="replace")
    if record.language == "python":
        return parse_python(source, record.path)
    return parse_ts_js(source, record.path, record.language)


def metadata_json(data: dict[str, Any] | None) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def resolve_relative_import(module: str, importer_path: str, file_paths: set[str]) -> str | None:
    importer_dir = Path(importer_path).parent
    base = module
    dots = len(base) - len(base.lstrip("."))
    rest = base[dots:]
    if rest.startswith("/"):
        rest = rest[1:]
    current = importer_dir
    if dots > 0:
        for _ in range(max(dots - 1, 0)):
            current = current.parent
    if rest:
        current = current / rest.replace(".", "/")
    candidates: list[str] = []
    if Path(current).suffix:
        candidates.append(current.as_posix())
    else:
        candidates.extend((current.with_suffix(ext)).as_posix() for ext in [".py", *TS_JS_EXTENSIONS])
        candidates.extend((current / f"__init__{ext}").as_posix() for ext in [".py"])
        candidates.extend((current / f"index{ext}").as_posix() for ext in TS_JS_EXTENSIONS)
    for candidate in candidates:
        normalized = str(Path(candidate)).replace("\\", "/")
        if normalized in file_paths:
            return normalized
    return None


def resolve_package_like_import(module: str, file_paths: set[str]) -> str | None:
    module_path = module.replace(".", "/")
    candidates = [f"{module_path}{ext}" for ext in [".py", *TS_JS_EXTENSIONS]]
    candidates.extend([f"{module_path}/__init__.py", *[f"{module_path}/index{ext}" for ext in TS_JS_EXTENSIONS]])
    for candidate in candidates:
        if candidate in file_paths:
            return candidate
    return None


def insert_edge(
    conn: sqlite3.Connection,
    src_kind: str,
    src_id: int,
    dst_kind: str,
    dst_id: int,
    edge_kind: str,
    confidence: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO edges(src_kind, src_id, dst_kind, dst_id, edge_kind, confidence, metadata_json)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (src_kind, src_id, dst_kind, dst_id, edge_kind, confidence, metadata_json(metadata)),
    )


def rebuild_index(repo_root: Path) -> dict[str, Any]:
    config, _ = load_config(repo_root)
    index_path = repo_root / DEFAULT_INDEX_PATH
    records = scan_repo(repo_root, config)
    conn = connect(index_path)
    started_at = now_iso()
    snapshot_id = None
    try:
        ensure_schema(conn)
        config_digest = hash_json(config)
        snapshot_id = conn.execute(
            """
            INSERT INTO snapshots(repo_root, head_commit, git_status_hash, config_hash, started_at, status)
            VALUES(?, ?, ?, ?, ?, 'running')
            """,
            (str(repo_root), head_commit(repo_root), git_status_hash(repo_root), config_digest, started_at),
        ).lastrowid
        reset_current_graph(conn)
        indexed_at = now_iso()
        file_ids: dict[str, int] = {}
        parse_results: dict[str, ParseResult] = {}
        for record in records:
            file_id = conn.execute(
                """
                INSERT INTO files(path, language, content_hash, size_bytes, mtime_ns, tracked, last_indexed_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.path,
                    record.language,
                    record.content_hash,
                    record.size_bytes,
                    record.mtime_ns,
                    int(record.tracked),
                    indexed_at,
                ),
            ).lastrowid
            file_ids[record.path] = int(file_id)
            conn.execute(
                "INSERT INTO file_hashes(file_id, content_hash, mtime_ns) VALUES(?, ?, ?)",
                (file_id, record.content_hash, record.mtime_ns),
            )
            parse_results[record.path] = parse_file(repo_root, record)

        symbol_ids_by_qualname: dict[tuple[str, str], int] = {}
        symbol_ids_by_name: dict[str, list[int]] = {}
        for record in records:
            file_id = file_ids[record.path]
            for symbol in parse_results[record.path].symbols:
                symbol_id = conn.execute(
                    """
                    INSERT INTO symbols(
                        file_id, language, kind, name, qualname, start_line, end_line,
                        signature, exported, metadata_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        record.language,
                        symbol.kind,
                        symbol.name,
                        symbol.qualname,
                        symbol.start_line,
                        symbol.end_line,
                        symbol.signature,
                        None if symbol.exported is None else int(symbol.exported),
                        metadata_json(symbol.metadata),
                    ),
                ).lastrowid
                symbol_id = int(symbol_id)
                symbol_ids_by_qualname[(record.path, symbol.qualname)] = symbol_id
                symbol_ids_by_name.setdefault(symbol.name, []).append(symbol_id)
                insert_edge(conn, "file", file_id, "symbol", symbol_id, "DEFINES", "exact")

        file_paths = set(file_ids)
        for record in records:
            file_id = file_ids[record.path]
            parsed = parse_results[record.path]
            for fact in parsed.imports:
                resolved_path = None
                if fact.is_relative:
                    resolved_path = resolve_relative_import(fact.module, record.path, file_paths)
                else:
                    resolved_path = resolve_package_like_import(fact.module, file_paths)
                resolved_file_id = file_ids.get(resolved_path) if resolved_path else None
                confidence = fact.confidence if resolved_file_id else "unknown"
                import_id = conn.execute(
                    """
                    INSERT INTO imports(
                        file_id, module, imported_name, alias, kind, is_relative,
                        resolved_file_id, confidence, start_line, metadata_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        fact.module,
                        fact.imported_name,
                        fact.alias,
                        fact.kind,
                        int(fact.is_relative),
                        resolved_file_id,
                        confidence,
                        fact.start_line,
                        metadata_json({**fact.metadata, "resolved_path": resolved_path}),
                    ),
                ).lastrowid
                if resolved_file_id:
                    insert_edge(conn, "file", file_id, "file", resolved_file_id, "IMPORTS", confidence, {"import_id": import_id})

            for fact in parsed.calls:
                caller_id = symbol_ids_by_qualname.get((record.path, fact.caller_qualname)) if fact.caller_qualname else None
                callee_short = fact.callee_name.split(".")[-1]
                candidates = symbol_ids_by_name.get(callee_short, [])
                callee_id = candidates[0] if len(candidates) == 1 else None
                confidence = "inferred" if callee_id else fact.confidence
                call_id = conn.execute(
                    """
                    INSERT INTO calls(file_id, caller_symbol_id, callee_name, callee_symbol_id, start_line, confidence, metadata_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        caller_id,
                        fact.callee_name,
                        callee_id,
                        fact.start_line,
                        confidence,
                        metadata_json(fact.metadata),
                    ),
                ).lastrowid
                if caller_id and callee_id:
                    insert_edge(conn, "symbol", caller_id, "symbol", callee_id, "CALLS", confidence, {"call_id": call_id})

            for fact in parsed.type_usages:
                symbol_id = symbol_ids_by_qualname.get((record.path, fact.symbol_qualname)) if fact.symbol_qualname else None
                candidates = symbol_ids_by_name.get(fact.type_name.split(".")[-1], [])
                resolved_symbol_id = candidates[0] if len(candidates) == 1 else None
                confidence = "inferred" if resolved_symbol_id else fact.confidence
                usage_id = conn.execute(
                    """
                    INSERT INTO type_usages(file_id, symbol_id, type_name, resolved_symbol_id, start_line, confidence, metadata_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        symbol_id,
                        fact.type_name,
                        resolved_symbol_id,
                        fact.start_line,
                        confidence,
                        metadata_json(fact.metadata),
                    ),
                ).lastrowid
                if symbol_id and resolved_symbol_id:
                    insert_edge(
                        conn,
                        "symbol",
                        symbol_id,
                        "symbol",
                        resolved_symbol_id,
                        "USES_TYPE",
                        confidence,
                        {"type_usage_id": usage_id},
                    )

        link_tests(conn, records, file_ids)
        conn.execute(
            "UPDATE snapshots SET finished_at=?, status='success', error_json=NULL WHERE id=?",
            (now_iso(), snapshot_id),
        )
        conn.commit()
        return {
            "files": len(records),
            "symbols": count_table(conn, "symbols"),
            "edges": count_table(conn, "edges"),
            "snapshot_id": snapshot_id,
        }
    except Exception as exc:
        payload = error_envelope(
            "codegraph_index_failed",
            "Index rebuild failed",
            {"exception": type(exc).__name__, "message": str(exc)},
        )
        if snapshot_id is not None:
            conn.execute(
                "UPDATE snapshots SET finished_at=?, status='error', error_json=? WHERE id=?",
                (now_iso(), json_dump(payload), snapshot_id),
            )
            conn.commit()
        if isinstance(exc, CodeGraphError):
            raise
        raise CodeGraphError("codegraph_index_failed", "Index rebuild failed", payload["error"]["details"]) from exc
    finally:
        conn.close()


def count_table(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])


def is_test_file(path: str, language: str, config: dict[str, Any] | None = None) -> bool:
    name = Path(path).name
    if language == "python":
        return name.startswith(("test_", "test-")) or name.endswith("_test.py") or path.startswith("tests/")
    if language == "typescript":
        return any(name.endswith(suffix) for suffix in [".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx"])
    if language == "javascript":
        return any(name.endswith(suffix) for suffix in [".test.js", ".spec.js", ".test.jsx", ".spec.jsx"])
    return False


def source_candidates_for_test(path: str, language: str, file_paths: set[str]) -> list[str]:
    test_path = Path(path)
    name = test_path.name
    candidates: list[str] = []
    if language == "python":
        stem = test_path.stem
        if stem.startswith("test_"):
            stem = stem.removeprefix("test_")
        elif stem.startswith("test-"):
            stem = stem.removeprefix("test-")
        elif stem.endswith("_test"):
            stem = stem.removesuffix("_test")
        for file_path in file_paths:
            if Path(file_path).name == f"{stem}.py" and file_path != path:
                candidates.append(file_path)
    else:
        stem = test_path.stem
        stem = re.sub(r"\.(test|spec)$", "", stem)
        for file_path in file_paths:
            if file_path == path:
                continue
            candidate = Path(file_path)
            if candidate.stem == stem and candidate.suffix in TS_JS_EXTENSIONS:
                candidates.append(file_path)
    return sorted(dict.fromkeys(candidates))


def link_tests(conn: sqlite3.Connection, records: list[FileRecord], file_ids: dict[str, int]) -> None:
    file_paths = set(file_ids)
    import_rows = conn.execute("SELECT file_id, resolved_file_id FROM imports WHERE resolved_file_id IS NOT NULL").fetchall()
    imports_by_file: dict[int, set[int]] = {}
    for row in import_rows:
        imports_by_file.setdefault(int(row["file_id"]), set()).add(int(row["resolved_file_id"]))

    for record in records:
        if not is_test_file(record.path, record.language):
            continue
        test_file_id = file_ids[record.path]
        named_source_ids = {file_ids[path] for path in source_candidates_for_test(record.path, record.language, file_paths)}
        imported_source_ids = imports_by_file.get(test_file_id, set())
        source_ids = named_source_ids | imported_source_ids
        for source_id in sorted(source_ids):
            if source_id == test_file_id:
                continue
            if source_id in named_source_ids and source_id in imported_source_ids:
                strategy = "naming+import"
                confidence = "exact"
            elif source_id in imported_source_ids:
                strategy = "import"
                confidence = "exact"
            else:
                strategy = "naming"
                confidence = "inferred"
            test_id = conn.execute(
                """
                INSERT INTO tests(test_file_id, source_file_id, symbol_id, strategy, confidence, metadata_json)
                VALUES(?, ?, NULL, ?, ?, ?)
                """,
                (test_file_id, source_id, strategy, confidence, "{}"),
            ).lastrowid
            insert_edge(conn, "file", source_id, "file", test_file_id, "TESTED_BY", confidence, {"test_id": test_id})


def current_state_digest(repo_root: Path, config: dict[str, Any]) -> dict[str, str]:
    records = scan_repo(repo_root, config)
    return {record.path: record.content_hash for record in records}


def freshness(repo_root: Path) -> dict[str, Any]:
    config, _ = load_config(repo_root)
    index_path = repo_root / DEFAULT_INDEX_PATH
    if not index_path.exists():
        return {"fresh": False, "reason": "missing_database"}
    try:
        with connect(index_path) as conn:
            ensure_schema(conn)
            row = conn.execute(
                "SELECT config_hash, status, error_json FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"fresh": False, "reason": "missing_snapshot"}
            if row["status"] != "success":
                return {"fresh": False, "reason": "last_index_failed", "last_error": row["error_json"]}
            if row["config_hash"] != hash_json(config):
                return {"fresh": False, "reason": "config_changed"}
            indexed = {row["path"]: row["content_hash"] for row in conn.execute("SELECT path, content_hash FROM files")}
    except CodeGraphError as exc:
        return {"fresh": False, "reason": exc.code, "details": exc.details}
    except Exception as exc:
        return {"fresh": False, "reason": "database_error", "details": str(exc)}
    current = current_state_digest(repo_root, config)
    if indexed != current:
        return {
            "fresh": False,
            "reason": "files_changed",
            "added": sorted(set(current) - set(indexed)),
            "removed": sorted(set(indexed) - set(current)),
            "changed": sorted(path for path in set(indexed) & set(current) if indexed[path] != current[path]),
        }
    return {"fresh": True, "reason": "fresh"}


def ensure_fresh(repo_root: Path) -> dict[str, Any]:
    state = freshness(repo_root)
    if state["fresh"]:
        return state
    config, _ = load_config(repo_root)
    if not config.get("auto_reindex", True):
        raise CodeGraphError("codegraph_stale", "CodeGraph index is stale and auto_reindex is disabled", state)
    rebuild_index(repo_root)
    new_state = freshness(repo_root)
    if not new_state["fresh"]:
        raise CodeGraphError("codegraph_stale_after_reindex", "CodeGraph index is still stale after reindex", new_state)
    return {"fresh": True, "reason": "auto_reindexed", "previous": state}


def open_fresh(repo_root: Path) -> sqlite3.Connection:
    ensure_fresh(repo_root)
    conn = connect(repo_root / DEFAULT_INDEX_PATH)
    ensure_schema(conn)
    return conn


def file_row(conn: sqlite3.Connection, target: str) -> sqlite3.Row | None:
    normalized = target.strip()
    row = conn.execute("SELECT * FROM files WHERE path=?", (normalized,)).fetchone()
    if row:
        return row
    matches = conn.execute("SELECT * FROM files WHERE path LIKE ? ORDER BY length(path) LIMIT 2", (f"%{normalized}%",)).fetchall()
    return matches[0] if len(matches) == 1 else None


def symbol_rows(conn: sqlite3.Connection, target: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT symbols.*, files.path AS file_path FROM symbols JOIN files ON files.id=symbols.file_id WHERE symbols.name=? OR symbols.qualname=?",
        (target, target),
    ).fetchall()


def file_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "path": row["path"],
        "language": row["language"],
        "content_hash": row["content_hash"],
        "tracked": bool(row["tracked"]),
        "confidence": "exact",
    }


def symbol_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "qualname": row["qualname"],
        "kind": row["kind"],
        "path": row["file_path"] if "file_path" in row.keys() else None,
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "confidence": "exact",
    }


def tests_for_source(conn: sqlite3.Connection, source_file_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT tests.strategy, tests.confidence, files.path AS test_path
        FROM tests
        JOIN files ON files.id = tests.test_file_id
        WHERE tests.source_file_id=?
        ORDER BY files.path
        """,
        (source_file_id,),
    ).fetchall()
    return [
        {"path": row["test_path"], "strategy": row["strategy"], "confidence": row["confidence"]}
        for row in rows
    ]


def query_impact(repo_root: Path, target: str) -> dict[str, Any]:
    with open_fresh(repo_root) as conn:
        file = file_row(conn, target)
        symbols = symbol_rows(conn, target)
        gaps: list[dict[str, Any]] = []
        affected_files: dict[str, dict[str, Any]] = {}
        affected_symbols: list[dict[str, Any]] = []
        tests: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        if file:
            affected_files[file["path"]] = file_payload(file)
            for row in conn.execute(
                """
                SELECT DISTINCT files.* FROM imports
                JOIN files ON files.id = imports.file_id
                WHERE imports.resolved_file_id=?
                UNION
                SELECT DISTINCT files.* FROM imports
                JOIN files ON files.id = imports.resolved_file_id
                WHERE imports.file_id=? AND imports.resolved_file_id IS NOT NULL
                """,
                (file["id"], file["id"]),
            ):
                affected_files[row["path"]] = file_payload(row)
            for row in conn.execute(
                "SELECT symbols.*, files.path AS file_path FROM symbols JOIN files ON files.id=symbols.file_id WHERE file_id=?",
                (file["id"],),
            ):
                affected_symbols.append(symbol_payload(row))
            tests.extend(tests_for_source(conn, int(file["id"])))
            edges.extend(edge_payloads_for_file(conn, int(file["id"])))

        for symbol in symbols:
            affected_symbols.append(symbol_payload(symbol))
            source = conn.execute("SELECT * FROM files WHERE id=?", (symbol["file_id"],)).fetchone()
            if source:
                affected_files[source["path"]] = file_payload(source)
                tests.extend(tests_for_source(conn, int(source["id"])))

        if not file and not symbols:
            gaps.append({"kind": "target_not_found", "target": target, "confidence": "unknown"})

        return {
            "target": target,
            "files": list(affected_files.values()),
            "symbols": unique_dicts(affected_symbols, ["path", "qualname"]),
            "tests": unique_dicts(tests, ["path", "strategy"]),
            "edges": edges,
            "gaps": gaps,
        }


def edge_payloads_for_file(conn: sqlite3.Connection, file_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT edge_kind, confidence, src_kind, src_id, dst_kind, dst_id
        FROM edges
        WHERE (src_kind='file' AND src_id=?) OR (dst_kind='file' AND dst_id=?)
        ORDER BY edge_kind, dst_id
        """,
        (file_id, file_id),
    ).fetchall()
    return [dict(row) for row in rows]


def unique_dicts(items: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for item in items:
        marker = tuple(item.get(key) for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        output.append(item)
    return output


def query_tests(repo_root: Path, target: str) -> dict[str, Any]:
    with open_fresh(repo_root) as conn:
        file = file_row(conn, target)
        symbols = symbol_rows(conn, target)
        linked: list[dict[str, Any]] = []
        gaps = []
        if file:
            linked.extend(tests_for_source(conn, int(file["id"])))
        for symbol in symbols:
            linked.extend(tests_for_source(conn, int(symbol["file_id"])))
        if not file and not symbols:
            gaps.append({"kind": "target_not_found", "target": target, "confidence": "unknown"})
        return {"target": target, "tests": unique_dicts(linked, ["path", "strategy"]), "gaps": gaps}


def query_context(repo_root: Path, target: str | None, task: str | None) -> dict[str, Any]:
    config, _ = load_config(repo_root)
    if target:
        impact = query_impact(repo_root, target)
    else:
        with open_fresh(repo_root) as conn:
            terms = extract_terms(task or "")
            paths = []
            for term in terms:
                paths.extend(
                    row["path"]
                    for row in conn.execute(
                        "SELECT path FROM files WHERE path LIKE ? ORDER BY path LIMIT 4",
                        (f"%{term}%",),
                    ).fetchall()
                )
            impact = {
                "target": task,
                "files": [{"path": path, "confidence": "inferred"} for path in sorted(dict.fromkeys(paths))],
                "symbols": [],
                "tests": [],
                "edges": [],
                "gaps": [] if paths else [{"kind": "no_task_match", "confidence": "unknown"}],
            }
    files = impact["files"][: int(config.get("max_context_files", 12))]
    symbols = impact["symbols"][: int(config.get("max_context_symbols", 40))]
    return {
        "target": target,
        "task": task,
        "files_to_read": files,
        "symbols_to_inspect": symbols,
        "tests_to_run": impact["tests"],
        "risks": [
            {
                "kind": "graph_support_only",
                "message": "CodeGraph supports gates but is not sole truth for release/security conclusions.",
                "confidence": "exact",
            }
        ],
        "gaps": impact["gaps"],
        "limits": {
            "max_context_files": config.get("max_context_files", 12),
            "max_context_symbols": config.get("max_context_symbols", 40),
        },
        "suggested_commands": ["python3 scripts/test-codegraph.py"],
    }


def extract_terms(text: str) -> list[str]:
    return [term for term in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}", text) if term.lower() not in {"and", "the", "for"}]


def load_changed_paths(value: str | None, explicit_paths: list[str]) -> list[str]:
    paths = list(explicit_paths)
    if value:
        path = Path(value)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                paths.extend(str(item) for item in payload)
            elif isinstance(payload, dict):
                paths.extend(str(item) for item in payload.get("changed_paths", []))
        else:
            paths.append(value)
    return sorted(dict.fromkeys(paths))


def validate_boundary_path(path: str) -> bool:
    return bool(path) and not path.startswith("/") and "\\" not in path and ".." not in Path(path).parts


def query_boundary(
    repo_root: Path,
    changed_paths_file: str | None,
    explicit_paths: list[str],
    allowed: list[str],
    forbidden: list[str],
) -> dict[str, Any]:
    changed_paths = load_changed_paths(changed_paths_file, explicit_paths)
    with open_fresh(repo_root) as conn:
        violations = []
        affected = []
        for path in changed_paths:
            valid = validate_boundary_path(path)
            allowed_match = any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed) if allowed else False
            forbidden_match = any(fnmatch.fnmatchcase(path, pattern) for pattern in forbidden)
            if not valid or forbidden_match or not allowed_match:
                violations.append(
                    {
                        "path": path,
                        "valid_path": valid,
                        "allowed": allowed_match,
                        "forbidden": forbidden_match,
                        "confidence": "exact",
                    }
                )
            file = file_row(conn, path)
            if file:
                affected.append({"path": file["path"], "language": file["language"], "confidence": "exact"})
                affected.extend(query_impact(repo_root, file["path"]).get("files", []))
            else:
                affected.append({"path": path, "language": None, "confidence": "unknown"})
        status = "pass" if not violations else "fail"
        if not changed_paths:
            status = "unknown"
        return {
            "status": status,
            "changed_paths": changed_paths,
            "allowed": allowed,
            "forbidden": forbidden,
            "violations": violations,
            "affected_surface": unique_dicts(affected, ["path"]),
            "evidence": {
                "version": 1,
                "changed_paths": changed_paths,
                "status": status,
                "confidence": "exact" if changed_paths else "unknown",
            },
        }


def query_deps(repo_root: Path, active_task: str | None, new_task: str | None) -> dict[str, Any]:
    with open_fresh(repo_root) as conn:
        active_text = read_task_argument(active_task)
        new_text = read_task_argument(new_task)
        active_surface = task_surface(conn, active_text)
        new_surface = task_surface(conn, new_text)
        shared_files = sorted(set(active_surface["files"]) & set(new_surface["files"]))
        shared_symbols = sorted(set(active_surface["symbols"]) & set(new_surface["symbols"]))
        shared_tests = sorted(set(active_surface["tests"]) & set(new_surface["tests"]))
        missing_context = []
        if not active_surface["files"] and not active_surface["symbols"]:
            missing_context.append("active_task")
        if not new_surface["files"] and not new_surface["symbols"]:
            missing_context.append("new_task")
        if shared_files or shared_symbols or shared_tests:
            classification = "dependent"
        elif missing_context:
            classification = "uncertain"
        else:
            classification = "clear"
        return {
            "classification": classification,
            "shared_files": shared_files,
            "shared_symbols": shared_symbols,
            "shared_tests": shared_tests,
            "missing_context": missing_context,
            "reasons": deps_reasons(classification, shared_files, shared_symbols, missing_context),
        }


def read_task_argument(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return value


def task_surface(conn: sqlite3.Connection, text: str) -> dict[str, list[str]]:
    files: set[str] = set()
    symbols: set[str] = set()
    tests: set[str] = set()
    candidates = set(re.findall(r"[\w./-]+\.(?:py|ts|tsx|js|jsx)", text))
    candidates.update(extract_terms(text))
    for candidate in candidates:
        file = file_row(conn, candidate)
        if file:
            files.add(file["path"])
            tests.update(item["path"] for item in tests_for_source(conn, int(file["id"])))
        for symbol in symbol_rows(conn, candidate):
            symbols.add(symbol["qualname"])
            source = conn.execute("SELECT path FROM files WHERE id=?", (symbol["file_id"],)).fetchone()
            if source:
                files.add(source["path"])
                tests.update(item["path"] for item in tests_for_source(conn, int(symbol["file_id"])))
    return {"files": sorted(files), "symbols": sorted(symbols), "tests": sorted(tests)}


def deps_reasons(
    classification: str,
    shared_files: list[str],
    shared_symbols: list[str],
    missing_context: list[str],
) -> list[dict[str, Any]]:
    if classification == "dependent":
        return [
            {
                "kind": "shared_surface",
                "files": shared_files,
                "symbols": shared_symbols,
                "confidence": "exact" if shared_files or shared_symbols else "inferred",
            }
        ]
    if classification == "uncertain":
        return [{"kind": "missing_context", "surface": missing_context, "confidence": "unknown"}]
    return [{"kind": "no_shared_surface", "confidence": "inferred"}]


def query_status(repo_root: Path) -> dict[str, Any]:
    ensure_fresh(repo_root)
    index_path = repo_root / DEFAULT_INDEX_PATH
    with connect(index_path) as conn:
        ensure_schema(conn)
        snapshot = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        last_error = conn.execute("SELECT error_json FROM snapshots WHERE status='error' ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "fresh": True,
            "files": count_table(conn, "files"),
            "symbols": count_table(conn, "symbols"),
            "edges": count_table(conn, "edges"),
            "imports": count_table(conn, "imports"),
            "calls": count_table(conn, "calls"),
            "type_usages": count_table(conn, "type_usages"),
            "tests": count_table(conn, "tests"),
            "last_indexed_at": snapshot["finished_at"] if snapshot else None,
            "last_error": json.loads(last_error["error_json"]) if last_error and last_error["error_json"] else None,
        }


def doctor(repo_root: Path) -> tuple[bool, dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append({"name": "python_version", "ok": sys.version_info >= (3, 10), "value": sys.version.split()[0]})
    try:
        conn = sqlite3.connect(":memory:")
        value = conn.execute("SELECT json('{\"ok\":true}')").fetchone()[0]
        checks.append({"name": "sqlite_json", "ok": value == '{"ok":true}', "value": sqlite3.sqlite_version})
        conn.close()
    except Exception as exc:
        checks.append({"name": "sqlite_json", "ok": False, "error": str(exc)})
    for name, snippet in [
        ("python_parser", b"def f(x):\n    return x\n"),
        ("javascript_parser", b"function f(x) { return x }\n"),
        ("typescript_parser", b"type T = string; function f(x: T) { return x }\n"),
        ("tsx_parser", b"export const X = () => <div />\n"),
    ]:
        try:
            language = name.removesuffix("_parser")
            ok = tree_sitter_parse(language, snippet)
            checks.append({"name": name, "ok": bool(ok)})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "error": str(exc)})
    try:
        config, config_path = load_config(repo_root)
        checks.append({"name": "config", "ok": config.get("schema_version") == SCHEMA_VERSION, "path": config_path.as_posix()})
    except Exception as exc:
        checks.append({"name": "config", "ok": False, "error": str(exc)})
    git_result = run_git(repo_root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    checks.append({"name": "git_discovery", "ok": git_result.returncode == 0, "returncode": git_result.returncode})
    try:
        with connect(repo_root / DEFAULT_INDEX_PATH) as conn:
            ensure_schema(conn)
        checks.append({"name": "schema", "ok": True, "path": DEFAULT_INDEX_PATH.as_posix()})
    except Exception as exc:
        checks.append({"name": "schema", "ok": False, "error": str(exc)})
    ok = all(check.get("ok") for check in checks)
    return ok, {"checks": checks}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index")
    sub.add_parser("status")
    impact = sub.add_parser("impact")
    impact.add_argument("--target", required=True)
    tests = sub.add_parser("tests")
    tests.add_argument("--target", required=True)
    context = sub.add_parser("context")
    context.add_argument("--target")
    context.add_argument("--task")
    boundary = sub.add_parser("boundary")
    boundary.add_argument("--changed-paths")
    boundary.add_argument("--path", action="append", default=[])
    boundary.add_argument("--allowed", action="append", default=[])
    boundary.add_argument("--forbidden", action="append", default=[])
    deps = sub.add_parser("deps")
    deps.add_argument("--active-task")
    deps.add_argument("--new-task")
    sub.add_parser("doctor")
    return parser


def handle(args: argparse.Namespace, repo_root: Path) -> tuple[int, dict[str, Any]]:
    index_path = repo_root / DEFAULT_INDEX_PATH
    if args.command == "doctor":
        ok, data = doctor(repo_root)
        return (0 if ok else 1), success(repo_root, index_path, data, fresh=ok)
    if args.command == "index":
        return 0, success(repo_root, index_path, rebuild_index(repo_root))
    if args.command == "status":
        return 0, success(repo_root, index_path, query_status(repo_root))
    if args.command == "impact":
        return 0, success(repo_root, index_path, query_impact(repo_root, args.target))
    if args.command == "tests":
        return 0, success(repo_root, index_path, query_tests(repo_root, args.target))
    if args.command == "context":
        return 0, success(repo_root, index_path, query_context(repo_root, args.target, args.task))
    if args.command == "boundary":
        if not args.allowed:
            raise CodeGraphError("codegraph_boundary_missing_allowed", "boundary requires at least one --allowed pattern")
        return 0, success(
            repo_root,
            index_path,
            query_boundary(repo_root, args.changed_paths, args.path, args.allowed, args.forbidden),
        )
    if args.command == "deps":
        return 0, success(repo_root, index_path, query_deps(repo_root, args.active_task, args.new_task))
    raise CodeGraphError("codegraph_unknown_command", "Unknown command", {"command": args.command})


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        repo_root = find_repo_root(Path.cwd())
        exit_code, payload = handle(args, repo_root)
        return emit(payload, exit_code)
    except CodeGraphError as exc:
        repo_root = find_repo_root(Path.cwd())
        return emit(error_envelope(exc.code, exc.message, exc.details), 1)
    except Exception as exc:
        return emit(
            error_envelope(
                "codegraph_unhandled_error",
                "Unhandled CodeGraph error",
                {"exception": type(exc).__name__, "message": str(exc)},
            ),
            1,
        )


if __name__ == "__main__":
    raise SystemExit(main())
