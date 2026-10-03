"""User-local DashScope credentials, kept outside projects and release bundles."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from pathlib import Path

API_KEY_ENV = "DASHSCOPE_API_KEY"


def credentials_path() -> Path:
    # The override also lets acceptance tests use an entirely empty profile.
    override = os.getenv("CONCEPT_MEMORY_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "credentials.json"
    if os.name == "nt":
        root = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "concept-memory" / "credentials.json"


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """Use Windows DPAPI for encryption tied to the current Windows account."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise OSError("Windows credential protection failed")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def get_api_key(env_name: str = API_KEY_ENV) -> str:
    key = os.getenv(env_name, "").strip()
    if key or env_name != API_KEY_ENV:
        return key
    try:
        payload = json.loads(credentials_path().read_text(encoding="utf-8"))
        data = base64.b64decode(payload["value"], validate=True)
        if payload["storage"] == "windows-dpapi" and os.name == "nt":
            data = _protect(data, decrypt=True)
        elif payload["storage"] != "private-file" or os.name == "nt":
            return ""
        return data.decode("utf-8").strip()
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def validate_api_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("请输入 API Key。")
    key = value.strip()
    if not key or len(key) > 512 or any(char.isspace() for char in key):
        raise ValueError("请输入完整的 API Key，不要包含空格或换行。")
    return key


def verify_api_key(key: str) -> None:
    """Verify access before saving; never expose provider errors containing secrets."""
    try:
        import dashscope

        response = dashscope.Generation.call(
            api_key=key, model="qwen-flash", messages=[{"role": "user", "content": "Reply OK."}],
            result_format="message", max_tokens=8, timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError("credential rejected")
    except Exception:
        raise ValueError("API Key 验证失败，请检查密钥、模型权限和网络后重试。") from None


def save_api_key(key: str) -> None:
    key = validate_api_key(key)
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = key.encode("utf-8")
    storage = "private-file"
    if os.name == "nt":
        data = _protect(data)
        storage = "windows-dpapi"
    payload = json.dumps({"storage": storage, "value": base64.b64encode(data).decode("ascii")})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".credentials-", delete=False) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def redact_error(error: Exception) -> str:
    message = str(error)
    key = get_api_key()
    return message.replace(key, "[API Key]") if key else message
