"""Package the tested folder build for GitHub Releases without an installer tool."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    package = root / "dist" / "windows-app" / "VoiceToText"
    for required in ("VoiceToText.exe", "ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe", "_internal"):
        if not (package / required).exists():
            raise SystemExit(f"Incomplete desktop build: missing {required}")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    output = root / "dist" / "releases"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"VoiceStudio-{args.version}-windows-x64.zip"
    files = sorted(p for p in package.rglob("*") if p.is_file())
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as bundle:
        for index, file in enumerate(files):
            bundle.write(file, "VoiceToText/" + file.relative_to(package).as_posix())
            if index % 250 == 0:
                print(f"Packaged {index}/{len(files)} files", flush=True)
        bundle.writestr("VoiceToText/RELEASE-INFO.json", json.dumps({
            "version": args.version, "source_commit": commit, "platform": "windows-x64",
            "entrypoint": "VoiceToText.exe",
        }, indent=2))
        bundle.writestr("VoiceToText/使用说明.txt", (
            "Voice Studio Windows 独立客户端\n\n"
            "1. 解压整个 VoiceToText 文件夹到可写目录。\n"
            "2. 双击 VoiceToText.exe 启动，不需要 Python、Conda 或 BAT。\n"
            "3. 请保留 _internal、FFmpeg 和 yt-dlp；不可只复制主 EXE。\n"
            "4. 模型沿用用户的 Hugging Face 缓存；首次使用需准备 tiny 和 medium 模型。\n"
            "5. GPU 推理需要兼容的 NVIDIA 驱动；也可在界面选择 CPU。\n"
            "6. 转写结果默认在用户文档 VoiceToText 目录，日志位于本地应用数据 VoiceToText 目录。\n"
            "\n源码及模型准备说明：https://github.com/xiaoxin179/voiceToText\n"
        ))
    if archive.stat().st_size >= 2 * 1024**3:
        raise SystemExit("Archive exceeds GitHub's per-asset size limit")
    with zipfile.ZipFile(archive) as bundle:
        corrupt = bundle.testzip()
        if corrupt:
            raise SystemExit(f"Archive validation failed: {corrupt}")
    with archive.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size,
                      "sha256": digest, "source_commit": commit}, indent=2), flush=True)


if __name__ == "__main__":
    main()
