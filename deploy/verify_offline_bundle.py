"""离线包验收探针：解压到新位置后，用包内运行时完成一次真实 MCP stdio 握手。

用法：python verify_offline_bundle.py <zip路径> <解压目标目录>
判据：initialize 成功 + tools/list 返回 scan_codebase 与 search_concepts。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

TIMEOUT = 60.0
_START = time.monotonic()


def log(message: str) -> None:
    print("[%6.1fs] %s" % (time.monotonic() - _START, message), flush=True)


def watchdog(seconds: float) -> None:
    """总时限兜底：任何一步卡住都不该让探针无限期挂起。"""

    def _fire() -> None:
        log("[看门狗] 超过 %.0f 秒仍未完成，强制结束探针。" % seconds)
        import os

        os._exit(2)

    timer = threading.Timer(seconds, _fire)
    timer.daemon = True
    timer.start()


def send(proc: subprocess.Popen, message: dict) -> None:
    line = json.dumps(message, ensure_ascii=False)
    assert proc.stdin is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def read_message(proc: subprocess.Popen) -> dict | None:
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                print("  [非协议输出，已忽略] " + line[:200])
                continue
        print("  [非协议输出，已忽略] " + line[:200])


def kill_tree(root_pid: int) -> None:
    """用 taskkill /T 连整棵进程树一起收掉。

    不能用只杀父进程的方式：memory-mcp.cmd 会派生 python.exe，
    子进程存活时管道句柄不释放，后续任何读取都会永久阻塞。
    wmic 在 Win11 上已被弃用且启动极慢，这里也不使用。
    """

    try:
        done = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(root_pid)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        print("  [清理] taskkill /T %d -> %s" % (root_pid, done.stdout.strip()[:120]))
    except Exception as exc:  # pragma: no cover - 清理失败不应影响判定
        print("  [清理] taskkill 失败：%s" % exc)


def main() -> int:
    watchdog(300.0)
    zip_path = Path(sys.argv[1])
    target = Path(sys.argv[2])
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    log("开始解压 %s" % zip_path.name)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target)
    log("解压完成")
    pkg_root = target / "concept-memory"
    log("解压后包根: %s" % pkg_root)
    if not pkg_root.is_dir():
        log("FAIL 解压结构不对，缺少 concept-memory\\ 顶层目录")
        return 1

    pth = list(pkg_root.rglob("*._pth"))
    log("包内 ._pth 文件数: %d（必须为 0）" % len(pth))
    sp_bin = pkg_root / "python" / "Lib" / "site-packages" / "bin"
    log("残留 site-packages\\bin: %s（必须为 False）" % sp_bin.exists())

    checker = pkg_root / "install" / "check_install.ps1"
    if not checker.is_file():
        log("FAIL 缺少包内自检脚本 %s" % checker)
        return 1
    log("运行包内自检 check_install.ps1")
    checked = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(checker),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(pkg_root),
    )
    tail = [line for line in (checked.stdout or "").splitlines() if line.strip()][-6:]
    for line in tail:
        log("  | " + line.strip())
    if checked.returncode != 0:
        log("FAIL check_install.ps1 退出码 %d" % checked.returncode)
        return 1
    log("check_install.ps1 PASS（退出码 0）")

    mcp_cmd = pkg_root / "bin" / "memory-mcp.cmd"
    if not mcp_cmd.is_file():
        log("FAIL 缺少 %s" % mcp_cmd)
        return 1

    log("启动 MCP：%s" % mcp_cmd)
    proc = subprocess.Popen(
        [str(mcp_cmd)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(pkg_root),
    )
    stderr_chunks: list[str] = []

    def drain_stderr() -> None:
        """常驻读取 stderr：既防管道写满卡死子进程，也保留诊断信息。"""

        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_chunks.append(line)
        except Exception:
            pass

    threading.Thread(target=drain_stderr, daemon=True).start()
    try:
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "deploy-verifier", "version": "0.1"},
                },
            },
        )
        init = read_message(proc)
        if not init or "result" not in init:
            log("FAIL initialize 失败：%s" % init)
            return 1
        server = init["result"].get("serverInfo", {})
        log("initialize OK -> serverInfo=%s" % server)

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listing = read_message(proc)
        if not listing or "result" not in listing:
            log("FAIL tools/list 失败：%s" % listing)
            return 1
        names = [t.get("name") for t in listing["result"].get("tools", [])]
        log("tools/list OK -> %s" % names)
        missing = [n for n in ("scan_codebase", "search_concepts") if n not in names]
        if missing:
            log("FAIL 缺少工具：%s" % missing)
            return 1
        log("PASS 离线包在新路径下可完成真实 MCP 握手，且两个工具均可用")
        return 0
    finally:
        # 关键：memory-mcp.cmd 会派生子进程 python.exe，两者都持有 stdout/stderr 管道句柄。
        # 只杀 cmd.exe 而不杀子进程，再去 read() 管道就会永久阻塞（曾把探针卡死到超时）。
        kill_tree(proc.pid)
        if stderr_chunks:
            log("[stderr 摘要] " + "".join(stderr_chunks).strip()[:400])
        else:
            log("stderr 无输出（stdio 卫生正常）")


if __name__ == "__main__":
    raise SystemExit(main())
