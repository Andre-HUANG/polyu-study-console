"""Small OS boundaries shared by the two native shells."""
from __future__ import annotations
import ctypes
import os
import re
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
APP_NAME = "PolyU study控制台"


def is_link(path):
    """Treat Windows directory junctions as redirects as well as symlinks."""
    path = Path(path)
    return path.is_symlink() or (IS_WINDOWS and getattr(path, "is_junction", lambda: False)())


def backend_session():
    # Windows uses the shell's kill-on-close Job Object instead of a POSIX group.
    if not IS_WINDOWS and hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass


def account_root():
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return Path(long_path(os.path.abspath(base / APP_NAME)))
    return Path.home() / "Library/Application Support" / APP_NAME


def long_path(value):
    """Windows extended paths avoid a registry change for the archive tree."""
    value = str(value)
    if not IS_WINDOWS or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    if re.match(r"^[A-Za-z]:\\", value):
        return "\\\\?\\" + value
    return value


def display_path(value):
    value = str(value)
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    return value[4:] if value.startswith("\\\\?\\") else value


def dpapi(data, decrypt=False):
    """Protect cookies with this Windows user's DPAPI key, without UI."""
    if not IS_WINDOWS:
        raise RuntimeError("Windows 账号保护只能在 Windows 上使用")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError("本 Windows 账号无法保护或读取 Canvas 会话，请重新登录")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def helper_command(name, *args):
    if getattr(sys, "frozen", False):
        if name != "pdf_layout.py":
            raise ValueError("Unsupported bundled helper")
        executable = str(Path(sys.executable).with_name("PolyUWorker.exe")) if IS_WINDOWS else sys.executable
        return [executable, "--pdf-layout", *map(str, args)]
    if IS_WINDOWS:
        return [str(Path(sys.executable).with_name("python.exe")), "-I", "-B", "-X", "utf8",
                str(Path(__file__).with_name(name)), *map(str, args)]
    return [sys.executable, str(Path(__file__).with_name(name)), *map(str, args)]
