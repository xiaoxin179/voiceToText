from __future__ import annotations

import os
import site
import sys
from pathlib import Path

_DLL_HANDLES = []


def candidate_cuda_dll_dirs() -> list[Path]:
    roots: list[Path] = []
    try:
        roots.extend(Path(path) for path in site.getsitepackages())
    except Exception:
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages")

    relative_dirs = [
        Path("nvidia") / "cublas" / "bin",
        Path("nvidia") / "cudnn" / "bin",
        Path("nvidia") / "cuda_runtime" / "bin",
    ]

    found: list[Path] = []
    for root in roots:
        for relative in relative_dirs:
            path = root / relative
            if path.exists() and path not in found:
                found.append(path)
    return found


def add_cuda_dll_directories() -> list[Path]:
    added: list[Path] = []
    for path in candidate_cuda_dll_dirs():
        if hasattr(os, "add_dll_directory"):
            _DLL_HANDLES.append(os.add_dll_directory(str(path)))
        os.environ["PATH"] = f"{path}{os.pathsep}{os.environ.get('PATH', '')}"
        added.append(path)
    return added
