"""Source rewrites that turn newer MATLAB syntax into something Octave runs.

- Name=value arguments (R2021a): plot(x, y, LineWidth=2) -> plot(x, y, 'LineWidth', 2)
- arguments blocks (R2019b) in functions are removed; default values become
  `if ~exist('x', 'var'), x = default; end`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .regions import _State, scan_line

_OPS = ("==", "~=", "<=", ">=", "&&", "||", ".*", "./", ".\\", ".^", ".'")


@dataclass
class Token:
    kind: str  # ws, nl, comment, str, id, num, op, open, close
    text: str


def tokenize(code: str) -> list[Token]:
    toks: list[Token] = []
    i, n = 0, len(code)
    prev_value = False  # does a value end right before i (decides ' = transpose vs string)
    depth = 0
    while i < n:
        c = code[i]
        if c == "\n":
            toks.append(Token("nl", c))
            i += 1
            prev_value = False
            continue
        if c in " \t\r":
            j = i
            while j < n and code[j] in " \t\r":
                j += 1
            toks.append(Token("ws", code[i:j]))
            if depth > 0 and j < n and code[j] == "'":
                prev_value = False  # [a 'b'] starts a string
            i = j
            continue
        if c == "%" or code.startswith("...", i):
            j = code.find("\n", i)
            j = n if j < 0 else j
            toks.append(Token("comment", code[i:j]))
            i = j
            continue
        if (c == "'" and not prev_value) or c == '"':
            j = i + 1
            while j < n and code[j] != "\n":
                if code[j] == c:
                    if j + 1 < n and code[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            toks.append(Token("str", code[i:j + 1]))
            i, prev_value = j + 1, True
            continue
        m = re.match(r"[A-Za-z_]\w*", code[i:])
        if m:
            toks.append(Token("id", m.group(0)))
            i, prev_value = i + m.end(), True
            continue
        m = re.match(r"(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?[ij]?", code[i:])
        if m and m.end() > 0:
            toks.append(Token("num", m.group(0)))
            i, prev_value = i + m.end(), True
            continue
        if c in "([{":
            depth += 1
            toks.append(Token("open", c))
            i, prev_value = i + 1, False
            continue
        if c in ")]}":
            depth = max(0, depth - 1)
            toks.append(Token("close", c))
            i, prev_value = i + 1, True
            continue
        op = next((o for o in _OPS if code.startswith(o, i)), c)
        toks.append(Token("op", op))
        i += len(op)
        prev_value = op in ("'", ".'")
    return toks


def name_value_args(code: str) -> str:
    """plot(x, LineWidth=2) -> plot(x, 'LineWidth', 2)."""
    if "=" not in code:
        return code
    toks = tokenize(code)
    stack: list[str] = []
    out: list[str] = []
    last_sig: Token | None = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "open":
            stack.append(t.text)
        elif t.kind == "close" and stack:
            stack.pop()
        if (t.kind == "id" and stack and stack[-1] == "(" and last_sig is not None
                and (last_sig.kind == "open" and last_sig.text == "(" or last_sig.kind == "op" and last_sig.text == ",")):
            j = i + 1
            while j < len(toks) and toks[j].kind == "ws":
                j += 1
            if j < len(toks) and toks[j].kind == "op" and toks[j].text == "=":
                out.append(f"'{t.text}', ")
                last_sig = Token("op", ",")
                i = j + 1
                while i < len(toks) and toks[i].kind == "ws":
                    i += 1
                continue
        out.append(t.text)
        if t.kind not in ("ws", "nl", "comment"):
            last_sig = t
        i += 1
    return "".join(out)


_ARG_DEFAULT = re.compile(r"^\s*(\w+)\b[^=%]*?=\s*(.+?)\s*(%.*)?$")


def strip_arguments_blocks(code: str) -> str:
    """Remove `arguments ... end` blocks, keeping default values."""
    if "arguments" not in code:
        return code
    lines = code.split("\n")
    out: list[str] = []
    st = _State()
    in_args, args_depth = False, 0
    defaults: list[str] = []
    for line in lines:
        before = len(st.blocks)
        scan_line(line, st)
        if not in_args and len(st.blocks) > before and st.blocks[-1] == "arguments":
            in_args, args_depth = True, len(st.blocks)
            defaults = []
            out.append("")  # keep line numbers stable
            continue
        if in_args:
            if len(st.blocks) < args_depth:
                in_args = False
                out.append(" ".join(defaults))
                continue
            m = _ARG_DEFAULT.match(line)
            if m and "." not in m.group(1):
                defaults.append(f"if ~exist('{m.group(1)}', 'var'), {m.group(1)} = {m.group(2).rstrip(';')}; end")
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)



# --- "text" + x string concatenation ---------------------------------------

_CHAIN_STOP_OPS = {",", ";", "=", "==", "~=", "<", ">", "<=", ">=", "&", "|", "&&", "||", ":", "-"}


def string_plus(code: str) -> str:
    """`"Total: " + n + " items"` -> `mlxkit_strplus("Total: ", n, " items")`.

    MATLAB adds strings by concatenating them (numbers are converted with
    string()); Octave treats "..." as char codes and adds numerically. Only
    `+` chains with a double-quoted literal as a direct operand are rewritten.
    """
    if '"' not in code or "+" not in code:
        return code
    toks = tokenize(code)
    changed = True
    while changed:
        changed, toks = _rewrite_one_chain(toks)
    return "".join(t.text for t in toks)


def _rewrite_one_chain(toks: list[Token]) -> tuple[bool, list[Token]]:
    # Index of each token's matching bracket, and bracket depth per token.
    depth, d, stack, match = [], 0, [], {}
    for i, t in enumerate(toks):
        if t.kind == "close" and stack:
            j = stack.pop()
            match[j], match[i] = i, j
            d -= 1
        depth.append(d)
        if t.kind == "open":
            stack.append(i)
            d += 1
    for i, t in enumerate(toks):
        if t.kind != "op" or t.text != "+":
            continue
        start, end = _chain_bounds(toks, i, depth, match)
        if start is None:
            continue
        terms, cur = [], []
        for k in range(start, end):
            if toks[k].kind == "op" and toks[k].text == "+" and depth[k] == depth[i]:
                terms.append(cur)
                cur = []
            else:
                cur.append(toks[k])
        terms.append(cur)
        if not any(_is_dq_literal(term) for term in terms):
            continue
        if any(_is_unary(term) for term in terms):
            continue
        args = ", ".join("".join(x.text for x in term).strip() for term in terms)
        new = [Token("id", "mlxkit_strplus"), Token("open", "("), Token("raw", args), Token("close", ")")]
        lead = [x for x in toks[start:end][:1] if x.kind == "ws"]
        trail = [x for x in toks[start:end][-1:] if x.kind == "ws"]
        return True, toks[:start] + lead + new + trail + toks[end:]
    return False, toks


def _chain_bounds(toks, i, depth, match):
    """Extent [start, end) of the additive expression around the `+` at i."""
    lvl = depth[i]
    if lvl > 0:
        k = i - 1
        while k >= 0 and not (toks[k].kind == "open" and depth[k] == lvl - 1):
            k -= 1
        if k < 0 or toks[k].text != "(":
            return None, None  # inside [] or {}, spaces separate elements; leave alone

    def stop(k):
        t = toks[k]
        if depth[k] < lvl or (t.kind == "open" and depth[k] < lvl):
            return True
        if depth[k] > lvl:
            return False
        if t.kind in ("nl", "comment") or (t.kind == "close"):
            return t.kind != "close" or depth[k] < lvl
        return t.kind == "op" and t.text in _CHAIN_STOP_OPS

    s = i
    while s > 0 and not stop(s - 1) and not (toks[s - 1].kind == "open" and depth[s - 1] == lvl - 1):
        s -= 1
    e = i + 1
    while e < len(toks) and not stop(e) and not (toks[e].kind == "close" and depth[e] == lvl - 1):
        e += 1
    return s, e


def _is_dq_literal(term: list[Token]) -> bool:
    sig = [t for t in term if t.kind != "ws"]
    return len(sig) == 1 and sig[0].kind == "str" and sig[0].text.startswith('"')


def _is_unary(term: list[Token]) -> bool:
    return not [t for t in term if t.kind != "ws"]


# --- MATLAB-only graphics properties -----------------------------------------

_DROP_PROPS = {"SeriesIndex"}


def drop_unsupported_props(code: str) -> str:
    """Remove `'SeriesIndex', value` pairs (and similar) from call arguments."""
    if not any(p in code for p in _DROP_PROPS):
        return code
    toks = tokenize(code)
    out: list[Token] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "str" and t.text[1:-1] in _DROP_PROPS:
            # drop: preceding ", ", the name, the comma, and the value up to the next top-level , or )
            while out and out[-1].kind == "ws":
                out.pop()
            if out and out[-1].kind == "op" and out[-1].text == ",":
                out.pop()
            j, d = i + 1, 0
            while j < len(toks) and not (toks[j].kind == "op" and toks[j].text == ","):
                j += 1
            j += 1
            while j < len(toks):
                tj = toks[j]
                if tj.kind == "open":
                    d += 1
                elif tj.kind == "close":
                    if d == 0:
                        break
                    d -= 1
                elif d == 0 and tj.kind == "op" and tj.text == ",":
                    break
                j += 1
            i = j
            continue
        out.append(t)
        i += 1
    return "".join(t.text for t in out)


# --- graphics handle dot-notation ---------------------------------------------

GRAPHICS_CREATORS = {
    "figure", "gcf", "gca", "gco", "axes", "subplot", "nexttile", "plot", "plot3", "line", "scatter",
    "scatter3", "bar", "barh", "histogram", "area", "stem", "stairs", "errorbar", "surf", "mesh", "contour",
    "contourf", "quiver", "quiver3", "patch", "fill", "image", "imagesc", "imshow", "text", "title", "xlabel",
    "ylabel", "zlabel", "legend", "colorbar", "annotation", "rectangle", "animatedline", "fplot", "fsurf",
    "fimplicit", "semilogx", "semilogy", "loglog", "polarplot", "pie", "xline", "yline", "uicontrol",
}
HANDLE_PROPS = {"parent", "children", "currentaxes", "xlabel", "ylabel", "zlabel", "title", "legend"}
_ASSIGN = re.compile(r"^\s*(?:\[([^\]=]*)\]|(\w+))\s*=\s*(\w+)(?:\s*\.\s*(\w+))?", re.M)


def find_handle_vars(code: str) -> set[str]:
    """Variables assigned from graphics calls (`ax = gca`, `[h] = plot(...)`, `ax = h.Parent`)."""
    handles: set[str] = set()
    changed = True
    while changed:
        changed = False
        for m in _ASSIGN.finditer(code):
            names = [n.strip() for n in (m.group(1) or m.group(2)).replace(",", " ").split() if n.strip() != "~"]
            if not names:
                continue
            rhs, prop = m.group(3), m.group(4)
            is_handle = (rhs in GRAPHICS_CREATORS and prop is None) or (
                rhs in handles and prop is not None and prop.lower() in HANDLE_PROPS)
            if is_handle and names[0] not in handles:
                handles.add(names[0])
                changed = True
    return handles


def handle_dot_notation(code: str, handles: set[str]) -> str:
    """ax.YLim(2) -> get(ax, 'YLim')(2);  s.XData = v; -> set(s, 'XData', v);"""
    if not handles or "." not in code:
        return code
    toks = tokenize(code)
    out: list[str] = []
    i, n = 0, len(toks)
    stmt_start = True
    while i < n:
        t = toks[i]
        chain = _dot_chain(toks, i) if t.kind == "id" and t.text in handles else None
        if chain:
            props, j = chain
            idx_end = _index_group_end(toks, j) if stmt_start else None
            if idx_end is not None and _is_simple_assign(toks, idx_end):
                # s.XData(k) = v  ->  read, modify, write back (keeps `end` working)
                k = _skip_ws(toks, idx_end) + 1
                end = _statement_end(toks, k)
                value = handle_dot_notation("".join(x.text for x in toks[k:end]).strip(), handles)
                index = handle_dot_notation("".join(x.text for x in toks[j:idx_end]), handles)
                target = t.text
                for p_ in props[:-1]:
                    target = f"get({target}, '{p_}')"
                out.append(f"mlxkit_tmp_ = get({target}, '{props[-1]}'); mlxkit_tmp_{index} = {value}; "
                           f"mlxkit_set({target}, '{props[-1]}', mlxkit_tmp_); clear mlxkit_tmp_")
                i = end
                stmt_start = False
                continue
            if stmt_start and _is_simple_assign(toks, j):
                k = _skip_ws(toks, j) + 1  # past '='
                end = _statement_end(toks, k)
                value = handle_dot_notation("".join(x.text for x in toks[k:end]).strip(), handles)
                target = t.text
                for p_ in props[:-1]:
                    target = f"mlxkit_get({target}, '{p_}')"
                out.append(f"mlxkit_set({target}, '{props[-1]}', {value})")
                i = end
                stmt_start = False
                continue
            expr = t.text
            for p_ in props:
                expr = f"get({expr}, '{p_}')"
            out.append(expr)
            i = j
            stmt_start = False
            continue
        out.append(t.text)
        if t.kind == "nl" or (t.kind == "op" and t.text in (";", ",")):
            stmt_start = True
        elif t.kind not in ("ws", "comment"):
            stmt_start = False
        i += 1
    return "".join(out)


def _dot_chain(toks, i):
    """`name.A.B` starting at i -> (["A", "B"], index after the chain), or None."""
    props, j = [], i + 1
    while j + 1 < len(toks) and toks[j].kind == "op" and toks[j].text == "." and toks[j + 1].kind == "id":
        props.append(toks[j + 1].text)
        j += 2
    if not props:
        return None
    if i > 0 and toks[i - 1].kind == "op" and toks[i - 1].text == ".":
        return None  # part of a longer chain like s.ax.YLim
    return props, j


def _index_group_end(toks, j):
    """If toks[j] opens `(...)` or `{...}`, the index just past its matching close."""
    if j >= len(toks) or toks[j].kind != "open" or toks[j].text not in "({":
        return None
    d = 0
    for k in range(j, len(toks)):
        if toks[k].kind == "open":
            d += 1
        elif toks[k].kind == "close":
            d -= 1
            if d == 0:
                return k + 1
    return None


def _skip_ws(toks, j):
    while j < len(toks) and toks[j].kind == "ws":
        j += 1
    return j


def _is_simple_assign(toks, j):
    j = _skip_ws(toks, j)
    return j < len(toks) and toks[j].kind == "op" and toks[j].text == "="


def _statement_end(toks, k):
    d = 0
    while k < len(toks):
        t = toks[k]
        if t.kind == "open":
            d += 1
        elif t.kind == "close":
            d -= 1
        elif d == 0 and (t.kind in ("nl", "comment") or (t.kind == "op" and t.text in (";", ","))):
            break
        k += 1
    return k


def transform(code: str, handles: set[str] | None = None) -> str:
    """Rewrite newer-MATLAB constructs. `handles`: variables holding graphics handles
    (see find_handle_vars); defaults to those assigned within `code` itself."""
    code = string_plus(drop_unsupported_props(strip_arguments_blocks(name_value_args(code))))
    return handle_dot_notation(code, find_handle_vars(code) if handles is None else handles)
