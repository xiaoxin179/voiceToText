from __future__ import annotations

from dataclasses import dataclass
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
        info = pa.get_default_input_device_info()
        return _to_device(pa, info)


def get_default_system_loopback() -> AudioDevice:
    pyaudio = _load_pyaudio()
    with pyaudio.PyAudio() as pa:
        info = pa.get_default_wasapi_loopback()
        return _to_device(pa, info, is_loopback=True)


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
