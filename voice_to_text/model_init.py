from __future__ import annotations

import argparse
import os
from pathlib import Path

from .model_cache import model_cache_summary, systran_model_cache_dir

DEFAULT_MODELS = ["tiny", "medium"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download faster-whisper models before running the app.")
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Model names to download.",
    )
    parser.add_argument(
        "--load-check",
        action="store_true",
        help="Load each model on CPU after downloading to verify the model files.",
    )
    return parser


def download_model(model_name: str) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("Missing dependency: huggingface-hub. Install faster-whisper dependencies first.") from exc

    repo_id = f"Systran/faster-whisper-{model_name}"
    print(f"\n==> Downloading {repo_id}")
    print(model_cache_summary(model_name))

    path = snapshot_download(
        repo_id=repo_id,
        repo_type="model",
        local_dir=None,
    )

    print(f"Downloaded to: {path}")
    print(model_cache_summary(model_name))
    return Path(path)


def load_check(model_name: str) -> None:
    print(f"Checking model load on CPU: {model_name}")
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    del model
    print(f"CPU load check passed: {model_name}")


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    args = build_parser().parse_args(argv)

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN is not set. Download will use anonymous HuggingFace requests.")
        print("This is allowed, but it may be slower or rate-limited.")

    for model_name in args.models:
        download_model(model_name)
        if args.load_check:
            load_check(model_name)

    print("\nModel cache summary:")
    for model_name in args.models:
        print(f"- {model_name}: {model_cache_summary(model_name)}")
        print(f"  cache dir: {systran_model_cache_dir(model_name)}")

    return 0
