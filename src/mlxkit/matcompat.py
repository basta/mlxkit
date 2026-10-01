"""Make .mat files loadable in Octave.

MATLAB objects (timeseries, table, string, ...) are stored as opaque MCOS data
that Octave can't read. We decode them with the `mat-io` package and save
plain structs with the same field names, so code like `inputs.Data(:,1)` keeps
working.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import scipy.io


def needs_conversion(path: str | Path) -> bool:
    try:
        data = scipy.io.loadmat(path)
    except Exception:
        return False  # v7.3 (HDF5) files: Octave reads them directly
    return any(type(v).__name__ == "MatlabOpaque" for k, v in data.items() if not k.startswith("__"))


def convert(src: str | Path, dst: str | Path) -> list[str]:
    """Write a plain-struct copy of src to dst. Returns notes about what was converted."""
    from matio import load_from_mat

    notes: list[str] = []
    data = load_from_mat(str(src))
    out = {}
    for name, value in data.items():
        if name.startswith("__"):
            continue
        out[name] = _plain(value, name, notes)
    scipy.io.savemat(dst, out, long_field_names=True, do_compression=True, oned_as="column")
    return notes


def _plain(v, path: str, notes: list[str]):
    cls = type(v).__name__
    if cls in ("MatlabOpaque", "MatlabObject"):
        classname = getattr(v, "classname", "?")
        props = getattr(v, "properties", {}) or {}
        if classname == "timeseries":
            notes.append(f"{path}: timeseries -> struct(Data, Time, Name)")
            return _timeseries(props)
        notes.append(f"{path}: {classname} object -> struct of its properties")
        return {k: _plain(p, f"{path}.{k}", notes) for k, p in props.items() if _savable(k)}
    if isinstance(v, dict):
        return {k: _plain(p, f"{path}.{k}", notes) for k, p in v.items() if _savable(k)}
    if cls == "DataFrame":  # MATLAB table
        notes.append(f"{path}: table -> struct of columns")
        return {str(c): _plain(v[c].to_numpy(), f"{path}.{c}", notes) for c in v.columns}
    if isinstance(v, np.ndarray) and v.dtype.kind == "U":
        notes.append(f"{path}: string array -> char/cellstr")
        return v.item() if v.size == 1 else v.astype(object)
    if isinstance(v, np.ndarray) and v.dtype == object:
        return np.array([_plain(x, path, notes) for x in v.flat], dtype=object).reshape(v.shape)
    return v


def _timeseries(props: dict) -> dict:
    data = np.asarray(props.get("Data_"))
    time = np.asarray(props.get("Time_", []), dtype=float)
    if time.size == 0:  # uniformly sampled: rebuild from start + increment
        info = props["TimeInfo"].properties
        n = int(np.asarray(info["Length"]).item())
        start = float(np.asarray(info["Start_"]).item())
        inc = float(np.asarray(info["Increment_"]).item())
        time = start + inc * np.arange(n)
    name = props.get("Name")
    name = "" if name is None or np.size(name) == 0 else str(np.asarray(name).item())
    return {"Data": data, "Time": time.reshape(-1, 1), "Name": name}


def _savable(key: str) -> bool:
    return key.isidentifier() and not key.startswith("_")
