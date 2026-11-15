"""Entry point for the windowed executable and its console-free helper process."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import traceback


def configure_runtime() -> Path:
    data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VoiceToText"
    data.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        # Relative exports belong to the user's documents, never the bundle.
        output = Path.home() / "Documents" / "VoiceToText"
        output.mkdir(parents=True, exist_ok=True)
        os.chdir(output)
        os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, (data / "desktop.log").open("a", encoding="utf-8", buffering=1))
    return data


def diagnose(target: Path) -> int:
    """Verify the packaged UI, audio helper, codecs and GPU inference locally."""
    import shutil
    import subprocess
    import numpy as np
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from .audio_devices import list_devices_in_fresh_process
    from .cuda_runtime import add_cuda_dll_directories
    from .model_cache import required_models_error_message
    from .qt_app import VoiceToTextWindow, create_app_icon
    from .text_converter import TextConverter

    target.parent.mkdir(parents=True, exist_ok=True)
    os.environ["VTT_TASK_DB"] = str(target.with_suffix(".sqlite3"))
    report = {"executable": sys.executable, "frozen": bool(getattr(sys, "frozen", False))}
    try:
        assert not required_models_error_message(), required_models_error_message()
        if os.name == "nt":
            report["console_window"] = bool(ctypes.windll.kernel32.GetConsoleWindow())
            assert not report["console_window"], "Unexpected console attached"
        report["audio_devices"] = len(list_devices_in_fresh_process())
        assert report["audio_devices"] > 0, "No audio devices found"
        report["cuda_dll_directories"] = [str(p) for p in add_cuda_dll_directories()]
        from faster_whisper import WhisperModel
        model = WhisperModel("medium", device="cuda", compute_type="float16", local_files_only=True)
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="zh", vad_filter=False)
        list(segments)  # Force lazy CUDA execution, not just model construction.
        report["gpu_inference"] = "medium / cuda / float16"
        del model
        TextConverter("simplified")
        for tool in ("ffmpeg", "ffprobe", "yt-dlp"):
            command = shutil.which(tool)
            assert command, f"Missing {tool}"
            result = subprocess.run([command, "--version" if tool == "yt-dlp" else "-version"],
                                    capture_output=True, timeout=45,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            assert result.returncode == 0, f"{tool} failed"
            report[tool] = command
        app = QApplication([])
        app.setWindowIcon(create_app_icon())
        window = VoiceToTextWindow()
        window.show()

        def finish():
            try:
                screenshot = target.with_suffix(".png")
                assert window.grab().save(str(screenshot)), "Screenshot failed"
                report.update(ok=True, window_title=window.windowTitle(), screenshot=str(screenshot))
            except Exception:
                report.update(ok=False, error=traceback.format_exc())
            finally:
                window.close()
                app.quit()

        QTimer.singleShot(1500, finish)
        app.exec()
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report.get("ok") else 1


def main() -> int:
    data = configure_runtime()
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--scan-audio-devices":
            from dataclasses import asdict
            from .audio_devices import list_devices
            Path(sys.argv[2]).write_text(json.dumps([asdict(d) for d in list_devices()]), encoding="utf-8")
            return 0
        if len(sys.argv) == 3 and sys.argv[1] == "--diagnose":
            return diagnose(Path(sys.argv[2]).resolve())
        from .qt_app import main as run_gui
        return run_gui()
    except Exception:
        log = data / "desktop-error.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, f"启动失败，错误详情已保存：\n{log}", "语音转文字", 0x10)
        return 1
