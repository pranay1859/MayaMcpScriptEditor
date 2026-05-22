#!/usr/bin/env python3
"""Parse a Maya help.autodesk.com page into a structured tool spec.

Handles two URL families:

1. **Python Commands reference** —
   ``help.autodesk.com/cloudhelp/<year>/<lang>/Maya-Tech-Docs/CommandsPython/<cmd>.html``
   Output: name, summary, return type, flags table, examples.

2. **OpenMaya py_ref** —
   ``help.autodesk.com/cloudhelp/<year>/<lang>/MAYA-API-REF/py_ref/class_<class>.html``
   Output: class name, summary, list of methods with signatures and docstrings.

Usage:
    python parse_maya_doc.py <url> [--json | --stub]

  --json   Emit the parsed spec as JSON (default).
  --stub   Emit a Python tool stub (boilerplate matching tool_template.py).

Requires: `requests`, `beautifulsoup4`. If unavailable, falls back to
``urllib`` + a regex-only parser that's less robust but has no deps.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any


# ---- HTTP -------------------------------------------------------------

def _fetch(url: str) -> str:
    try:
        import requests  # type: ignore[import-not-found]

        r = requests.get(url, timeout=30, headers={"User-Agent": "maya-mcp-builder/0.1"})
        r.raise_for_status()
        return r.text
    except ImportError:
        from urllib.request import Request, urlopen

        req = Request(url, headers={"User-Agent": "maya-mcp-builder/0.1"})
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")


# ---- Data shapes ------------------------------------------------------

@dataclass
class Flag:
    long: str
    short: str
    type: str
    properties: str = ""  # "C", "Q", "E", "M" combos
    description: str = ""


@dataclass
class CommandSpec:
    kind: str = "command"
    name: str = ""
    summary: str = ""
    return_type: str = ""
    flags: list[Flag] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    source_url: str = ""


@dataclass
class MethodSpec:
    name: str
    signature: str = ""
    description: str = ""


@dataclass
class ClassSpec:
    kind: str = "class"
    name: str = ""
    summary: str = ""
    methods: list[MethodSpec] = field(default_factory=list)
    source_url: str = ""


# ---- URL routing ------------------------------------------------------

URL_RE_COMMAND = re.compile(r"/CommandsPython/([A-Za-z0-9_]+)\.html", re.IGNORECASE)
URL_RE_PY_REF = re.compile(r"/py_ref/(?:class_)?([A-Za-z0-9_]+)\.html", re.IGNORECASE)


def classify(url: str) -> str:
    if URL_RE_COMMAND.search(url):
        return "command"
    if URL_RE_PY_REF.search(url):
        return "class"
    raise ValueError(
        "Could not classify URL — expected a CommandsPython/<cmd>.html or "
        "py_ref/class_<class>.html page on help.autodesk.com."
    )


# ---- Parsing ----------------------------------------------------------

def parse_command_page(html: str, url: str) -> CommandSpec:
    """Parse a CommandsPython page. Uses BeautifulSoup if available, else regex."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    except ImportError:
        return _parse_command_regex(html, url)

    soup = BeautifulSoup(html, "html.parser")
    spec = CommandSpec(source_url=url)

    m = URL_RE_COMMAND.search(url)
    if m:
        spec.name = m.group(1)

    # The page title is usually "<name> command".
    title = soup.find("title")
    if title and not spec.name:
        spec.name = title.get_text(strip=True).split(" ", 1)[0]

    # Summary: walk paragraphs and skip the nav/TOC strip
    # ("Go to: Synopsis . Return value . Related ...") and the
    # "command (Python) MEL version ..." breadcrumb. Take the first
    # paragraph that looks like real prose about what the command does.
    summary_chunks: list[str] = []
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        if not text:
            continue
        low = text.lower()
        if low.startswith(("go to:", "synopsis", "return value", "flags")):
            continue
        if "command (python)" in low and "mel version" in low:
            continue
        if "in categories:" in low:
            continue
        # Collect up to ~3 short descriptive sentences.
        summary_chunks.append(text)
        if sum(len(c) for c in summary_chunks) > 350:
            break
    spec.summary = " ".join(summary_chunks)[:600]

    # Return type: the help pages have a "Return value" section, usually
    # a <h2> followed by a paragraph or table cell.
    for header in soup.find_all(["h2", "h3"]):
        if header.get_text(strip=True).lower() == "return value":
            nxt = header.find_next(["p", "td"])
            if nxt:
                spec.return_type = nxt.get_text(" ", strip=True)
            break

    # Flags table: Maya's CommandsPython page uses a single "Long name (short name)"
    # header column. Each flag spans 2-3 rows: first row is name/type/properties;
    # following row(s) hold the description. Identify by header text.
    flag_table = None
    for table in soup.find_all("table"):
        headers_text = " ".join(
            th.get_text(" ", strip=True).lower() for th in table.find_all("th")
        )
        if "long name" in headers_text and "argument types" in headers_text:
            flag_table = table
            break
    if flag_table:
        name_re = re.compile(r"^(\w+)\s*\(\s*(\w+)\s*\)\s*$")
        rows = flag_table.find_all("tr")
        i = 0
        while i < len(rows):
            cells = rows[i].find_all("td")
            if len(cells) < 2:
                i += 1
                continue
            name_text = cells[0].get_text(" ", strip=True)
            m = name_re.match(name_text)
            if not m:
                # Section header like "Common poly creation operation flags",
                # or empty separator row. Skip.
                i += 1
                continue
            long_name, short_name = m.group(1), m.group(2)
            type_text = cells[1].get_text(" ", strip=True).strip("[]")
            properties = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""

            # Description sits in the next row(s); pull text until we hit
            # the next flag-name row or end of table.
            desc_parts: list[str] = []
            j = i + 1
            while j < len(rows):
                next_cells = rows[j].find_all("td")
                if not next_cells:
                    j += 1
                    continue
                if next_cells[0].get_text(" ", strip=True) and name_re.match(
                    next_cells[0].get_text(" ", strip=True)
                ):
                    break
                # Description text could be in any cell; take the longest.
                texts = [c.get_text(" ", strip=True) for c in next_cells]
                texts = [t for t in texts if t]
                if texts:
                    desc_parts.append(max(texts, key=len))
                j += 1
                # Heuristic: a flag's description rarely spans more than 2
                # rows in this table format.
                if j - i > 2:
                    break

            # The description text often appears twice in the row (cell 0
            # and cell 2 mirror each other). Dedupe.
            dedup: list[str] = []
            seen: set[str] = set()
            for chunk in desc_parts:
                key = chunk.strip()
                if key and key not in seen:
                    dedup.append(chunk)
                    seen.add(key)
            spec.flags.append(
                Flag(
                    long=long_name,
                    short=short_name,
                    type=type_text,
                    properties=properties,
                    description=" ".join(dedup).strip(),
                )
            )
            i = j

    # Examples: <pre> blocks usually contain them.
    for pre in soup.find_all("pre"):
        text = pre.get_text("\n").strip()
        if "cmds." in text or "import maya" in text:
            spec.examples.append(text)

    return spec


def _parse_command_regex(html: str, url: str) -> CommandSpec:
    """Fallback parser using regex only (when bs4 isn't installed)."""
    spec = CommandSpec(source_url=url)
    m = URL_RE_COMMAND.search(url)
    if m:
        spec.name = m.group(1)
    # Best-effort summary: first <p> contents stripped of tags.
    p_match = re.search(r"<p[^>]*>(.+?)</p>", html, re.DOTALL | re.IGNORECASE)
    if p_match:
        spec.summary = re.sub(r"<[^>]+>", "", p_match.group(1)).strip()
    # Examples
    for pre in re.findall(r"<pre[^>]*>(.+?)</pre>", html, re.DOTALL | re.IGNORECASE):
        text = re.sub(r"<[^>]+>", "", pre).strip()
        if "cmds." in text:
            spec.examples.append(text)
    return spec


def parse_class_page(html: str, url: str) -> ClassSpec:
    """Parse a py_ref OpenMaya class page (Doxygen-generated)."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    except ImportError:
        # Doxygen pages are heavy; regex fallback is very lossy. Return
        # a minimal shell and let the caller decide.
        spec = ClassSpec(source_url=url)
        m = URL_RE_PY_REF.search(url)
        if m:
            spec.name = m.group(1).replace("_", "::")
        return spec

    soup = BeautifulSoup(html, "html.parser")
    spec = ClassSpec(source_url=url)

    m = URL_RE_PY_REF.search(url)
    if m:
        spec.name = m.group(1).replace("class_", "").replace("_", "::")

    # Doxygen puts the class brief in <div class="textblock"> near the top.
    block = soup.find("div", class_="textblock")
    if block:
        spec.summary = block.get_text(" ", strip=True)[:600]

    # Methods are typically in tables of class "memberdecls" with header
    # "Public Member Functions" or "Static Public Member Functions".
    for table in soup.find_all("table", class_="memberdecls"):
        for row in table.find_all("tr", class_=re.compile(r"^memitem")):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            sig_cell = cells[-1]
            sig = sig_cell.get_text(" ", strip=True)
            # The function name is usually the first <a> in that cell.
            link = sig_cell.find("a")
            name = link.get_text(strip=True) if link else sig.split("(")[0].strip()
            spec.methods.append(MethodSpec(name=name, signature=sig))

    return spec


# ---- Stub emission ----------------------------------------------------

# Map of Maya flag type names → Python type hint.
TYPE_MAP = {
    "string": "str",
    "boolean": "bool",
    "int": "int",
    "uint": "int",
    "int64": "int",
    "float": "float",
    "linear": "float",
    "angle": "float",
    "time": "float",
    "any": "object",
    "name": "str",
    "script": "str",
    "selectionItem": "str",
}


def _flag_to_python_type(flag_type: str) -> str:
    # Handle multi-arg flag types like "linear linear linear" → tuple[float, float, float]
    parts = flag_type.split()
    if len(parts) > 1 and all(p in TYPE_MAP for p in parts):
        inner = ", ".join(TYPE_MAP[p] for p in parts)
        return f"tuple[{inner}]"
    return TYPE_MAP.get(parts[0] if parts else "any", "object")


def emit_command_stub(spec: CommandSpec) -> str:
    """Emit a Python tool stub matching tool_template.py for a command."""
    fn_name = _snake(spec.name)
    args_lines: list[str] = []
    cmds_kwargs: list[str] = []

    # Only surface "creation" flags — flags whose properties include "C".
    # Skip pure query/edit-only flags in the auto-stub; the human can add
    # them later. Limit to ~10 to keep the schema readable.
    create_flags = [f for f in spec.flags if not f.properties or "C" in f.properties]
    if not create_flags:
        create_flags = spec.flags
    create_flags = create_flags[:10]

    for f in create_flags:
        py_type = _flag_to_python_type(f.type)
        default = "None"
        if py_type == "bool":
            default = "False"
        elif py_type == "str":
            default = '""'
        elif py_type in ("int", "float"):
            default = "0"
        elif py_type.startswith("tuple"):
            default = "None"
        # If Maya marks the flag as having no default, use None + Optional.
        py_type_with_optional = f"{py_type} | None" if default == "None" else py_type
        py_name = _snake(f.long)
        args_lines.append(f"    {py_name}: {py_type_with_optional} = {default},")
        # Maya kwarg name is the long flag name (camelCase) — keep it for
        # the underlying call.
        cmds_kwargs.append(f"                {f.long}={py_name},")

    args_block = "\n".join(args_lines) or "    # no flags surfaced; add by hand"
    kwargs_block = "\n".join(cmds_kwargs) or "        # add kwargs by hand"

    example_block = ""
    if spec.examples:
        # Prefer examples that actually call the command (the page may
        # contain unrelated snippets like blockTree). Fall back to the
        # shortest if none mention the command name.
        cmd_examples = [e for e in spec.examples if spec.name in e]
        chosen = min(cmd_examples or spec.examples, key=len)
        example_block = "\n    Example (from Maya docs):\n        " + chosen.replace(
            "\n", "\n        "
        )

    summary = spec.summary or f"Wraps maya.cmds.{spec.name}."

    return f'''"""Auto-generated tool stub for maya.cmds.{spec.name}.

Edit before committing:
  - Tighten the docstring and argument descriptions.
  - Remove or rename arguments you don't want surfaced to the agent.
  - Add input validation appropriate to the command.
"""
from __future__ import annotations

from maya_mcp.bridge import run_main_thread
from maya_mcp.server import mcp


@mcp.tool()
def {fn_name}(
{args_block}
) -> object:
    """{summary[:300]}

    Wraps maya.cmds.{spec.name}.
    Docs: {spec.source_url}
{example_block}
    """
    def _do():
        import maya.cmds as cmds

        cmds.undoInfo(openChunk=True, chunkName="mcp:{fn_name}")
        try:
            return cmds.{spec.name}(
{kwargs_block}
            )
        finally:
            cmds.undoInfo(closeChunk=True)

    return run_main_thread(_do)
'''


def _snake(name: str) -> str:
    """camelCase → snake_case. Handles acronyms: ``createUVs`` → ``create_uvs``.

    Algorithm: insert an underscore before any lowercase that follows
    uppercase (UV|s → UV_s), then between any lowercase/digit and a
    following uppercase (create|UVs → create_UVs), then lowercase.
    """
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    # Reserved Python names: don't collide with built-ins.
    snake = s.lower().replace("__", "_")
    if snake in {"object", "type", "from", "import", "class", "global", "lambda",
                 "return", "yield", "as", "is", "in", "and", "or", "not", "if",
                 "else", "elif", "for", "while", "with", "try", "except", "raise",
                 "def", "pass", "del", "print", "input", "id", "list", "dict",
                 "set", "tuple", "str", "int", "float", "bool", "bytes"}:
        return snake + "_"
    return snake


# ---- CLI --------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", help="Maya help.autodesk.com URL")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="Emit JSON (default)")
    out.add_argument("--stub", action="store_true", help="Emit a Python tool stub")
    args = p.parse_args(argv)

    kind = classify(args.url)
    html = _fetch(args.url)

    if kind == "command":
        spec_obj: Any = parse_command_page(html, args.url)
    else:
        spec_obj = parse_class_page(html, args.url)

    if args.stub:
        if kind != "command":
            print(
                "--stub is only supported for CommandsPython URLs right now. "
                "For OpenMaya classes, use --json and hand-write the wrapper.",
                file=sys.stderr,
            )
            return 2
        print(emit_command_stub(spec_obj))
    else:
        print(json.dumps(asdict(spec_obj), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
