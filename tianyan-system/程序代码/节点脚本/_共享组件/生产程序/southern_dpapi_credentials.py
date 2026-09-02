from __future__ import annotations

import base64
import ctypes
import json
from ctypes import wintypes
from pathlib import Path
from typing import Any


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect_bytes(data: bytes) -> bytes:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Windows DPAPI is required.")
    input_blob, input_buffer = _blob(data)
    output_blob = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Southern Fund login",
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def unprotect_bytes(data: bytes) -> bytes:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Windows DPAPI is required.")
    input_blob, input_buffer = _blob(data)
    output_blob = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def write_credentials(path: Path, login_id: str, password: str) -> None:
    login_id = str(login_id or "").strip()
    if not login_id or not password:
        raise ValueError("Both login ID and password are required.")
    payload = json.dumps(
        {"format_version": 1, "login_id": login_id, "password": password},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.b64encode(protect_bytes(payload)).decode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded + "\n", encoding="ascii")
    temporary.replace(path)


def read_credentials(path: Path) -> tuple[str, str]:
    encrypted = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    payload = json.loads(unprotect_bytes(encrypted).decode("utf-8"))
    login_id = str(payload.get("login_id") or "").strip()
    password = str(payload.get("password") or "")
    if not login_id or not password:
        raise ValueError("Southern DPAPI credential payload is incomplete.")
    return login_id, password
