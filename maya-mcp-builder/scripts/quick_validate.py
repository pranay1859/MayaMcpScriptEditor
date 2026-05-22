#!/usr/bin/env python3
"""Validate Maya MCP tool files against required scaffold patterns.

Runs outside Maya (pure Python, no Maya dependency). Reads tool module
.py files and checks each @mcp.tool()-decorated function follows the rules
defined in references/architecture.md.

Checks performed (per decorated function):
  1. Has @mcp.tool() decorator
  2. Every parameter has a type annotation AND there is a return type
  3. Docstring has Args:, Returns:, and Example: sections
  4. No top-level 'import maya.*' or 'from maya ...' at module level
  5. Function body contains 'run_main_thread' call
  6. Mutating tools (set_/create_/delete_/etc.) contain undoInfo(openChunk

Usage:
    python quick_validate.py <file1.py> [file2.py ...]
    python quick_validate.py          # validates all tools/ files

Exit 0: all checks pass
Exit 1: any check failed
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Function name prefixes that indicate a mutating tool requiring undo chunk.
MUTATING_PREFIXES = (
    "set_",
    "create_",
    "delete_",
    "freeze_",
    "export_selection",
    "assign_",
    "load_",
    "unload_",
    "remove_",
)

# Required docstring sections (case-sensitive as per Maya scaffold convention).
REQUIRED_DOC_SECTIONS = ("Args:", "Returns:", "Example:")


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _is_mcp_tool_decorator(node: ast.expr) -> bool:
    """Return True if the decorator node represents @mcp.tool()."""
    # @mcp.tool() is ast.Call(func=ast.Attribute(value=ast.Name(id='mcp'), attr='tool'))
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tool"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "mcp"
    )


def _has_mcp_tool_decorator(func: ast.FunctionDef) -> bool:
    """Return True if the function has @mcp.tool() as one of its decorators."""
    return any(_is_mcp_tool_decorator(d) for d in func.decorator_list)


def _get_mcp_tool_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Return top-level functions decorated with @mcp.tool() (module scope only)."""
    results: list[ast.FunctionDef] = []
    for node in tree.body:  # only module-level statements, not nested scopes
        if isinstance(node, ast.FunctionDef) and _has_mcp_tool_decorator(node):
            results.append(node)
    return results


# ---------------------------------------------------------------------------
# Per-check functions
# ---------------------------------------------------------------------------

def check_type_annotations(func: ast.FunctionDef) -> list[str]:
    """Check that every parameter has an annotation and there is a return type."""
    failures: list[str] = []
    args = func.args

    all_args = (
        args.posonlyargs
        + args.args
        + args.kwonlyargs
        + ([args.vararg] if args.vararg else [])
        + ([args.kwarg] if args.kwarg else [])
    )

    missing_params = [
        arg.arg
        for arg in all_args
        if arg.annotation is None and arg.arg != "self"
    ]
    if missing_params:
        failures.append(
            f"missing type annotation on parameter(s): {', '.join(missing_params)}"
        )

    if func.returns is None:
        failures.append("missing return type annotation")

    return failures


def check_docstring_sections(func: ast.FunctionDef) -> list[str]:
    """Check that the docstring contains Args:, Returns:, and Example: sections.

    Args: is only required when the function has at least one parameter.
    """
    failures: list[str] = []
    docstring = ast.get_docstring(func) or ""

    # Determine whether the function has any user-visible parameters.
    args = func.args
    has_params = bool(
        args.posonlyargs
        or args.args
        or args.kwonlyargs
        or args.vararg
        or args.kwarg
    )

    for section in REQUIRED_DOC_SECTIONS:
        if section == "Args:" and not has_params:
            continue  # No parameters — Args: section is not required.
        if section not in docstring:
            failures.append(f"docstring missing '{section}' section")
    return failures


def check_no_top_level_maya_import(source: str) -> list[str]:
    """Check that no top-level lines import from maya (maya.*  or maya itself).

    Only fails on non-indented lines so inner _do() function imports are OK.
    Also exempts 'maya_mcp' imports (e.g. 'from maya_mcp.bridge import ...').
    """
    failures: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        # Skip indented lines — those are inside function/class bodies.
        if line.startswith(" ") or line.startswith("\t"):
            continue
        stripped = line.strip()
        if not stripped:
            continue

        # Match 'import maya' (but not 'import maya_mcp...')
        if re.match(r"^import\s+maya(\s|\.|\s*$)", stripped):
            failures.append(f"top-level 'import maya' at line {lineno}")
            continue

        # Match 'from maya ...' (but not 'from maya_mcp...')
        if re.match(r"^from\s+maya(\s*\.|\ |\s+import)", stripped):
            # Exclude maya_mcp — double-check that there's no underscore after 'maya'
            if not re.match(r"^from\s+maya_", stripped):
                failures.append(f"top-level 'from maya' import at line {lineno}")

    return failures


def check_run_main_thread(func: ast.FunctionDef, source: str) -> list[str]:
    """Check that 'run_main_thread' appears in the function body source."""
    func_source = ast.get_source_segment(source, func) or ""
    if "run_main_thread" not in func_source:
        return ["no 'run_main_thread' call found"]
    return []


def check_undo_chunk(func: ast.FunctionDef, source: str) -> list[str]:
    """For mutating tools, check that undoInfo(openChunk appears in the function body."""
    name = func.name
    if not any(name.startswith(prefix) for prefix in MUTATING_PREFIXES):
        return []  # Not a mutating tool — skip check.

    func_source = ast.get_source_segment(source, func) or ""
    if "undoInfo(openChunk" not in func_source:
        return ["mutating tool missing undoInfo(openChunk"]
    return []


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------

def validate_file(file_path: Path) -> tuple[int, int]:
    """Validate all @mcp.tool() functions in a file.

    Returns (pass_count, total_count).
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: Cannot read {file_path}: {exc}")
        return 0, 0

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        print(f"ERROR: Syntax error in {file_path}: {exc}")
        return 0, 0

    print(f"Validating: {file_path}")

    # Check 4 at file level — report once, not per function.
    file_level_failures = check_no_top_level_maya_import(source)
    if file_level_failures:
        for detail in file_level_failures:
            print(f"  FILE-LEVEL FAIL: {detail}")

    tool_functions = _get_mcp_tool_functions(tree)

    if not tool_functions:
        print("  (no @mcp.tool() functions found)")
        return 0, 0

    pass_count = 0
    total_count = len(tool_functions)

    for func in tool_functions:
        failures: list[str] = []

        # Check 2: type annotations
        failures.extend(check_type_annotations(func))

        # Check 3: docstring sections
        failures.extend(check_docstring_sections(func))

        # Check 5: run_main_thread in body
        failures.extend(check_run_main_thread(func, source))

        # Check 6: undo chunk for mutating tools
        failures.extend(check_undo_chunk(func, source))

        # Align function names in output.
        padded_name = func.name.ljust(30)
        if not failures:
            print(f"  {padded_name} ... PASS")
            pass_count += 1
        else:
            print(f"  {padded_name} ... FAIL")
            for detail in failures:
                print(f"    - {detail}")

    return pass_count, total_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_tool_files() -> list[Path]:
    """Return all .py files under assets/server_scaffold/maya_mcp/tools/."""
    base = Path(__file__).resolve().parent.parent
    tools_dir = base / "assets" / "server_scaffold" / "maya_mcp" / "tools"
    if not tools_dir.exists():
        return []
    return sorted(
        p for p in tools_dir.glob("*.py")
        if p.name != "__init__.py"
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if args:
        files = [Path(a) for a in args]
    else:
        files = _default_tool_files()
        if not files:
            print("No tool files found. Pass file paths explicitly.")
            return 1

    total_pass = 0
    total_funcs = 0

    for i, fpath in enumerate(files):
        if i > 0:
            print()
        passed, total = validate_file(fpath)
        total_pass += passed
        total_funcs += total

    print()
    file_word = "file" if len(files) == 1 else "files"
    print(
        f"Summary: {total_pass}/{total_funcs} functions passed ({len(files)} {file_word})"
    )

    return 0 if total_pass == total_funcs else 1


if __name__ == "__main__":
    sys.exit(main())
