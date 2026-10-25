"""Which backend modules and third-party packages a model needs (SP-760).

    .venv/bin/python docs/research/model-bench/import_closure.py [backend.engine.workout_review ...] [--json out.json]

Static: walks the AST of every backend module reachable from the roots and
follows every `import` / `from ... import` — both the module-level ones (needed
the moment the module is imported) and the ones inside functions (needed only
when that code path runs). Prints two closures:

* eager — only module-level imports (what `import backend.engine.workout_review`
  pulls in by itself);
* full  — module-level + function-level imports (an upper bound of what a call
  could reach).

For each closure: the number of backend modules, their source bytes, and every
non-backend top-level module (stdlib or third-party) with the backend modules
that import it. Nothing is executed.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STDLIB = set(sys.stdlib_module_names)


def module_path(mod: str) -> Path | None:
    p = ROOT.joinpath(*mod.split("."))
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    return None


def resolve(mod: str) -> str | None:
    """The longest prefix of `mod` that is a backend module file."""
    parts = mod.split(".")
    while parts:
        m = ".".join(parts)
        if module_path(m):
            return m
        parts.pop()
    return None


def imports_of(mod: str) -> list[tuple[str, bool]]:
    """[(imported module, eager)] — eager when the import is not inside a def / lambda."""
    path = module_path(mod)
    tree = ast.parse(path.read_text("utf-8"), str(path))
    is_pkg = path.name == "__init__.py"
    out: list[tuple[str, bool]] = []

    def visit(node, in_func: bool):
        for ch in ast.iter_child_nodes(node):
            f = in_func or isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(ch, ast.Import):
                for a in ch.names:
                    out.append((a.name, not in_func))
            elif isinstance(ch, ast.ImportFrom):
                if ch.level:
                    base = mod.split(".") if is_pkg else mod.split(".")[:-1]
                    base = base[: len(base) - (ch.level - 1)]
                    target = ".".join(base + ([ch.module] if ch.module else []))
                else:
                    target = ch.module or ""
                out.append((target, not in_func))
                for a in ch.names:            # `from backend.engine import hints` imports a module
                    out.append((f"{target}.{a.name}", not in_func))
            visit(ch, f)
    visit(tree, False)
    return out


def closure(roots: list[str], eager_only: bool) -> tuple[set[str], dict[str, set[str]]]:
    seen: set[str] = set()
    external: dict[str, set[str]] = {}
    todo = list(roots)
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        # a package's parents run first on import
        parts = m.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if module_path(parent) and parent not in seen:
                todo.append(parent)
        for name, eager in imports_of(m):
            if eager_only and not eager:
                continue
            if name.split(".")[0] == "backend":
                r = resolve(name)
                if r and r not in seen:
                    todo.append(r)
            elif name and not name.startswith("."):
                external.setdefault(name.split(".")[0], set()).add(m)
    return seen, external


def report(title: str, mods: set[str], ext: dict[str, set[str]]) -> dict:
    size = sum(module_path(m).stat().st_size for m in mods)
    third = {k: v for k, v in ext.items() if k not in STDLIB and k != "__future__"}
    std = {k: v for k, v in ext.items() if k in STDLIB}
    print(f"== {title}: {len(mods)} backend modules, {size / 1024:.0f} KB of source")
    print("   third-party:")
    for k in sorted(third):
        users = sorted(third[k])
        print(f"     {k:<22} {len(users):>3} modules  e.g. {', '.join(users[:3])}")
    print("   stdlib:", ", ".join(sorted(std)))
    return {"modules": sorted(mods), "source_bytes": size,
            "third_party": {k: sorted(v) for k, v in third.items()},
            "stdlib": {k: sorted(v) for k, v in std.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", default=["backend.engine.workout_review"])
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    out = {}
    for eager in (True, False):
        mods, ext = closure(a.roots, eager)
        out["eager" if eager else "full"] = report("eager (module-level imports)" if eager else
                                                   "full (module-level + function-level imports)", mods, ext)
    total = sum(p.stat().st_size for p in (ROOT / "backend").rglob("*.py") if "tests" not in p.parts)
    print(f"(all of backend/ without tests: {total / 1024:.0f} KB)")
    if a.json:
        a.json.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
