"""OS-protected Canvas sessions; fail visibly and preserve legacy data on failure."""
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from platform_support import IS_WINDOWS, dpapi

PROTECTION = "macos-keychain-v1"


def account_key(path):
    return hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()


def vault(operation, account, value=None):
    helper = os.environ.get("POLYU_SESSION_VAULT")
    if not helper or not Path(helper).is_absolute() or not Path(helper).is_file():
        raise RuntimeError("Keychain 保护组件不可用；未保存明文会话，原账号文件保持不变。")
    command = [helper, operation, account]
    # This override addresses an explicitly created synthetic keychain, never the login keychain.
    test_keychain = os.environ.get("POLYU_TEST_KEYCHAIN")
    if test_keychain:
        if os.environ.get("POLYU_TEST_ISOLATION") != "1":
            raise RuntimeError("测试 Keychain 必须明确处于隔离验证模式。")
        command += ["--test-keychain", test_keychain]
    try:
        result = subprocess.run(command, input=json.dumps(value, ensure_ascii=False).encode("utf-8") if value is not None else None,
                                capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Keychain 暂不可用；未降级保存明文。请重试或检查钥匙串授权。") from None
    if result.returncode == 3 and operation == "get":
        return None
    if result.returncode != 0:
        raise RuntimeError("无法访问 Canvas Keychain 会话；未降级保存明文。原账号资料保留，请检查钥匙串授权。")
    if operation == "get":
        try:
            value = json.loads(result.stdout)
            if not isinstance(value, list): raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise RuntimeError("Keychain 会话格式无效，原账号资料保留。") from None


def protect_session(path, rows, windows=IS_WINDOWS, protector=dpapi):
    if windows:
        encoded = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        return {"protection": "windows-user-dpapi", "data": base64.b64encode(protector(encoded)).decode("ascii")}
    if sys.platform == "darwin":
        account = account_key(path)
        vault("set", account, rows)
        if vault("get", account) != rows:
            raise RuntimeError("Keychain 写入复核失败，未替换原会话文件。")
        return {"protection": PROTECTION, "account": account}
    raise RuntimeError("此平台没有已支持的 Canvas 会话保护机制，拒绝保存明文。")


def read_session(path, value, windows=IS_WINDOWS, protector=dpapi):
    if windows:
        if not isinstance(value, dict) or value.get("protection") != "windows-user-dpapi":
            raise RuntimeError("Windows 登录资料缺少系统保护，请退出登录后重新连接")
        try:
            rows = json.loads(protector(base64.b64decode(value["data"], validate=True), decrypt=True))
            if not isinstance(rows, list): raise ValueError()
            return rows
        except (KeyError, ValueError, UnicodeError):
            raise RuntimeError("Windows 会话保护数据无效；原账号文件保留。") from None
    if sys.platform == "darwin" and isinstance(value, dict) and value.get("protection") == PROTECTION:
        if value.get("account") != account_key(path):
            raise RuntimeError("Keychain 会话属于其他账号目录，拒绝读取。")
        rows = vault("get", value["account"])
        if rows is None:
            raise RuntimeError("Keychain 会话已不可用，原学习资料保留；请重新授权同一 Canvas 账号。")
        return rows
    if sys.platform == "darwin" and isinstance(value, list):
        # Replacement happens only after OS write + independent read-back succeeds.
        marker = protect_session(path, value)
        from native_canvas import write_private
        write_private(path, marker)
        return value
    raise RuntimeError("会话资料未受支持的系统保护，拒绝读取。")


def delete_session(path):
    path = Path(path)
    if sys.platform == "darwin" and path.exists():
        vault("delete", account_key(path))
