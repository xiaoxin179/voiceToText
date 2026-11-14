from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: int
    is_loopback: bool = False


def _load_pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: pyaudiowpatch. Install it with "
            "`pip install pyaudiowpatch` inside the conda environment."
        ) from exc
    return pyaudio


def _host_api_name(pa, host_api_index: int) -> str:
    try:
        return str(pa.get_host_api_info_by_index(host_api_index).get("name", "unknown"))
    except Exception:
        return "unknown"


def _to_device(pa, info: dict, *, is_loopback: bool = False) -> AudioDevice:
    return AudioDevice(
        index=int(info["index"]),
        name=str(info.get("name", "")),
        host_api=_host_api_name(pa, int(info.get("hostApi", 0))),
        max_input_channels=int(info.get("maxInputChannels", 0)),
        max_output_channels=int(info.get("maxOutputChannels", 0)),
        default_sample_rate=int(float(info.get("defaultSampleRate", 16000))),
        is_loopback=bool(is_loopback or info.get("isLoopbackDevice", False)),
    )


def list_devices() -> list[AudioDevice]:
    pyaudio = _load_pyaudio()
    devices: list[AudioDevice] = []
    with pyaudio.PyAudio() as pa:
        for info in pa.get_device_info_generator():
            devices.append(_to_device(pa, info))
    return devices


def device_fingerprint(devices: Iterable[AudioDevice]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        sorted(
            (
                device.name,
                device.host_api,
                device.max_input_channels,
                device.max_output_channels,
                device.default_sample_rate,
                device.is_loopback,
            )
            for device in devices
        )
    )


def list_devices_in_fresh_process() -> list[AudioDevice]:
    completed = subprocess.run(
        [sys.executable, "-m", "voice_to_text.audio_devices", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Fresh audio device scan failed: {detail}")
    return [AudioDevice(**item) for item in json.loads(completed.stdout)]


def list_loopback_devices() -> list[AudioDevice]:
    pyaudio = _load_pyaudio()
    devices: list[AudioDevice] = []
    with pyaudio.PyAudio() as pa:
        for info in pa.get_loopback_device_info_generator():
            devices.append(_to_device(pa, info, is_loopback=True))
    return devices


def get_default_microphone() -> AudioDevice:
    pyaudio = _load_pyaudio()
    with pyaudio.PyAudio() as pa:
        default_info = pa.get_default_input_device_info()
        default_name = str(default_info.get("name", ""))

        # WASAPI input devices cooperate better with WASAPI loopback than MME devices.
        for info in pa.get_device_info_generator():
            host_api = _host_api_name(pa, int(info.get("hostApi", 0)))
            if (
                host_api == "Windows WASAPI"
                and int(info.get("maxInputChannels", 0)) > 0
                and not info.get("isLoopbackDevice", False)
                and str(info.get("name", "")) in default_name
            ):
                return _to_device(pa, info)

        for info in pa.get_device_info_generator():
            host_api = _host_api_name(pa, int(info.get("hostApi", 0)))
            if (
                host_api == "Windows WASAPI"
                and int(info.get("maxInputChannels", 0)) > 0
                and not info.get("isLoopbackDevice", False)
            ):
                return _to_device(pa, info)

        return _to_device(pa, default_info)


def get_default_system_loopback() -> AudioDevice:
    pyaudio = _load_pyaudio()
    with pyaudio.PyAudio() as pa:
        info = pa.get_default_wasapi_loopback()
        return _to_device(pa, info, is_loopback=True)


def resolve_device(
    pa,
    requested: AudioDevice,
    *,
    require_loopback: bool,
) -> AudioDevice:
    """Resolve a saved UI selection against the current PortAudio device list."""

    def is_valid(device: AudioDevice) -> bool:
        if device.host_api != requested.host_api or device.name != requested.name:
            return False
        if require_loopback:
            return device.is_loopback and device.max_input_channels > 0
        return not device.is_loopback and device.max_input_channels > 0

    try:
        current = _to_device(pa, pa.get_device_info_by_index(requested.index))
        if is_valid(current):
            return current
    except Exception:
        pass

    infos = (
        pa.get_loopback_device_info_generator()
        if require_loopback
        else pa.get_device_info_generator()
    )
    for info in infos:
        candidate = _to_device(pa, info, is_loopback=require_loopback)
        if is_valid(candidate):
            return candidate

    source_name = "system loopback" if require_loopback else "microphone"
    raise RuntimeError(
        f"The selected {source_name} device is no longer available: {requested.name}. "
        "Refresh devices and select it again."
    )


def select_input_device(source: str, index: int | None = None) -> AudioDevice:
    if source not in ("mic", "system"):
        raise ValueError("source must be mic or system")
    if index is None:
        return get_default_microphone() if source == "mic" else get_default_system_loopback()
    devices = list_loopback_devices() if source == "system" else list_devices()
    for device in devices:
        if (device.index == index and device.max_input_channels > 0
                and device.is_loopback == (source == "system")):
            return device
    raise ValueError(f"Device {index} is not a valid {source} input; run devices to list inputs")


def format_devices(devices: Iterable[AudioDevice]) -> str:
    rows = [
        "index | in | out | rate | loopback | host api | name",
        "----- | -- | --- | ---- | -------- | -------- | ----",
    ]
    for device in devices:
        rows.append(
            f"{device.index:>5} | "
            f"{device.max_input_channels:>2} | "
            f"{device.max_output_channels:>3} | "
            f"{device.default_sample_rate:>5} | "
            f"{str(device.is_loopback):>8} | "
            f"{device.host_api} | "
            f"{device.name}"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    if "--json" not in sys.argv:
        raise SystemExit("Use --json to scan audio devices.")
    print(json.dumps([asdict(device) for device in list_devices()], ensure_ascii=True))
