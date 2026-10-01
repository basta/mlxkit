# mlxkit

Edit and run MATLAB live scripts (`.mlx`) without MATLAB, using GNU Octave.

```
mlx serve  [folder]          # notebook editor in the browser: edit, run, see outputs inline
mlx edit   script.mlx        # -> script.live.m, a plain-text version you can edit anywhere
mlx build  script.live.m     # apply your edits back into script.mlx
mlx run    script.mlx        # run it in Octave; figures and output are embedded in the .mlx
mlx html   script.mlx        # -> script.html, to look at the result in a browser
mlx show   script.mlx        # print the plain-text version
```

`build` and `run` overwrite the `.mlx` in place and keep the previous version as `script.mlx.bak`.

## The editor

`mlx serve` opens `http://127.0.0.1:8765` (or the next free port) with every
`.mlx` under the folder in a sidebar. Click a paragraph to edit it as Markdown;
code cells have MATLAB syntax highlighting.

Each open file gets its own Octave session that keeps its workspace between
runs, like MATLAB's Live Editor:

- **▶ Section** on a code cell (or **⌘⏎** with the cursor in it) runs that
  section in the current workspace; **⇥ To here** runs everything up to it.
- **▶ Run all** (**⇧⌘⏎**) runs the whole script in a fresh workspace.
- **■ Stop** interrupts Octave but keeps the session and its variables;
  **⟲ Restart** clears the workspace.
- **Workspace** lists the session's variables. Code that ran in the current
  session has a green edge.

Figures and printed output appear right under the line that produced them and
are saved into the `.mlx`. A partial run replaces only the outputs of the
statements it ran; editing code dims the outputs it affects, and saving keeps
outputs of statements you didn't change. **⌘S** saves (keeping a `.bak`). The
server only listens on localhost and only touches `.mlx` files inside the
folder you gave it.

## Install

```
brew install octave            # or your platform's Octave package (version 9+)
uv tool install ./mlxkit       # or: pip install ./mlxkit
```

Optional Octave packages are loaded automatically when installed, and cover common MATLAB toolboxes:

```
octave --eval "pkg install -forge datatypes statistics symbolic"
```

## How it works

An `.mlx` file is a zip archive. `matlab/document.xml` holds the text and code
as Word-style paragraphs, and `matlab/output.xml` holds the saved results.

- **Editing.** `mlx edit` writes MATLAB's own plain-text live script format
  (R2025a+): `%[text]` lines for formatted text (Markdown), plain lines for
  code, `%%` for section breaks. On `mlx build`, every paragraph whose text you
  didn't change keeps its original XML, so images, equations, live controls and
  formatting survive. All 132 files in our test corpus round-trip byte for byte.
- **Running.** `mlx run` splits the code into the statements ("regions") MATLAB
  uses to attach outputs, runs them one by one in Octave's base workspace, and
  records each region's printed output, warnings, errors and the figures it
  touched. Local functions at the end of the script become function files.
  Outputs are written into `output.xml` the way MATLAB does: figures as PNGs,
  `x = 5` as variable outputs, matrices and structs as their own output types.
- **Compatibility shims** cover common MATLAB/Octave differences:
  - `.mat` files containing MATLAB objects (`timeseries`, `table`, ...) are
    converted to plain structs Octave can load (via `mat-io`).
  - ODE solvers accept MATLAB's extra-parameter syntax `ode45(f, t, y0, opts, p1, p2)`.
  - `set(groot, 'defaultLegendInterpreter', ...)` and other defaults Octave doesn't know are ignored.
  - LaTeX labels (`'Interpreter','latex'`) are rewritten into Octave's TeX subset.
  - `tiledlayout`/`nexttile` map onto `subplot`.
  - Scripts inside a MATLAB project run from the project root with all project
    folders on the path; `currentProject()` works.

## Limits

- Octave isn't MATLAB. Toolbox functions without an Octave equivalent fail, and
  MATLAB `string` semantics (`"a" + 1` concatenates in MATLAB) differ, since
  Octave treats `"..."` as a char array.
- Figures are rendered by Octave and look slightly different from MATLAB's.
- Live controls (sliders, dropdowns) run with their current value.
- The `.mlx` output format is undocumented. The files mlxkit writes follow what
  MATLAB R2021a-R2026b save, but haven't been verified by opening them in every
  MATLAB release.

## Development

```
uv sync
uv run --group dev pytest
uv run python tools/corpus_check.py CORPUS_DIR      # regions vs. MATLAB's saved ones
uv run python tools/roundtrip_check.py CORPUS_DIR   # .mlx -> text -> .mlx
uv run python tools/compat_survey.py out.json *.mlx # how far real scripts get in Octave
```
