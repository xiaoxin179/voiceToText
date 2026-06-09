from __future__ import annotations

import os
from pathlib import Path

REQUIRED_MODEL_ENV = "VTT_REQUIRED_MODELS"
DEFAULT_REQUIRED_MODELS = ["tiny", "medium"]


def huggingface_cache_dir() -> Path:
    root = os.environ.get("HF_HOME")
    if root:
        return Path(root) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def systran_model_cache_dir(model_size: str) -> Path:
    return huggingface_cache_dir() / f"models--Systran--faster-whisper-{model_size}"


def required_models() -> list[str]:
    raw = os.environ.get(REQUIRED_MODEL_ENV)
    if not raw:
        return DEFAULT_REQUIRED_MODELS.copy()
    return [model.strip() for model in raw.replace(";", ",").split(",") if model.strip()]


def model_snapshot_dirs(model_size: str) -> list[Path]:
    snapshots = systran_model_cache_dir(model_size) / "snapshots"
    if not snapshots.exists():
        return []
    return [path for path in snapshots.iterdir() if path.is_dir()]


def is_model_cache_ready(model_size: str) -> bool:
    required_files = {"model.bin", "config.json", "tokenizer.json"}
    for snapshot in model_snapshot_dirs(model_size):
        names = {file.name for file in snapshot.iterdir() if file.is_file()}
        model_path = snapshot / "model.bin"
        if required_files.issubset(names) and model_path.stat().st_size > 1024 * 1024:
            return True
    return False


def missing_required_models(models: list[str] | None = None) -> list[str]:
    checked_models = models if models is not None else required_models()
    return [model for model in checked_models if not is_model_cache_ready(model)]


def model_cache_summary(model_size: str) -> str:
    path = systran_model_cache_dir(model_size)
    if not path.exists():
        return f"model cache not found: {path}"

    files = [file for file in path.rglob("*") if file.is_file()]
    total_bytes = sum(file.stat().st_size for file in files)
    total_mb = total_bytes / 1024 / 1024
    ready = "ready" if is_model_cache_ready(model_size) else "not ready"
    return f"model cache: {path} | files={len(files)} | size={total_mb:.1f} MB | {ready}"


def required_models_error_message(models: list[str] | None = None) -> str:
    checked_models = models if models is not None else required_models()
    missing = missing_required_models(checked_models)
    if not missing:
        return ""

    lines = [
        "缺少必需的 faster-whisper 模型缓存，程序已中断启动。",
        "",
        f"缓存目录: {huggingface_cache_dir()}",
        f"必需模型: {', '.join(checked_models)}",
        f"缺失模型: {', '.join(missing)}",
        "",
        "请先运行:",
        f"python init_models.py --models {' '.join(missing)}",
        "",
        "或双击项目根目录的 init_models.bat。",
    ]
    return "\n".join(lines)
