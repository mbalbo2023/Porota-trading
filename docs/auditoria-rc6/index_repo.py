"""Generate a reviewable inventory from a clean, pinned Git tree."""

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml


DDL = re.compile(r"\bCREATE\s+(?:TEMP\s+|TEMPORARY\s+)?(TABLE|VIEW|INDEX|TRIGGER)\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"\x60\[]?([A-Za-z_]\w*)", re.I)
SQL_READ = re.compile(r"\b(?:FROM|JOIN)\s+[\"\x60\[]?([A-Za-z_]\w*)", re.I)
SQL_WRITE = re.compile(r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+[\"\x60\[]?([A-Za-z_]\w*)", re.I)
FILE_NAME = re.compile(r"[\w.-]+\.(?:py|sh|sql|jsonl?|db|sqlite3?|service|timer|md|ya?ml|toml|ini|txt|csv)", re.I)
DOTTED = re.compile(r"\b(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*\b")
REPORTABLE_SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|REPLACE)\b", re.I)
IO = {
    "input": {"input", "parse_args", "read_text", "read_bytes", "read", "load", "loads", "getenv", "open"},
    "output": {"write_text", "write_bytes", "write", "print", "dump", "dumps", "replace", "append"},
    "sqlite": {"connect", "execute", "executemany", "executescript", "commit", "rollback"},
    "http": {"get", "post", "put", "request", "urlopen", "http_get", "http_post"},
    "process": {"Popen", "run", "check_output", "execv", "system"},
}
HTTP_OWNERS = {"requests", "httpx", "urllib", "session", "client", "http", "request"}


def shell(root, *args):
    return subprocess.run(args, cwd=root, check=True, capture_output=True).stdout


def name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{name(node.value)}.{node.attr}"
    return ""


def annotation(node):
    if node is None:
        return None
    return ast.unparse(node)[:68]


def signature(node):
    a = node.args
    inputs = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs) if x.arg not in {"self", "cls"}]
    if a.vararg:
        inputs.append("*" + a.vararg.arg)
    if a.kwarg:
        inputs.append("**" + a.kwarg.arg)
    return {"name": node.name, "line": node.lineno, "params": inputs,
            "returns": annotation(node.returns)}


def python_facts(raw):
    try:
        tree = ast.parse(raw)
    except SyntaxError as e:
        return {"syntax_error": e.lineno, "imports": [], "functions": [], "classes": [], "io": {}, "sql": {}}
    imports = set()
    io = defaultdict(set)
    tables = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            if node.module:
                imports.add(prefix)
            imports.update(prefix + ("." if node.module else "") + a.name for a in node.names)
        elif isinstance(node, ast.Call):
            full = name(node.func)
            leaf = full.split(".")[-1]
            for kind, known in IO.items():
                if leaf in known and (kind != "http" or full.split(".")[0] in HTTP_OWNERS or leaf == "urlopen"):
                    io[kind].add(full or leaf)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and REPORTABLE_SQL.search(node.value):
            for action, found in DDL.findall(node.value):
                tables["ddl"].add((found, node.lineno, action.upper()))
            if re.search(r"\bSELECT\b", node.value, re.I):
                tables["read"].update((found, node.lineno, "READ") for found in SQL_READ.findall(node.value))
            tables["write"].update((found, node.lineno, "WRITE") for found in SQL_WRITE.findall(node.value))
    functions = [signature(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [{"name": n.name, "line": n.lineno, "methods": [
        signature(f) for f in n.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (f.name == "__init__" or not f.name.startswith("_"))]}
        for n in tree.body if isinstance(n, ast.ClassDef)]
    description = (ast.get_docstring(tree) or "").splitlines()
    return {"summary": " ".join(description[0].split())[:125] if description else None,
            "imports": sorted(imports), "functions": functions, "classes": classes,
            "main": any(isinstance(n, ast.If) and "__name__" in ast.unparse(n.test) for n in tree.body),
            "io": {k: sorted(v) for k,v in io.items()}, "sql": {k: sorted(v) for k,v in tables.items()}}


def data_kind(path):
    if path.startswith(".github/workflows/") and path.endswith((".yml",".yaml")):
        return "Workflow Actions"
    if path.endswith(".timer"):
        return "Timer systemd"
    if path.endswith(".service"):
        return "Servicio systemd"
    if path.endswith(".py"):
        return "Pruebas Python" if Path(path).name.startswith("test_") else "Módulo Python"
    if path.endswith(".sh"):
        return "Script shell"
    if path.endswith((".json",".yaml",".yml",".toml",".ini",".lock",".conf",".cfg")):
        return "Configuración/evidencia"
    if path.endswith((".md",".rst",".txt")):
        return "Documentación/manifest"
    return "Recurso o metadato"


def inventory(root, expected_tree):
    actual = shell(root, "git", "rev-parse", "HEAD^{tree}").decode().strip()
    if actual != expected_tree:
        raise ValueError(f"Árbol distinto: {actual}")
    if shell(root, "git", "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("El checkout tiene cambios rastreados")
    entries = shell(root, "git", "ls-files", "--stage", "-z").decode().split("\0")
    files = {}
    links = defaultdict(set)
    duplicate = defaultdict(list)
    stem_paths = defaultdict(set)
    dotted_paths = defaultdict(set)
    basename_paths = defaultdict(set)
    for record in filter(None, entries):
        fields, path = record.split("\t",1)
        mode, sha, stage = fields.split()
        if stage != "0":
            raise ValueError("Índice en conflicto")
        files[path] = {"path": path, "mode": mode, "blob": sha, "kind": data_kind(path)}
        basename_paths[Path(path).name].add(path)
        if path.endswith(".py"):
            stem_paths[Path(path).stem].add(path)
            dotted_paths[path[:-3].replace("/",".")].add(path)
        duplicate[sha].append(path)
    for path, row in files.items():
        raw = (root/path).read_bytes() if row["mode"] in ("100644","100755") else b""
        row["bytes"] = len(raw)
        if b"\0" in raw or not raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        row["lines"] = len(text.splitlines())
        for token in set(FILE_NAME.findall(text)):
            for target in basename_paths.get(token, ()):
                if target != path:
                    links[target].add(path)
        for token in set(DOTTED.findall(text)):
            for target in dotted_paths.get(token, ()):
                if target != path:
                    links[target].add(path)
        if path.endswith(".py"):
            row["python"] = p = python_facts(text)
            for module in p.get("imports", []):
                for linked in (module, module.split(".")[-1], module.lstrip(".").split(".")[0]):
                    for dest in stem_paths.get(linked, ()):
                        if dest != path:
                            links[dest].add(path)
        elif path.endswith((".timer",".service")):
            row["unit"] = {key: re.findall(r"(?m)^" + key + r"=(.*)$", text) for key in
                          ("Description","Unit","OnCalendar","OnBootSec","OnUnitActiveSec",
                           "AccuracySec","Persistent","ExecStart","Type")}
            row["unit"]["ExecStart"] = [sorted(set(FILE_NAME.findall(line))) for line in row["unit"]["ExecStart"]]
        elif path.endswith((".json",".yml",".yaml")):
            try:
                content = json.loads(text) if path.endswith(".json") else yaml.load(text,Loader=yaml.BaseLoader)
                if isinstance(content, dict):
                    row["keys"] = [k[:60] for k in content if isinstance(k,str) and re.fullmatch(r"[\w. -]+",k)][:12]
                    if path.startswith(".github/workflows/"):
                        row["workflow"] = {"events": list(content.get("on") or {}) if isinstance(content.get("on"),(dict,list)) else [content.get("on")],
                                           "jobs": list(content.get("jobs") or {})}
            except (ValueError, yaml.YAMLError, TypeError):
                row["parse_note"] = "Formato no interpretado"
    for path,row in files.items():
        row["reference_count"] = len(links[path])
        row["reference_sample"] = sorted(links[path])[:6]
    return {"tree": actual, "files": list(files.values()),
            "kinds": dict(Counter(x["kind"] for x in files.values())),
            "identical": [paths for sha,paths in duplicate.items() if len(paths)>1],
            "syntax_failures": [{"file":row["path"],"line":row["python"]["syntax_error"]}
                                for row in files.values() if "syntax_error" in row.get("python",{})]}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--tree", required=True)
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--markdown", type=Path)
    args = cli.parse_args()
    result = inventory(args.source, args.tree)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        lines = ["# Inventario estático de Porota RC6", "",
                 f"Árbol `{result['tree']}` · {len(result['files'])} archivos.", "",
                 "Las referencias estáticas orientan la revisión. La activación dinámica, systemd y los comandos manuales requieren evidencia adicional.", "",
                 "| Archivo | Tipo | Referencias detectadas | Entradas o contratos declarados |",
                 "| --- | --- | --- | --- |"]
        for row in result["files"]:
            p = row.get("python") or {}
            entry = [x["name"] for x in (*p.get("functions",[]),*p.get("classes",[]))][:5]
            entry = entry or row.get("keys",[])[:4]
            lines.append(f"| `{row['path']}` | {row['kind']} | {row['reference_count']} | "
                         + (", ".join("`" + x + "`" for x in entry) if entry else "—") + " |")
        args.markdown.write_text("\n".join(lines) + "\n",encoding="utf-8")
    print(json.dumps({"files":len(result["files"]),"kinds":result["kinds"],
                      "duplicates":len(result["identical"]),"syntax_failures":result["syntax_failures"]}))


if __name__ == "__main__":
    main()
