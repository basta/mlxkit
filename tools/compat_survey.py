"""Run many real .mlx files in Octave and tally how far each gets.

usage: python tools/compat_survey.py OUT.json FILE.mlx [FILE.mlx ...]

Each file runs in its own folder (so it finds its data). Results: how many
regions ran before the first error, and the error message, so the most
common incompatibilities can be fixed first.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

from mlxkit.document import Document
from mlxkit.package import MlxPackage
from mlxkit.regions import split_regions
from mlxkit.runner import run


def main():
    out_json, files = Path(sys.argv[1]), [Path(f) for f in sys.argv[2:]]
    results = []
    for f in files:
        doc = Document(MlxPackage.read(f).document_xml)
        sections = {b.first_line for b in doc.blocks if b.kind == "sectionbreak"}
        total = sum(not r.is_function for r in split_regions(doc.lines(), sections))
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                res = run(f, Path(tmp) / "out.mlx", timeout=300)
            regions_with_output = {r for o in res.outputs for r in o.regions}
            last = max(regions_with_output, default=-1)
            entry = {"file": str(f), "total": total, "error": res.error,
                     "outputs": len(res.outputs), "figures": sum(o.kind == "figure" for o in res.outputs),
                     "last_output_region": last}
        except Exception as e:  # noqa: BLE001 - survey keeps going
            entry = {"file": str(f), "total": total, "error": f"[mlxkit] {type(e).__name__}: {e}"}
        entry["seconds"] = round(time.time() - t0, 1)
        results.append(entry)
        status = "OK " if not entry.get("error") else "ERR"
        print(f"{status} {entry['seconds']:6.1f}s {f.name}: {entry.get('error') or ''}"[:200], flush=True)
    out_json.write_text(json.dumps(results, indent=1))
    ok = sum(not r.get("error") for r in results)
    print(f"\n{ok}/{len(results)} ran to completion")


if __name__ == "__main__":
    main()
