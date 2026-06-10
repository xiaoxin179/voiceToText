from __future__ import annotations

from typing import Literal

TextMode = Literal["original", "simplified", "traditional"]


class TextConverter:
    def __init__(self, mode: TextMode = "simplified") -> None:
        self.mode = mode
        self._converter = None
        if mode == "original":
            return

        try:
            from opencc import OpenCC
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: opencc-python-reimplemented. "
                "Run `pip install -r requirements.txt` inside the conda environment."
            ) from exc

        config = "t2s" if mode == "simplified" else "s2t"
        self._converter = OpenCC(config)

    def convert(self, text: str) -> str:
        if not text or self._converter is None:
            return text
        return self._converter.convert(text)
