from __future__ import annotations

import unittest
from unittest.mock import patch

from voice_to_text.audio_devices import (
    AudioDevice,
    device_fingerprint,
    list_devices_in_fresh_process,
    resolve_device,
)


class FakePyAudio:
    def __init__(self, devices: list[dict]) -> None:
        self.devices = devices

    def get_device_info_by_index(self, index: int) -> dict:
        return next(device for device in self.devices if device["index"] == index)

    def get_device_info_generator(self):
        yield from self.devices

    def get_loopback_device_info_generator(self):
        yield from (device for device in self.devices if device.get("isLoopbackDevice"))

    def get_host_api_info_by_index(self, _index: int) -> dict:
        return {"name": "Windows WASAPI"}


def device_info(index: int, name: str, *, loopback: bool) -> dict:
    return {
        "index": index,
        "name": name,
        "hostApi": 0,
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 48000,
        "isLoopbackDevice": loopback,
    }


class ResolveDeviceTests(unittest.TestCase):
    def test_remaps_stale_loopback_index_instead_of_opening_microphone(self) -> None:
        requested = AudioDevice(
            index=20,
            name="Speakers (Realtek Audio) [Loopback]",
            host_api="Windows WASAPI",
            max_input_channels=2,
            max_output_channels=0,
            default_sample_rate=48000,
            is_loopback=True,
        )
        pa = FakePyAudio(
            [
                device_info(20, "Microphone (Realtek Audio)", loopback=False),
                device_info(23, requested.name, loopback=True),
            ]
        )

        resolved = resolve_device(pa, requested, require_loopback=True)

        self.assertEqual(resolved.index, 23)
        self.assertTrue(resolved.is_loopback)

    def test_never_accepts_microphone_for_system_capture(self) -> None:
        requested = AudioDevice(
            index=20,
            name="Speakers (Realtek Audio) [Loopback]",
            host_api="Windows WASAPI",
            max_input_channels=2,
            max_output_channels=0,
            default_sample_rate=48000,
            is_loopback=True,
        )
        pa = FakePyAudio([device_info(20, "Microphone (Realtek Audio)", loopback=False)])

        with self.assertRaisesRegex(RuntimeError, "system loopback"):
            resolve_device(pa, requested, require_loopback=True)

    def test_device_fingerprint_ignores_index_changes(self) -> None:
        first = AudioDevice(20, "Headphones", "Windows WASAPI", 0, 2, 48000, False)
        second = AudioDevice(28, "Headphones", "Windows WASAPI", 0, 2, 48000, False)

        self.assertEqual(device_fingerprint([first]), device_fingerprint([second]))

    def test_fresh_process_scan_deserializes_devices(self) -> None:
        class Completed:
            returncode = 0
            stdout = (
                '[{"index": 28, "name": "Headphones [Loopback]", '
                '"host_api": "Windows WASAPI", "max_input_channels": 2, '
                '"max_output_channels": 0, "default_sample_rate": 48000, '
                '"is_loopback": true}]'
            )
            stderr = ""

        with patch("voice_to_text.audio_devices.subprocess.run", return_value=Completed()):
            devices = list_devices_in_fresh_process()

        self.assertEqual(devices[0].index, 28)
        self.assertTrue(devices[0].is_loopback)


if __name__ == "__main__":
    unittest.main()
