#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建 concept-memory 的 Windows 离线包（自带精简 Python 运行时，免安装免联网）。

用法示例（在打包机上执行，用打包机自己的 Python 3.11 解释器）：

    python deploy\\build_offline_bundle.py
    python deploy\\build_offline_bundle.py --out D:\\dist --keep-work
    python deploy\\build_offline_bundle.py --wheels D:\\wheels

参数：--src 默认脚本所在目录，脚本会自动定位 memory_system\\；
      --out 默认 <项目根>\\deploy\\dist；
      --runtime 打包用的 Python 3.11 安装目录；不传则先读环境变量
                CONCEPT_MEMORY_BUILD_PYTHON，再自动探测（py 启动器 / PATH / 常见安装位置）；
      --version 版本号；不传则取源码 __version__，并与 pyproject.toml 校验一致；
      --wheels 给了就完全不联网，直接用该目录的 .whl 离线装依赖；
      --keep-work 保留 _work 中间目录便于排查。

产物：<out>\\concept-memory-offline-win64-v<版本>.zip，解压后顶层只有 concept-memory\\ 一个文件夹。
      zip 文件名带版本，但包内顶层文件夹名固定为 concept-memory：用户升级时覆盖同名目录即可，
      客户端配置里的路径一个字都不用改。
脚本幂等：每次重跑先删掉 --out\\_work，不往用户家目录写任何东西。

设计约束（已实测，改动前请先读）：
  1. 精简运行时 = python.exe / pythonw.exe / python3.dll / python311.dll /
     vcruntime140*.dll / DLLs\\ / Lib\\（排除 Lib\\site-packages）/ libs\\，约 86MB。
     绝不整目录复制打包机的 Python（混装了 torch/pandas 等无关包）。
  2. 绝不在包内生成 python311._pth：它会让解释器进入隔离模式，DLLs 不进 sys.path，
     import _sqlite3 会失败，而 sqlite3 是存储层必需。
     因此改为启动脚本设置 PYTHONPATH = 包内 python\\Lib\\site-packages + 包内 src。
  3. 依赖一律用「打包机的 base python -m pip ... --target 包内 site-packages」安装；
     包内 python 没有 pip，绝不用它执行 pip。离线安装必须让 pip 从 wheel 目录解析
     整棵依赖树（--no-index --find-links），逐个 --no-deps 装会丢传递依赖。
  4. 包内不得出现硬编码绝对路径；启动脚本用 %~dp0 推导包根。
  5. MCP 走 stdio，日志绝不能写 stdout；启动脚本不输出任何提示文字。
  6. 包内 python 跑自检会生成 __pycache__，必须禁写并在打包前清理。
  7. 依赖必须让 pip 从 wheel 目录解析整棵依赖树：--no-deps 逐包装会漏掉 pydantic_core
     这类传递依赖，装出来的包 import mcp 就会失败。

本脚本不会被复制进离线包，只留在仓库里。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PACKAGE_DIR_NAME = "concept-memory"
ZIP_NAME_TEMPLATE = "concept-memory-offline-win64-v{version}.zip"

# 打包机运行时不在仓库里写死个人路径（公开仓库里那就是别人的报错来源）。
# 优先级：--runtime > 本环境变量 > 自动探测。
RUNTIME_ENV_VAR = "CONCEPT_MEMORY_BUILD_PYTHON"
RUNTIME_FALLBACK_DIRS = (
    r"C:\Program Files\Python311",
    r"C:\Program Files (x86)\Python311",
)
REQUIRED_PYTHON_VERSION = "3.11"

# 要装进包内 site-packages 的依赖清单（按任务规定，顺序即安装顺序）
REQUIREMENTS = [
    "mcp",
    "mcp-types",
    "dashscope",
    "python-dotenv",
    "anyio",
    "httpx",
    "httpcore",
    "httpx-sse",
    "jsonschema",
    "pydantic",
    "cryptography",
    "pywin32",
    "rich",
    "starlette",
    "uvicorn",
    "aiohttp",
    "requests",
    "certifi",
    "urllib3",
    "attrs",
    "click",
    "typer",
]

# 这些包必须真的装进包里，装不上就终止（其余依赖缺失只告警）
CRITICAL_REQUIREMENTS = ("mcp", "dashscope", "python-dotenv")

# 运行时目录里要复制的顶层文件
RUNTIME_TOP_FILES = (
    "python.exe",
    "pythonw.exe",
    "python3.dll",
    "python311.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)

# 复制 Lib\\ 时按目录名整块跳过的子目录（体积优化，均与运行无关）
LIB_EXCLUDE_DIRS = {
    "site-packages",
    "__pycache__",
    "test",
    "tests",
    "turtledemo",
    "idlelib",
    "ensurepip",
    "lib2to3",
    "distutils",
    "tkinter",
    "curses",
    "msilib",
    "pydoc_data",
    "venv",
}

# 任意目录下都要跳过的名字
ALWAYS_SKIP_DIRS = {"__pycache__", ".DS_Store", ".mypy_cache", ".pytest_cache", ".git"}
ALWAYS_SKIP_SUFFIXES = (".pyc", ".pyo")

ENV_EXAMPLE = """\
# concept-memory 环境变量示例
#
# 重要：当前 MCP 服务（python -m memory_system.mcp_server）不读取 .env 文件，
# 它只从「MCP 客户端配置里为该服务设置的 env 字段」或「系统环境变量」读取
# DASHSCOPE_API_KEY。因此本文件只是示例与备忘，把值填到客户端配置或系统环境变量里才生效。
#
# 获取方式：登录阿里云百炼（DashScope）控制台 -> API-KEY 管理 -> 创建新的 API-KEY。
# 控制台入口：https://bailian.console.aliyun.com/  （API-KEY 页面在右上角个人中心里）
#
# 填写格式（等号两侧不要加引号，不要有多余空格）：

DASHSCOPE_API_KEY=
"""

MCP_CMD = r"""@echo off
setlocal EnableExtensions DisableDelayedExpansion
for %%I in ("%~dp0..") do set "PKG_ROOT=%%~fI"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%PKG_ROOT%\python\Lib\site-packages;%PKG_ROOT%\src"
"%PKG_ROOT%\python\python.exe" -m memory_system.mcp_server
exit /b %ERRORLEVEL%
"""

WEB_CMD = r"""@echo off
setlocal EnableExtensions DisableDelayedExpansion
for %%I in ("%~dp0..") do set "PKG_ROOT=%%~fI"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%PKG_ROOT%\python\Lib\site-packages;%PKG_ROOT%\src"
"%PKG_ROOT%\python\python.exe" -m memory_system.web_server %*
exit /b %ERRORLEVEL%
"""

CLI_CMD = r"""@echo off
setlocal EnableExtensions DisableDelayedExpansion
for %%I in ("%~dp0..") do set "PKG_ROOT=%%~fI"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%PKG_ROOT%\python\Lib\site-packages;%PKG_ROOT%\src"
"%PKG_ROOT%\python\python.exe" -m memory_system.cli %*
exit /b %ERRORLEVEL%
"""

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

# 让中文进度在默认 GBK 控制台与重定向到文件时都不乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def cout(message: str = "") -> None:
    """打印一行进度（仅构建期可见，不进入包内运行时）。"""

    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def fail(message: str, code: int = 1):
    """打印可读的失败原因并终止。"""

    sys.stderr.write("\n[失败] " + message + "\n")
    sys.stderr.flush()
    raise SystemExit(code)


def step(index: int, total: int, title: str) -> None:
    cout("")
    cout("[%d/%d] %s" % (index, total, title))


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            if unit == "B":
                return "%d B" % int(value)
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%.1f GB" % value


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def count_files(path: Path) -> int:
    total = 0
    for _root, _dirs, files in os.walk(path):
        total += len(files)
    return total


def child_env(**extra: str) -> dict:
    """构造子进程环境：强制 UTF-8 输出，禁止污染用户家目录与包内目录。"""

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    # 关键：包内 python 的自检/探测会往包内 python\Lib 写 __pycache__，必须禁掉
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env.update(extra)
    return env


def run(cmd, env=None, allow_fail=False):
    """执行外部命令，返回 (returncode, stdout+stderr)。"""

    printable = " ".join(str(part) for part in cmd)
    cout("  $ " + printable)
    try:
        proc = subprocess.run(
            [str(part) for part in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env if env is not None else child_env(),
        )
    except OSError as exc:
        if allow_fail:
            return 1, str(exc)
        fail("无法执行命令：%s\n原因：%s" % (printable, exc))
    output = proc.stdout.decode("utf-8", errors="replace")
    if output.strip():
        for line in output.rstrip().splitlines():
            cout("    | " + line)
    if proc.returncode != 0 and not allow_fail:
        fail("命令执行失败（退出码 %d）：%s" % (proc.returncode, printable))
    return proc.returncode, output


# ---------------------------------------------------------------------------
# 复制
# ---------------------------------------------------------------------------


def should_skip_dir(name: str, extra: set | None = None) -> bool:
    if name in ALWAYS_SKIP_DIRS:
        return True
    if extra and name in extra:
        return True
    return False


def should_skip_file(name: str) -> bool:
    if name in ALWAYS_SKIP_DIRS:
        return True
    return name.endswith(ALWAYS_SKIP_SUFFIXES)


def copytree(src: Path, dst: Path, exclude_dirs: set | None = None) -> int:
    """递归复制目录，返回复制的文件数。"""

    copied = 0
    dst.mkdir(parents=True, exist_ok=True)
    for entry in os.scandir(src):
        target = dst / entry.name
        if entry.is_dir(follow_symlinks=False):
            if should_skip_dir(entry.name, exclude_dirs):
                continue
            copied += copytree(Path(entry.path), target, exclude_dirs)
        else:
            if should_skip_file(entry.name):
                continue
            shutil.copy2(entry.path, target)
            copied += 1
    return copied


def ensure_utf8_bom(source: Path, target: Path) -> None:
    """把 .ps1 复制进包时补上 UTF-8 BOM。

    Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 ANSI/GBK 解析，
    脚本里的中文提示会变成乱码并连带引发语法错误（引号被乱码吞掉）。
    打包这一层必须兜住，不能依赖源文件始终保持 BOM。
    """

    data = source.read_bytes()
    if not data.startswith(b"\xef\xbb\xbf"):
        data = b"\xef\xbb\xbf" + data
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def prune_bytecode(root: Path) -> int:
    """删掉包内的 __pycache__ 与 .pyc，返回删除的文件数。

    包内 python 跑自检时会生成字节码（已用 PYTHONDONTWRITEBYTECODE 抑制，
    这里再兜底清一次），既减体积，也避免包内混入机器相关产物。
    """

    removed = 0
    for current, dirs, files in os.walk(root, topdown=True):
        for name in list(dirs):
            if name == "__pycache__":
                target = Path(current) / name
                removed += sum(1 for _ in target.rglob("*") if _.is_file())
                shutil.rmtree(target, ignore_errors=True)
                dirs.remove(name)
        for name in files:
            if name.endswith(ALWAYS_SKIP_SUFFIXES):
                try:
                    (Path(current) / name).unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


# ---------------------------------------------------------------------------
# 包内 python 自检
# ---------------------------------------------------------------------------


def bundled_env(pkg_root: Path, python_exe: Path) -> dict:
    site = pkg_root / "python" / "Lib" / "site-packages"
    src = pkg_root / "src"
    return child_env(PYTHONPATH=str(site) + os.pathsep + str(src))


PROBE_MARKER = "__CM_PROBE__"

PROBE_CODE = (
    "import sys, json\n"
    "mods = json.loads(sys.argv[1])\n"
    "out = {}\n"
    "import importlib.util\n"
    "for m in mods:\n"
    "    try:\n"
    "        out[m] = bool(importlib.util.find_spec(m))\n"
    "    except Exception:\n"
    "        out[m] = False\n"
    "print('%s' + json.dumps(out))\n" % PROBE_MARKER
)


def probe_modules(pkg_root: Path, python_exe: Path, modules) -> dict:
    """用包内 python 探测模块是否可导入，返回 {模块名: 是否存在}。"""

    if not python_exe.exists():
        return {name: False for name in modules}
    try:
        proc = subprocess.run(
            [str(python_exe), "-c", PROBE_CODE, json.dumps(list(modules))],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=bundled_env(pkg_root, python_exe),
        )
    except OSError:
        return {name: False for name in modules}
    text = proc.stdout.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith(PROBE_MARKER):
            continue
        try:
            data = json.loads(line[len(PROBE_MARKER):])
        except ValueError:
            continue
        if isinstance(data, dict):
            return {str(key): bool(value) for key, value in data.items()}
    return {name: False for name in modules}


SELFCHECK_CODE = (
    "import sys\n"
    "print('python', sys.version.replace('\\n', ' '))\n"
    "import sqlite3\n"
    "print('sqlite3', sqlite3.sqlite_version)\n"
    "import mcp, dashscope, dotenv\n"
    "print('modules ok')\n"
)


def selfcheck(pkg_root: Path, python_exe: Path) -> None:
    """用包内 python 验证解释器、sqlite3 与关键依赖。失败即终止。"""

    if not python_exe.exists():
        fail("包内解释器不存在：%s" % python_exe)
    code, output = run(
        [python_exe, "-c", SELFCHECK_CODE], env=bundled_env(pkg_root, python_exe)
    )
    if code != 0:
        fail(
            "包内自检未通过（sqlite3 / mcp / dashscope / dotenv 未能全部导入）。\n"
            "请检查：包内 python\\DLLs\\_sqlite3.pyd 是否完整、site-packages 是否装好依赖、"
            "PYTHONPATH 是否指向 python\\Lib\\site-packages 与 src。"
        )


# ---------------------------------------------------------------------------
# wheel 处理与依赖安装
# ---------------------------------------------------------------------------


def normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def deploy_dir() -> Path:
    """本脚本所在目录（<项目根>\\deploy）。"""

    return Path(__file__).resolve().parent


def detect_packager_runtime() -> Path | None:
    """探测打包用的 Python 3.11 安装目录；找不到返回 None。

    顺序：环境变量 → py 启动器 → PATH → 常见安装位置。候选必须同时具备 python.exe
    与 DLLs（DLLs 里是 _sqlite3.pyd，缺了存储层直接不可用）。
    """

    candidates: list[Path] = []

    env_value = os.environ.get(RUNTIME_ENV_VAR)
    if env_value:
        candidates.append(Path(env_value))

    launcher = shutil.which("py") or shutil.which("py.exe")
    if launcher:
        code, output = run([launcher, "-0p"], allow_fail=True)
        if code == 0:
            for line in output.splitlines():
                stripped = line.strip()
                if not stripped.startswith("-V:" + REQUIRED_PYTHON_VERSION):
                    continue
                parts = stripped.split(None, 1)
                if len(parts) != 2:
                    continue
                exe_text = parts[1].lstrip("*").strip()
                if exe_text:
                    candidates.append(Path(exe_text).parent)

    for name in ("python3.11", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found).parent)

    candidates.extend(Path(item) for item in RUNTIME_FALLBACK_DIRS)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        exe = candidate / "python.exe"
        if not exe.is_file() or not (candidate / "DLLs").is_dir():
            continue
        code, output = run(
            [exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], allow_fail=True
        )
        if code == 0 and output.strip().startswith(REQUIRED_PYTHON_VERSION):
            return candidate
    return None


def resolve_runtime(explicit: str | None) -> Path:
    """确定打包用的 Python 3.11 安装目录：--runtime > 环境变量 > 自动探测。"""

    if explicit:
        return Path(explicit).resolve()

    detected = detect_packager_runtime()
    if detected is None:
        fail(
            "找不到打包用的 Python %s 安装目录。\n"
            "请用 --runtime 指定（例如 --runtime C:\\Python311，该目录下应有 python.exe），"
            "或设置环境变量 %s。\n"
            "也可以先用 Windows 的 py 启动器装一个 %s：py -%s -m pip --version 能跑通即可。"
            % (
                REQUIRED_PYTHON_VERSION,
                RUNTIME_ENV_VAR,
                REQUIRED_PYTHON_VERSION,
                REQUIRED_PYTHON_VERSION,
            )
        )
    cout("  已自动探测打包机运行时：%s" % detected)
    return detected


def read_package_version(src: Path) -> str | None:
    """读源码里的 __version__（文本解析，不 import，避免依赖缺失时读不到版本）。"""

    init_path = src / "memory_system" / "__init__.py"
    if not init_path.is_file():
        return None
    for line in init_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("__version__"):
            value = line.partition("=")[2].split("#")[0].strip().strip("'\"")
            return value or None
    return None


def read_pyproject_version(project_root: Path) -> str | None:
    """读 pyproject.toml 的 [project] 段里的 version。"""

    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    in_project = False
    for line in pyproject.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project and stripped.startswith("version") and "=" in stripped:
            return stripped.partition("=")[2].strip().strip("'\"") or None
    return None


def resolve_version(explicit: str | None, src: Path, project_root: Path) -> str:
    """确定打进包里的版本号，保证源码、pyproject.toml、命令行三处一致。"""

    declared = read_package_version(src)
    pyproject_version = read_pyproject_version(project_root)

    if declared and pyproject_version and declared != pyproject_version:
        fail(
            "版本号不一致：src\\memory_system\\__init__.py 是 %s，pyproject.toml 是 %s。\n"
            "先把两处改成同一个版本号再打包。" % (declared, pyproject_version)
        )
    source_version = declared or pyproject_version
    if not source_version:
        fail(
            "读不到版本号：请检查 %s 里的 __version__ 与 %s 里的 [project] version。"
            % (src / "memory_system" / "__init__.py", project_root / "pyproject.toml")
        )
    if explicit and explicit != source_version:
        fail(
            "--version %s 与源码声明的版本号 %s 不一致。\n"
            "要么改用 %s，要么先把源码与 pyproject.toml 一起升到 %s。"
            % (explicit, source_version, source_version, explicit)
        )
    return source_version


def resolve_src_root(src: Path) -> Path:
    """把 --src 归一化成「直接包含 memory_system\\ 的那个目录」。

    默认值按规范取脚本所在目录（<项目根>\\deploy）；若该目录下没有 memory_system\\，
    再依次尝试它的上一级、以及 <项目根>\\src，兼容 src 布局的仓库。
    """

    candidates = [src, src.parent, src / "src", src.parent / "src"]
    for candidate in candidates:
        if (candidate / "memory_system" / "mcp_server.py").is_file():
            return candidate
    return src


# 依赖发行名 -> 用于导入核验的模块名
IMPORT_NAMES = {
    "mcp": "mcp",
    "mcp-types": "mcp_types",
    "dashscope": "dashscope",
    "python-dotenv": "dotenv",
    "anyio": "anyio",
    "httpx": "httpx",
    "httpcore": "httpcore",
    "httpx-sse": "httpx_sse",
    "jsonschema": "jsonschema",
    "pydantic": "pydantic",
    "cryptography": "cryptography",
    "pywin32": "win32api",
    "rich": "rich",
    "starlette": "starlette",
    "uvicorn": "uvicorn",
    "aiohttp": "aiohttp",
    "requests": "requests",
    "certifi": "certifi",
    "urllib3": "urllib3",
    "attrs": "attr",
    "click": "click",
    "typer": "typer",
}


def probe_and_split(pkg_root: Path, probe_python: Path, names):
    """探测一批依赖是否已装好，返回 (已装集合, 未装列表)。"""

    wanted = [name for name in names if name in IMPORT_NAMES]
    modules = sorted({IMPORT_NAMES[name] for name in wanted})
    result = probe_modules(pkg_root, probe_python, modules)
    installed = set()
    missing = []
    for name in names:
        module = IMPORT_NAMES.get(name)
        if module is not None and result.get(module):
            installed.add(name)
        else:
            missing.append(name)
    return installed, missing


def installed_requirements(pkg_root: Path, probe_python: Path) -> set:
    """探测哪些依赖已经装好（按 import 名判断），用于幂等重跑。"""

    if not probe_python.is_file():
        return set()
    installed, _missing = probe_and_split(pkg_root, probe_python, REQUIREMENTS)
    return installed


def download_wheels(packager_python: Path, wheel_dir: Path, cache_dir: Path) -> None:
    """用打包机的 base python 把依赖下载成离线 wheel。"""

    wheel_dir.mkdir(parents=True, exist_ok=True)
    env = child_env(PIP_CACHE_DIR=str(cache_dir))
    base = [str(packager_python), "-m", "pip", "download", "--dest", str(wheel_dir)]
    code, _ = run(
        base + ["--only-binary=:all:"] + REQUIREMENTS, env=env, allow_fail=True
    )
    if code == 0:
        return
    cout("  仅下载二进制 wheel 失败，改用允许源码分发的下载方式重试……")
    code, output = run(base + REQUIREMENTS, env=env, allow_fail=True)
    if code != 0:
        fail(
            "依赖下载失败。请检查打包机网络，或先用 pip download 预下载 wheel 后"
            "用 --wheels 指定该目录。\n原始输出末尾：\n" + "\n".join(output.splitlines()[-15:])
        )


def wheel_dist_name(wheel: Path) -> str:
    """从 wheel 文件名取出规范化后的发行名，例如 mcp-1.2.0-py3-none-any.whl -> mcp。"""

    stem = wheel.name
    if stem.endswith(".whl"):
        stem = stem[:-4]
    parts = stem.split("-")
    if not parts:
        return ""
    return normalize(parts[0])


def installed_tops(output: str) -> list:
    """从 pip 输出里解析本次装上的顶层包，用于逐包定位失败原因。"""

    tops = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Successfully installed "):
            body = line[len("Successfully installed "):]
            for item in body.split():
                tops.append(item.rsplit("-", 1)[0])
    if tops:
        return tops
    # 兜底：pip 报错前会打印 "Installing collected packages: a, b"
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Installing collected packages:"):
            body = line[len("Installing collected packages:"):]
            return [item.strip() for item in body.split(",") if item.strip()]
    return []


def install_requirements(
    pkg_root: Path,
    packager_python: Path,
    wheel_dir: Path,
    cache_dir: Path,
    site_packages: Path,
) -> None:
    """把依赖装进包内 site-packages。

    pip 一律由打包机的 base python 执行（包内 python 没有 pip）；
    导入复核则由包内 python 执行，确保 import 真的走得通。

    离线安装必须让 pip 从 wheel 目录解析整棵依赖树（--no-index --find-links），
    否则像 pydantic 这样的包会丢掉 pydantic_core 之类的传递依赖，装出来是坏的。
    """

    env = child_env(PIP_CACHE_DIR=str(cache_dir))
    pip_base = [
        str(packager_python),
        "-m",
        "pip",
        "install",
        "--no-compile",
        "--no-warn-script-location",
        "--target",
        str(site_packages),
    ]
    probe_python = pkg_root / "python" / "python.exe"

    present = installed_requirements(pkg_root, probe_python)
    if present:
        cout("  已存在、跳过安装的依赖：" + ", ".join(sorted(present)))

    def wheel_index() -> dict:
        index = {}
        for wheel in sorted(wheel_dir.glob("*.whl")) if wheel_dir.is_dir() else []:
            index.setdefault(wheel_dist_name(wheel), wheel)
        return index

    to_install = [name for name in REQUIREMENTS if name not in present]
    available = wheel_index()
    missing_wheels = [name for name in to_install if normalize(name) not in available]
    if missing_wheels:
        cout(
            "  wheel 目录缺少 %d 个依赖，先补齐下载：%s"
            % (len(missing_wheels), ", ".join(missing_wheels))
        )
        download_wheels(packager_python, wheel_dir, cache_dir)
        available = wheel_index()

    failed = []
    failures = {}

    # 一、优先完全离线安装：让 pip 在 wheel 目录内解析全部依赖（含传递依赖）
    if to_install and any(normalize(name) in available for name in to_install):
        cout("  从本地 wheel 目录离线安装（pip 解析整棵依赖树）……")
        code, output = run(
            pip_base
            + ["--no-index", "--find-links", str(wheel_dir), "--upgrade"]
            + to_install,
            env=env,
            allow_fail=True,
        )
        if code == 0:
            cout("  离线安装成功，本次装上 %d 个包" % len(installed_tops(output)))
        else:
            cout("  离线整体解析失败，改为逐个定位失败包……")
            for name in to_install:
                wheel = available.get(normalize(name))
                if wheel is None:
                    continue
                code_one, output_one = run(
                    pip_base
                    + [
                        "--no-index",
                        "--find-links",
                        str(wheel_dir),
                        "--upgrade",
                        name,
                    ],
                    env=env,
                    allow_fail=True,
                )
                printed = installed_tops(output_one)
                if code_one == 0:
                    cout("  已安装 %s（离线，本次共装上 %d 个包）" % (name, len(printed)))
                else:
                    failures[name] = output_one
                    failed.append(name)
                    cout("  [告警] %s 离线安装失败" % name)

    # 二、仍未装上的依赖，退回到索引安装（可能联网）
    _, still_missing = probe_and_split(pkg_root, probe_python, to_install)
    if still_missing:
        cout("  以下依赖本地装不上，改从索引安装：%s" % ", ".join(still_missing))
        code, output = run(
            pip_base + ["--upgrade", "--only-binary=:all:"] + still_missing,
            env=env,
            allow_fail=True,
        )
        if code != 0:
            cout("  仅二进制安装失败，改为允许源码分发的回退安装……")
            code, output = run(
                pip_base + ["--upgrade"] + still_missing, env=env, allow_fail=True
            )
        if code != 0:
            for name in still_missing:
                failures[name] = output
                if name not in failed:
                    failed.append(name)

    if failed:
        cout("")
        cout("  [告警] 以下依赖未能安装：" + ", ".join(sorted(set(failed))))
        critical_missing = [name for name in CRITICAL_REQUIREMENTS if name in set(failed)]
        if critical_missing:
            for name in critical_missing:
                cout("    ---- %s 的安装输出 ----" % name)
                for line in failures.get(name, "").splitlines()[-15:]:
                    cout("    | " + line)
            fail(
                "关键依赖安装失败：%s。这些依赖缺失会导致 MCP 服务无法启动，已终止打包。"
                % ", ".join(critical_missing)
            )
        cout("  提示：pywin32 等平台相关包缺失通常不影响 MCP 主流程；")
        cout("        其余依赖会在下一步用包内 python 复核。")

    # pip 会给带 entry points 的包在 site-packages\bin 下留一批启动器，
    # 这些 .exe 内嵌打包机 python.exe 的绝对路径，必须清掉（MCP 路径用不到它们）。
    scripts_dir = site_packages / "bin"
    if scripts_dir.is_dir():
        shutil.rmtree(scripts_dir, ignore_errors=True)
        cout("  已移除 pip 生成的 site-packages\\bin（其中启动器内嵌打包机绝对路径）")

    # 复核：mcp / dashscope / dotenv 必须真的能被包内 python 导入
    check = probe_modules(pkg_root, probe_python, ["mcp", "dashscope", "dotenv", "anyio"])
    bad = [name for name, ok in check.items() if not ok]
    if bad:
        fail(
            "装完依赖后包内 python 仍无法导入：%s。\n"
            "请检查依赖是否被装到了包内目录：%s" % (", ".join(bad), site_packages)
        )


# ---------------------------------------------------------------------------
# 打包主流程
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="构建 concept-memory Windows 离线包（自带精简 Python 运行时）",
    )
    parser.add_argument(
        "--src",
        default=None,
        help="项目根目录（默认脚本所在目录，即 <项目根>\\deploy；脚本会自动定位 memory_system）",
    )
    parser.add_argument("--out", default=None, help="输出目录（默认 <项目根>\\deploy\\dist）")
    parser.add_argument(
        "--runtime",
        default=None,
        help="打包用 Python 3.11 安装目录（默认读环境变量 %s 或自动探测）" % RUNTIME_ENV_VAR,
    )
    parser.add_argument(
        "--version",
        default=None,
        help="版本号（默认取源码 __version__，并与 pyproject.toml 校验一致）",
    )
    parser.add_argument("--wheels", default=None, help="预下载的 wheel 目录（给了就不联网下载）")
    parser.add_argument("--keep-work", action="store_true", help="保留中间目录 _work 便于排查")
    args = parser.parse_args(argv)
    if args.src is None:
        # 规范要求默认取脚本上级目录；在 src 布局的仓库里该目录正是项目根的 deploy 子目录
        args.src = str(deploy_dir())
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    src = resolve_src_root(Path(args.src).resolve())
    runtime = resolve_runtime(args.runtime)
    # 默认输出到「项目根的 deploy\dist」：src 布局下项目根是 src 的上一级，否则就是 src 本身
    project_root = src.parent if src.name == "src" else src
    out = Path(args.out).resolve() if args.out else (project_root / "deploy" / "dist")
    wheels_arg = Path(args.wheels).resolve() if args.wheels else None
    version = resolve_version(args.version, src, project_root)

    total_steps = 10

    cout("=" * 68)
    cout("concept-memory 离线包构建")
    cout("=" * 68)
    cout("版本号  : %s" % version)
    cout("源码根  : %s" % src)
    cout("输出目录: %s" % out)
    cout("运行时  : %s" % runtime)
    cout("wheel源 : %s" % (wheels_arg if wheels_arg else "未指定（联网下载到 _work\\wheels）"))
    cout("保留中间目录: %s" % ("是" if args.keep_work else "否"))

    # --- 1) 校验 ---------------------------------------------------------
    step(1, total_steps, "校验打包机运行时与项目源码")
    packager_python = runtime / "python.exe"
    if not packager_python.is_file():
        fail(
            "打包机 Python 不存在：%s\n"
            "请用 --runtime 指定 Python 3.11 安装目录（该目录下应有 python.exe）。" % packager_python
        )
    if not (runtime / "Lib").is_dir():
        fail("运行时目录缺少 Lib\\：%s" % (runtime / "Lib"))
    if not (runtime / "DLLs").is_dir():
        fail(
            "运行时目录缺少 DLLs\\：%s\n"
            "DLLs 里有 _sqlite3.pyd，缺失会导致 sqlite3 不可用，不能继续。" % (runtime / "DLLs")
        )
    mcp_entry = src / "memory_system" / "mcp_server.py"
    if not mcp_entry.is_file():
        fail(
            "未找到 MCP 入口：%s\n"
            "请用 --src 指定直接包含 memory_system\\ 的目录"
            "（源码在 src\\memory_system 布局时，就传那个 src 目录）。" % mcp_entry
        )
    src_pkg = src / "memory_system"
    cout("  运行时检查通过：%s" % packager_python)
    code, output = run([packager_python, "-c", "import sys;print(sys.version)"], allow_fail=True)
    if code != 0:
        fail("无法用打包机 Python 执行代码，请确认它可正常运行。")
    code, _ = run([packager_python, "-m", "pip", "--version"], allow_fail=True)
    if code != 0:
        fail(
            "打包机 Python 里没有可用的 pip（python -m pip 失败）。\n"
            "打包需要 pip 来安装依赖；请先在该 Python 上安装 pip 后重试。"
        )
    cout("  源码检查通过：%s" % src_pkg)

    # --- 2) 组装 staging -------------------------------------------------
    step(2, total_steps, "组装 staging 目录")
    if out.exists() and not out.is_dir():
        fail("输出路径已存在且不是目录：%s" % out)
    work = out / "_work"
    if work.exists():
        cout("  清理上次的中间目录：%s" % work)
        shutil.rmtree(work, ignore_errors=True)
    staging = work / "staging"
    pkg_root = staging / PACKAGE_DIR_NAME
    (pkg_root / "python").mkdir(parents=True, exist_ok=True)
    (pkg_root / "src").mkdir(parents=True, exist_ok=True)
    (pkg_root / "install").mkdir(parents=True, exist_ok=True)
    (pkg_root / "bin").mkdir(parents=True, exist_ok=True)
    cout("  staging 就绪：%s" % pkg_root)

    # --- 3) 复制精简运行时 ------------------------------------------------
    step(3, total_steps, "复制精简 Python 运行时（排除 Lib\\site-packages）")
    pkg_python_dir = pkg_root / "python"
    copied_top = 0
    for name in RUNTIME_TOP_FILES:
        candidate = runtime / name
        if candidate.is_file():
            shutil.copy2(candidate, pkg_python_dir / name)
            copied_top += 1
        else:
            cout("  跳过不存在的运行时文件：%s" % name)
    if not (pkg_python_dir / "python.exe").is_file():
        fail("复制运行时失败：包内缺少 python.exe")
    dlls_files = copytree(runtime / "DLLs", pkg_python_dir / "DLLs")
    lib_files = copytree(runtime / "Lib", pkg_python_dir / "Lib", exclude_dirs=LIB_EXCLUDE_DIRS)
    libs_files = copytree(runtime / "libs", pkg_python_dir / "libs")
    cout(
        "  运行时已复制：顶层文件 %d、DLLs %d、Lib %d、libs %d"
        % (copied_top, dlls_files, lib_files, libs_files)
    )
    if not (pkg_python_dir / "Lib" / "sqlite3").is_dir():
        fail("精简运行时缺少 Lib\\sqlite3，sqlite3 是存储层必需，不能继续。")

    # --- 4) 安装依赖 -----------------------------------------------------
    step(4, total_steps, "安装依赖到包内 site-packages（用打包机 base python 执行 pip）")
    site_packages = pkg_python_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    wheel_dir = wheels_arg if wheels_arg is not None else (work / "wheels")
    pip_cache = work / "pip-cache"
    if wheels_arg is not None and not wheels_arg.is_dir():
        fail("--wheels 指定的目录不存在：%s" % wheels_arg)
    if wheels_arg is not None and not list(wheels_arg.glob("*.whl")):
        cout("  --wheels 目录里没有 .whl，将改为联网下载依赖 wheel。")
    install_requirements(pkg_root, packager_python, wheel_dir, pip_cache, site_packages)

    # --- 5) 复制源码 -----------------------------------------------------
    step(5, total_steps, "复制 memory_system 源码到包内 src")
    src_files = copytree(src_pkg, pkg_root / "src" / "memory_system")
    cout("  源码已复制：%d 个文件 -> %s" % (src_files, pkg_root / "src" / "memory_system"))
    if not (pkg_root / "src" / "memory_system" / "mcp_server.py").is_file():
        fail("源码复制不完整：包内缺少 memory_system\\mcp_server.py")

    # --- 6) 生成 .env.example --------------------------------------------
    step(6, total_steps, "生成 .env.example")
    (pkg_root / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    cout("  已写入 %s" % (pkg_root / ".env.example"))

    # --- 7) 生成启动脚本 --------------------------------------------------
    step(7, total_steps, "生成 bin 下的启动脚本")
    bin_dir = pkg_root / "bin"
    mcp_cmd = bin_dir / "memory-mcp.cmd"
    web_cmd = bin_dir / "memory-web.cmd"
    cli_cmd = bin_dir / "memory-concepts.cmd"
    # newline="" 必须给：默认文本模式会把我们已经写好的 \r\n 再翻译一次，
    # 在 Windows 上会变成 \r\r\n，批处理解析出错。
    mcp_cmd.write_text(MCP_CMD.replace("\n", "\r\n"), encoding="ascii", newline="")
    web_cmd.write_text(WEB_CMD.replace("\n", "\r\n"), encoding="ascii", newline="")
    cli_cmd.write_text(CLI_CMD.replace("\n", "\r\n"), encoding="ascii", newline="")
    cout("  已写入 %s" % mcp_cmd)
    cout("  已写入 %s" % web_cmd)
    cout("  已写入 %s" % cli_cmd)

    # --- 8) 复制安装说明 --------------------------------------------------
    step(8, total_steps, "复制安装说明与 MCP 配置示例")
    deploy_path = deploy_dir()
    install_src = deploy_path / "INSTALL.md"
    examples_src = deploy_path / "mcp-config-examples"
    checker_src = deploy_path / "check_install.ps1"
    rules_src = deploy_path / "agent-rules-template.md"
    changelog_src = project_root / "CHANGELOG.md"
    missing = []
    if not install_src.is_file():
        missing.append(str(install_src))
    if not examples_src.is_dir():
        missing.append(str(examples_src))
    if not checker_src.is_file():
        missing.append(str(checker_src))
    if not rules_src.is_file():
        missing.append(str(rules_src))
    if not changelog_src.is_file():
        missing.append(str(changelog_src))
    if missing:
        fail(
            "安装说明类文件缺失，请先补齐后再打包：\n  - "
            + "\n  - ".join(missing)
            + "\n（INSTALL.md / check_install.ps1 / mcp-config-examples\\ 由另一条流水线产出，缺了就不该出包。）"
        )
    shutil.copy2(install_src, pkg_root / "install" / "INSTALL.md")
    ensure_utf8_bom(checker_src, pkg_root / "install" / "check_install.ps1")
    shutil.copy2(rules_src, pkg_root / "install" / "agent-rules-template.md")
    shutil.copy2(changelog_src, pkg_root / "CHANGELOG.md")
    (pkg_root / "VERSION").write_text(version + "\n", encoding="utf-8")
    example_count = copytree(examples_src, pkg_root / "install" / "mcp-config-examples")
    cout(
        "  已复制 INSTALL.md、agent-rules-template.md、check_install.ps1（已补 UTF-8 BOM）"
        "与 %d 个配置示例文件" % example_count
    )
    cout("  已写入 VERSION（%s）与 CHANGELOG.md" % version)

    # --- 自检（打包前必须通过） ------------------------------------------
    cout("")
    cout("[自检] 用包内 python 验证解释器、sqlite3 与关键依赖")
    selfcheck(pkg_root, pkg_python_dir / "python.exe")

    # --- 清理字节码（自检可能已生成，兜底再清一次） ----------------------
    pruned = prune_bytecode(pkg_root)
    if pruned:
        cout("  已清理字节码文件：%d 个" % pruned)

    # --- 9) 打 zip --------------------------------------------------------
    step(9, total_steps, "打包 zip")
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / ZIP_NAME_TEMPLATE.format(version=version)
    if zip_path.exists():
        cout("  覆盖已存在的 zip：%s" % zip_path)
        zip_path.unlink()
    written = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(staging):
            dirs.sort()
            for name in sorted(files):
                full = Path(root) / name
                rel = full.relative_to(staging)
                zf.write(full, rel.as_posix())
                written += 1
    if written == 0:
        fail("zip 内没有任何文件，打包失败。")
    cout("  已写入 %d 个文件" % written)

    # --- 10) 汇总 ---------------------------------------------------------
    step(10, total_steps, "汇总")
    zip_size = zip_path.stat().st_size
    raw_size = dir_size(staging)
    files_in_pkg = count_files(pkg_root)
    cout("")
    cout("=" * 68)
    cout("构建完成")
    cout("=" * 68)
    cout("版本号            : %s" % version)
    cout("zip 绝对路径      : %s" % zip_path.resolve())
    cout("zip 体积          : %s" % human_size(zip_size))
    cout("解压后体积        : %s" % human_size(raw_size))
    cout("包内文件数        : %d" % files_in_pkg)
    cout("")
    cout("解压后请先自检（PowerShell 里执行，<包根> 换成解压后的实际路径）：")
    cout("  powershell -NoProfile -ExecutionPolicy Bypass -File \"<包根>\\install\\check_install.ps1\"")
    cout("")
    cout("启动方式：")
    cout("  MCP（stdio，给客户端调用）: <包根>\\bin\\memory-mcp.cmd")
    cout("  概念网络页面              : <包根>\\bin\\memory-web.cmd")
    cout("  命令行建索引              : <包根>\\bin\\memory-concepts.cmd")
    cout("安装步骤见包内 install\\INSTALL.md")

    if args.keep_work:
        cout("")
        cout("中间目录已保留：%s" % work)
    else:
        shutil.rmtree(work, ignore_errors=True)
        cout("")
        cout("中间目录已清理：%s" % work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
