"""Give the desktop window its own taskbar identity, including brokered launches."""
from __future__ import annotations

import ctypes
import os
import uuid
import warnings

APP_USER_MODEL_ID = "xiaoxin.voiceToText.desktop"


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


class _VariantValue(ctypes.Union):
    _fields_ = [("text", ctypes.c_void_p), ("alignment", ctypes.c_uint64),
                ("storage", ctypes.c_ubyte * (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8))]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("reserved1", ctypes.c_ushort),
                ("reserved2", ctypes.c_ushort), ("reserved3", ctypes.c_ushort),
                ("value", _VariantValue)]


def _check_hresult(result: int, operation: str) -> None:
    if result < 0:
        raise OSError(f"{operation} failed: HRESULT 0x{result & 0xffffffff:08x}")


def configure_process_identity() -> None:
    if os.name != "nt":
        return
    setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    setter.argtypes = [ctypes.c_wchar_p]
    setter.restype = ctypes.c_long
    _check_hresult(setter(APP_USER_MODEL_ID), "SetCurrentProcessExplicitAppUserModelID")


def configure_window_identity(hwnd: int) -> None:
    """Set PKEY_AppUserModel_ID on the actual top-level HWND before showing it.

    A process-level ID alone can leave windows launched by a packaged host
    associated with that host. The explicit window property overrides grouping.
    """
    if os.name != "nt":
        return
    iid = GUID.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
    key = PROPERTYKEY(GUID.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)
    store = ctypes.c_void_p()
    getter = ctypes.windll.shell32.SHGetPropertyStoreForWindow
    getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    getter.restype = ctypes.c_long
    _check_hresult(getter(hwnd, ctypes.byref(iid), ctypes.byref(store)), "SHGetPropertyStoreForWindow")
    vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                 ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(vtable[6])
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[7])
    text = ctypes.create_unicode_buffer(APP_USER_MODEL_ID)
    value = PROPVARIANT()
    value.vt = 31  # VT_LPWSTR; SetValue copies this string into the store.
    value.value.text = ctypes.cast(text, ctypes.c_void_p).value
    try:
        _check_hresult(set_value(store, ctypes.byref(key), ctypes.byref(value)), "IPropertyStore.SetValue")
        _check_hresult(commit(store), "IPropertyStore.Commit")
    finally:
        release(store)


def warn_identity_failure(error: Exception) -> None:
    # Do not silently mask failed identity setup: it changes taskbar behavior.
    warnings.warn(f"Windows taskbar identity could not be set: {error}", RuntimeWarning)
