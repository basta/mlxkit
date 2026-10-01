"""Split live script code into MATLAB "regions" (executable statements).

output.xml ties every output to a region: a run of document lines holding one
complete statement (a multi-line `for` loop is one region). Region line
numbers are 0-based document lines as produced by Document.lines().
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK_OPENERS = {"for", "parfor", "while", "if", "switch", "try", "function", "spmd",
                 "classdef", "methods", "properties", "events", "enumeration", "arguments"}
_IDENT = re.compile(r"[A-Za-z_]\w*")
_NUMBER = re.compile(r"(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?[ij]?")


@dataclass
class Region:
    number: int
    start_line: int
    end_line: int
    section_break: bool = False  # first region of a section
    end_of_section: bool = False  # last region of a section
    is_function: bool = False  # part of a local function definition (never executed)


@dataclass
class _State:
    depth: int = 0  # () [] {} nesting
    blocks: list[str] = None
    continued: bool = False
    in_block_comment: bool = False

    def __post_init__(self):
        self.blocks = []

    @property
    def open(self) -> bool:
        return self.depth > 0 or bool(self.blocks) or self.continued or self.in_block_comment


def scan_line(line: str, st: _State) -> bool:
    """Advance the lexer over one line. Returns True if the line holds any code."""
    stripped = line.strip()
    if st.in_block_comment:
        if stripped == "%}":
            st.in_block_comment = False
        return False
    if stripped == "%{":
        st.in_block_comment = True
        return False
    st.continued = False
    has_code = False
    stmt_start = st.depth == 0
    prev = ""  # previous significant token kind: "value" (ident, number, ), ], }, ', .') or "op"
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t":
            if st.depth > 0 and prev == "value":
                # Inside brackets whitespace separates elements: [a 'str'] starts a string.
                j = i
                while j < n and line[j] in " \t":
                    j += 1
                if j < n and line[j] == "'" :
                    prev = "op"
            i += 1
            continue
        if c == "%":
            break
        if line.startswith("...", i):
            st.continued = True
            break
        has_code = True
        if c == "'" and prev != "value":
            j = i + 1
            while j < n:
                if line[j] == "'":
                    if j + 1 < n and line[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i, prev, stmt_start = j + 1, "value", False
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if line[j] == '"':
                    if j + 1 < n and line[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            i, prev, stmt_start = j + 1, "value", False
            continue
        if c == "'" or (c == "." and line.startswith(".'", i)):  # transpose
            i += 2 if c == "." else 1
            prev = "value"
            continue
        if c in "([{":
            st.depth += 1
            i, prev, stmt_start = i + 1, "op", False
            continue
        if c in ")]}":
            st.depth = max(0, st.depth - 1)
            i, prev, stmt_start = i + 1, "value", False
            continue
        if c in ",;" and st.depth == 0:
            i, prev, stmt_start = i + 1, "op", True
            continue
        m = _IDENT.match(line, i)
        if m:
            word = m.group(0)
            if stmt_start and st.depth == 0:
                if word in BLOCK_OPENERS:
                    st.blocks.append(word)
                elif word == "end" and st.blocks:
                    st.blocks.pop()
                rest = line[m.end():]
                if _is_command_syntax(word, rest):
                    break  # e.g. `hold on`, `format long g`: rest of line is literal text
            i, prev, stmt_start = m.end(), "value", False
            continue
        m = _NUMBER.match(line, i)
        if m and m.end() > i:
            i, prev, stmt_start = m.end(), "value", False
            continue
        if c == ".":
            i, prev = i + 1, "op"
            continue
        i, prev, stmt_start = i + 1, "op", False
    return has_code


def _is_command_syntax(word: str, rest: str) -> bool:
    """MATLAB command syntax: `word arg` where arg isn't an operator or assignment."""
    if word in BLOCK_OPENERS or word in ("end", "else", "elseif", "case", "otherwise", "catch",
                                          "return", "break", "continue"):
        return False
    if not rest or rest[0] not in " \t":
        return False
    arg = rest.lstrip()
    if not arg or arg[0] in "%,;=(":
        return False
    if re.match(r"[-+*/\\^<>~&|.]=?\s", arg) or re.match(r"[=<>~]=", arg):
        return False  # binary operator with spaces around it: `a - b`
    if arg[0] in "'\"" or _IDENT.match(arg) or arg[0].isdigit() or arg[0] == "-":
        return True
    return False


def split_regions(lines: list[str | None], section_starts: set[int] | None = None) -> list[Region]:
    """Compute regions from document lines (None marks a non-code line).

    section_starts: document lines of section-break paragraphs; the first
    region after each one starts a new section.
    """
    section_starts = section_starts or set()
    regions: list[Region] = []
    st = _State()
    cur_start = None
    in_function = False
    section_has_region = False
    pending_section = True
    first_code_line_of_section = None

    def close_section():
        nonlocal section_has_region
        if regions and section_has_region:
            regions[-1].end_of_section = True

    for ln, text in enumerate(lines):
        if text is None:
            if ln in section_starts:
                if not section_has_region and first_code_line_of_section is not None:
                    _add(regions, first_code_line_of_section, first_code_line_of_section, pending_section, in_function)
                    section_has_region = True
                close_section()
                pending_section, section_has_region, first_code_line_of_section = True, False, None
            continue
        if first_code_line_of_section is None:
            first_code_line_of_section = ln
        has_code = scan_line(text, st)
        if in_function and not st.blocks:
            # MATLAB lumps everything from the first local function's `end` to the
            # last code line into one region.
            last = max(i for i, t in enumerate(lines) if t is not None)
            _add(regions, ln, last, pending_section, True)
            pending_section, section_has_region, cur_start = False, True, None
            break
        if cur_start is None and has_code:
            cur_start = ln
            if not in_function and st.blocks and st.blocks[0] == "function":
                # The function header is a region of its own; body statements follow.
                in_function = True
                _add(regions, ln, ln, pending_section, True)
                pending_section, section_has_region, cur_start = False, True, None
                continue
        if cur_start is not None and not (st.depth > 0 or st.continued or st.in_block_comment
                                          or len(st.blocks) > (1 if in_function else 0)):
            _add(regions, cur_start, ln, pending_section, in_function)
            pending_section, section_has_region, cur_start = False, True, None
    if cur_start is not None:
        _add(regions, cur_start, len(lines) - 1, pending_section, in_function)
        section_has_region = True
    elif not section_has_region and first_code_line_of_section is not None:
        _add(regions, first_code_line_of_section, first_code_line_of_section, pending_section, in_function)
        section_has_region = True
    close_section()
    return regions


def _add(regions: list[Region], start: int, end: int, section_break: bool, is_function: bool) -> None:
    regions.append(Region(len(regions), start, end, section_break=section_break, is_function=is_function))
