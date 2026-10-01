"""mlx: edit and run MATLAB live scripts without MATLAB.

  mlx edit   script.mlx            write script.live.m, an editable plain-text version
  mlx build  script.live.m         apply edits back into script.mlx
  mlx run    script.mlx            run in GNU Octave and embed outputs (figures, text)
  mlx html   script.mlx            render script + outputs to script.html for viewing
  mlx show   script.mlx            print the plain-text version to the terminal
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .document import Document
from .package import MlxPackage


def _live_path(mlx: Path) -> Path:
    return mlx.with_name(mlx.stem + ".live.m")


def _mlx_path(live: Path) -> Path:
    name = live.name
    stem = name[: -len(".live.m")] if name.endswith(".live.m") else live.stem
    return live.with_name(stem + ".mlx")


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def cmd_edit(args) -> int:
    from .livetext import to_text

    src = Path(args.file)
    dst = Path(args.output) if args.output else _live_path(src)
    if dst.exists() and not args.force:
        print(f"{dst} already exists (use --force to overwrite it)", file=sys.stderr)
        return 1
    dst.write_text(to_text(Document(MlxPackage.read(src).document_xml)))
    print(f"wrote {dst}\nedit it, then run: mlx build {dst}")
    return 0


def cmd_build(args) -> int:
    from .livetext import from_text

    src = Path(args.file)
    base_path = Path(args.base) if args.base else _mlx_path(src)
    out = Path(args.output) if args.output else base_path
    pkg = MlxPackage.read(base_path)
    base = Document(pkg.document_xml)
    doc = from_text(src.read_text(), base)
    code_changed = [b.code for b in doc.code_blocks] != [b.code for b in base.code_blocks]
    pkg.document_xml = doc.to_xml()
    if code_changed and pkg.output_xml is not None:
        # Saved outputs no longer match the code; drop them until the next `mlx run`.
        from .outputs import build_output_xml, code_line_numbers
        from .regions import split_regions

        sections = {b.first_line for b in doc.blocks if b.kind == "sectionbreak"}
        pkg.output_xml = build_output_xml(split_regions(doc.lines(), sections), [], code_line_numbers(doc.lines()))
    if out == base_path:
        _backup(out)
    pkg.write(out)
    note = " (code changed: old outputs cleared, run `mlx run` to regenerate)" if code_changed else ""
    print(f"wrote {out}{note}")
    return 0


def cmd_run(args) -> int:
    from .runner import run

    src = Path(args.file)
    out = Path(args.output) if args.output else src
    if shutil.which(args.octave) is None and not Path(args.octave).exists():
        print(f"Octave not found ({args.octave}). Install it (e.g. `brew install octave`) or pass --octave PATH.",
              file=sys.stderr)
        return 1
    if out == src:
        _backup(src)
    result = run(src, out, octave=args.octave, timeout=args.timeout, keep_workdir=args.keep,
                 log=lambda m: print(m, file=sys.stderr))
    for note in result.notes:
        print(f"note: {note}", file=sys.stderr)
    kinds: dict[str, int] = {}
    for o in result.outputs:
        kinds[o.kind] = kinds.get(o.kind, 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in kinds.items()) or "no outputs"
    print(f"wrote {out}: {summary}")
    if result.error:
        print(f"execution stopped at an error: {result.error}", file=sys.stderr)
        if args.verbose:
            print(result.octave_log, file=sys.stderr)
        return 2
    if args.verbose:
        print(result.octave_log, file=sys.stderr)
    return 0


def cmd_html(args) -> int:
    from .render import render_html

    src = Path(args.file)
    out = Path(args.output) if args.output else src.with_suffix(".html")
    out.write_text(render_html(src))
    print(f"wrote {out}")
    return 0


def cmd_show(args) -> int:
    from .livetext import to_text

    text = to_text(Document(MlxPackage.read(args.file).document_xml))
    print(text.split("%[appendix]", 1)[0], end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mlx", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("edit", help="write an editable plain-text version of a .mlx")
    e.add_argument("file")
    e.add_argument("-o", "--output")
    e.add_argument("-f", "--force", action="store_true", help="overwrite an existing .live.m")
    e.set_defaults(func=cmd_edit)

    b = sub.add_parser("build", help="apply an edited .live.m back into its .mlx")
    b.add_argument("file")
    b.add_argument("--base", help="the original .mlx (default: next to the .live.m)")
    b.add_argument("-o", "--output", help="output .mlx (default: overwrite the base, keeping a .bak)")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("run", help="run a .mlx in GNU Octave and embed its outputs")
    r.add_argument("file")
    r.add_argument("-o", "--output", help="output .mlx (default: overwrite the input, keeping a .bak)")
    r.add_argument("--octave", default="octave", help="Octave executable")
    r.add_argument("--timeout", type=float, default=3600, help="seconds before giving up")
    r.add_argument("--keep", action="store_true", help="keep the temporary work directory")
    r.add_argument("-v", "--verbose", action="store_true", help="print Octave's own log")
    r.set_defaults(func=cmd_run)

    h = sub.add_parser("html", help="render a .mlx and its outputs to HTML")
    h.add_argument("file")
    h.add_argument("-o", "--output")
    h.set_defaults(func=cmd_html)

    s = sub.add_parser("show", help="print a .mlx as plain text")
    s.add_argument("file")
    s.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
