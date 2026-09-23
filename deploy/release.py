#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""concept-memory 发布脚本：一条命令走完「校验 → 测试 → 构建 → 包验收 → 打 tag → 发 Release」。

用法（在仓库根目录执行，用任意可用的 Python）：

    python deploy/release.py --version 0.1.0 --dry-run   # 只出产物+校验和，不碰 git、不发网络请求
    python deploy/release.py --version 0.1.0             # 真发布，需要环境变量 GITHUB_TOKEN

参数：
    --version     必填，形如 0.1.0；必须与 pyproject.toml 与源码 __version__ 三处一致；
    --dry-run     只做到「产物 + 校验和」，并打印后续将要执行的 git / 网络动作；
    --skip-tests  跳过 pytest（仅在你已单独跑过测试时使用）；
    --allow-dirty 允许工作树有未提交改动（默认拒绝，避免发出与仓库不一致的包）；
    --no-upload   打 tag 并推送，但不创建 Release、不上传资产；
    --create-repo origin 不存在时，用令牌创建公开仓库 <账号>/<repo-name>；
    --repo-name   配合 --create-repo 的仓库名，默认 concept-memory；
    --notes-file  自定义 Release 说明文件（默认从 CHANGELOG.md 提取该版本段）。

硬约束：
  - 令牌只从环境变量 GITHUB_TOKEN 读，绝不写入文件、绝不打印；
  - 失败即停：打 tag 之前的所有关卡先全部通过，避免留下半成品 tag；
  - Release 说明一律来自 CHANGELOG.md 的对应版本段，缺了就报错，不自动编造。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
HEADING_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"
HTTP_TIMEOUT = 300.0

# 让中文进度在默认 GBK 控制台与重定向到文件时都不乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def cout(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def fail(message: str, code: int = 1):
    sys.stderr.write("\n[失败] " + message + "\n")
    sys.stderr.flush()
    raise SystemExit(code)


def step(index: int, total: int, title: str) -> None:
    cout("")
    cout("[%d/%d] %s" % (index, total, title))


def pretty(command) -> str:
    return " ".join(str(part) for part in command)


def run(command, allow_fail: bool = False, cwd: Path | None = None):
    """执行外部命令，返回 (returncode, stdout+stderr)。"""

    cout("  $ " + pretty(command))
    try:
        proc = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(os.environ),
        )
    except OSError as exc:
        if allow_fail:
            return 1, str(exc)
        fail("无法执行命令：%s\n原因：%s" % (pretty(command), exc))
    output = proc.stdout.decode("utf-8", errors="replace")
    if output.strip():
        for line in output.rstrip().splitlines():
            cout("    | " + line)
    if proc.returncode != 0 and not allow_fail:
        fail("命令执行失败（退出码 %d）：%s" % (proc.returncode, pretty(command)))
    return proc.returncode, output


# ---------------------------------------------------------------------------
# 路径与依赖
# ---------------------------------------------------------------------------


def deploy_dir() -> Path:
    return Path(__file__).resolve().parent


REPO_ROOT = deploy_dir().parent
SRC_DIR = REPO_ROOT / "src"
DIST_DIR = REPO_ROOT / "deploy" / "dist"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"


def load_build_module():
    """加载同目录的构建脚本，复用它的版本号读取逻辑。"""

    path = deploy_dir() / "build_offline_bundle.py"
    spec = importlib.util.spec_from_file_location("concept_memory_build", path)
    if spec is None or spec.loader is None:
        fail("找不到构建脚本：%s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_version(version: str) -> None:
    if not SEMVER.match(version):
        fail("--version 必须是 X.Y.Z 形式，收到：%s" % version)


def check_version_consistency(build_module, version: str) -> None:
    declared = build_module.read_package_version(SRC_DIR)
    pyproject_version = build_module.read_pyproject_version(REPO_ROOT)
    mismatch = []
    if declared != version:
        mismatch.append("src\\memory_system\\__init__.py 是 %s" % declared)
    if pyproject_version != version:
        mismatch.append("pyproject.toml 是 %s" % pyproject_version)
    if mismatch:
        fail(
            "--version %s 与仓库声明不一致：\n  - %s\n"
            "请先把三处改成同一个版本号（发布不允许版本号漂移）。"
            % (version, "\n  - ".join(mismatch))
        )


def read_release_notes(version: str, notes_file: str | None) -> str:
    """取 Release 说明：优先 --notes-file，否则从 CHANGELOG.md 提取该版本段。"""

    if notes_file:
        path = Path(notes_file).resolve()
        if not path.is_file():
            fail("--notes-file 指定的文件不存在：%s" % path)
        return path.read_text(encoding="utf-8").strip()

    if not CHANGELOG_PATH.is_file():
        fail("找不到 CHANGELOG.md：%s" % CHANGELOG_PATH)
    lines = CHANGELOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()

    start = None
    heading = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and ("[%s]" % version) in stripped:
            start = index
            heading = stripped
            break
    if start is None:
        fail(
            "CHANGELOG.md 里找不到版本段 '## [%s] - YYYY-MM-DD'。\n"
            "发布前先把 CHANGELOG.md 的 Unreleased 内容整理到该版本段并写上日期。" % version
        )
    if not HEADING_DATE.search(heading or ""):
        fail(
            "CHANGELOG.md 的版本段缺日期：%s\n"
            "请写成 '## [%s] - 2026-09-23' 这种带日期的形式。" % (heading, version)
        )

    collected: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip().startswith("## "):
            break
        collected.append(line)
    notes = "\n".join(collected).strip()
    if not notes:
        fail("CHANGELOG.md 里 [%s] 段落是空的，没法当 Release 说明。" % version)
    return notes


def git(*args: str, allow_fail: bool = False):
    return run(["git", *args], allow_fail=allow_fail, cwd=REPO_ROOT)


def ensure_clean_tree(allow_dirty: bool) -> None:
    code, output = git("status", "--porcelain")
    if code != 0:
        fail("无法读取 git 状态，请确认当前目录是 git 仓库。")
    if output.strip() and not allow_dirty:
        fail(
            "工作树有未提交改动，先提交或 stash 后再发布（否则发布的包与仓库内容不一致）：\n"
            + output.strip()
            + "\n确实要带着未提交改动发布时加 --allow-dirty。"
        )
    if output.strip():
        cout("  [警告] 工作树不干净，已按 --allow-dirty 继续。")


def ensure_tag_absent(version: str) -> None:
    code, _ = git("rev-parse", "-q", "--verify", "refs/tags/v%s" % version, allow_fail=True)
    if code == 0:
        fail(
            "tag v%s 已存在。升版本号再发，或先删除：git tag -d v%s && git push origin :refs/tags/v%s"
            % (version, version, version)
        )
    cout("  tag v%s 尚未占用。" % version)


def resolve_test_python() -> str:
    """找一个能跑 pytest 的解释器：当前解释器 > 仓库 .venv。"""

    candidates = [sys.executable, str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if candidate != sys.executable and not Path(candidate).is_file():
            continue
        code, _ = run([candidate, "-m", "pytest", "--version"], allow_fail=True)
        if code == 0:
            return candidate
    fail(
        "找不到带 pytest 的解释器。先在仓库里建开发环境：\n"
        "  py -3.11 -m venv .venv\n"
        "  .venv\\Scripts\\python.exe -m pip install -e \".[dev,mcp,dashscope]\""
    )


def push_branch() -> None:
    """确保当前分支已推到 origin（首次发布顺带建立上游跟踪）。"""

    code, output = git("rev-parse", "--abbrev-ref", "HEAD")
    branch = ""
    if output.strip():
        branch = output.strip().splitlines()[-1].strip()
    if code != 0 or not branch or branch == "HEAD":
        fail("当前不在任何分支上（可能是 detached HEAD），请先切回分支再发布。")
    push_code, _ = git("push", "-u", "origin", branch, allow_fail=True)
    if push_code != 0:
        fail("推送分支 %s 失败，请检查远端仓库与令牌权限。" % branch)
    cout("  分支 %s 已推送。" % branch)


# ---------------------------------------------------------------------------
# 构建与验收
# ---------------------------------------------------------------------------


def build_bundle(version: str, python_exe: str) -> Path:
    build_script = deploy_dir() / "build_offline_bundle.py"
    code, _ = run(
        [python_exe, str(build_script), "--out", str(DIST_DIR), "--version", version],
    )
    if code != 0:
        fail("构建离线包失败。")
    zip_path = DIST_DIR / ("concept-memory-offline-win64-v%s.zip" % version)
    if not zip_path.is_file():
        fail("构建脚本没有产出预期文件：%s" % zip_path)
    return zip_path


def verify_bundle(zip_path: Path, python_exe: str) -> None:
    verify_script = deploy_dir() / "verify_offline_bundle.py"
    extract_dir = DIST_DIR / "_verify"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    code, _ = run([python_exe, str(verify_script), str(zip_path), str(extract_dir)])
    shutil.rmtree(extract_dir, ignore_errors=True)
    if code != 0:
        fail("离线包验收失败：解压后 MCP 握手没有通过，不要发布这个包。")


def write_checksum(zip_path: Path) -> Path:
    digest = hashlib.sha256()
    with zip_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    checksum_path.write_text("%s  %s\n" % (digest.hexdigest(), zip_path.name), encoding="ascii")
    cout("  已写入校验和：%s" % checksum_path.name)
    return checksum_path


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def require_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        fail(
            "缺少环境变量 GITHUB_TOKEN。\n"
            "请创建一个具备 repo 权限的 GitHub 令牌，只放进当前 shell 的环境变量（不要写进文件）：\n"
            "  PowerShell: $env:GITHUB_TOKEN = \"ghp_xxx\"   然后重跑本脚本"
        )
    return token


def api_request(method: str, url: str, token: str, payload=None, body: bytes | None = None,
                content_type: str | None = None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    elif body is not None:
        data = body

    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "concept-memory-release")
    if content_type:
        request.add_header("Content-Type", content_type)

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        fail("GitHub 接口返回 %s：%s\n%s" % (exc.code, url, detail[:800]))
    except urllib.error.URLError as exc:
        fail("访问 GitHub 失败（网络或代理问题）：%s\n%s" % (url, exc.reason))
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def parse_remote_slug(url: str) -> tuple[str, str] | None:
    """把 origin URL 解析成 (owner, repo)。"""

    text = url.strip()
    patterns = (
        r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group(1), match.group(2)
    return None


def resolve_remote(token: str, create_repo: bool, repo_name: str) -> tuple[str, str]:
    code, output = git("remote", "get-url", "origin", allow_fail=True)
    if code == 0 and output.strip():
        slug = parse_remote_slug(output.strip().splitlines()[0].strip())
        if slug is None:
            fail(
                "origin 不是 GitHub 仓库地址，本脚本只支持 GitHub：%s\n"
                "请改成 GitHub 地址，或先 git remote remove origin 再重试。" % output.strip()
            )
        cout("  远端仓库：%s/%s" % slug)
        return slug

    if not create_repo:
        fail(
            "当前仓库没有 origin 远端。\n"
            "要么先自己建好仓库并 git remote add origin <地址>，要么加 --create-repo 让脚本用令牌创建。"
        )

    account = api_request("GET", GITHUB_API + "/user", token)
    owner = account.get("login")
    if not owner:
        fail("无法从令牌读出账号名，请检查令牌权限。")
    cout("  令牌账号：%s，准备创建公开仓库 %s" % (owner, repo_name))
    created = api_request(
        "POST",
        GITHUB_API + "/user/repos",
        token,
        payload={
            "name": repo_name,
            "private": False,
            "description": "Local concept memory MCP service for coding agents",
            "has_issues": True,
            "has_wiki": False,
        },
    )
    clone_url = created.get("clone_url") or ("https://github.com/%s/%s.git" % (owner, repo_name))
    git("remote", "add", "origin", clone_url)
    cout("  已添加 origin：%s" % clone_url)
    return owner, repo_name


def create_release(token: str, slug: tuple[str, str], version: str, notes: str) -> dict:
    owner, repo = slug
    tag = "v%s" % version
    payload = {
        "tag_name": tag,
        "name": tag,
        "body": notes,
        "draft": False,
        "prerelease": False,
    }
    release = api_request("POST", "%s/repos/%s/%s/releases" % (GITHUB_API, owner, repo), token, payload=payload)
    cout("  已创建 Release：%s" % release.get("html_url", tag))
    return release


def upload_asset(token: str, slug: tuple[str, str], release_id: int, file_path: Path) -> None:
    owner, repo = slug
    body = file_path.read_bytes()
    url = "%s/repos/%s/%s/releases/%s/assets?name=%s" % (
        GITHUB_UPLOADS,
        owner,
        repo,
        release_id,
        urllib.parse.quote(file_path.name),
    )
    asset = api_request(
        "POST", url, token, body=body, content_type="application/zip" if file_path.suffix == ".zip" else "text/plain"
    )
    cout("  已上传：%s（%s bytes）" % (asset.get("name", file_path.name), asset.get("size", len(body))))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="concept-memory 发布：构建离线包并发布到 GitHub Release")
    parser.add_argument("--version", required=True, help="版本号，形如 0.1.0（必须与仓库声明一致）")
    parser.add_argument("--dry-run", action="store_true", help="只出产物与校验和，不碰 git、不发网络请求")
    parser.add_argument("--skip-tests", action="store_true", help="跳过 pytest")
    parser.add_argument("--allow-dirty", action="store_true", help="允许工作树有未提交改动")
    parser.add_argument("--no-upload", action="store_true", help="打 tag 并推送，但不创建 Release")
    parser.add_argument("--create-repo", action="store_true", help="origin 不存在时用令牌创建公开仓库")
    parser.add_argument("--repo-name", default="concept-memory", help="配合 --create-repo 的仓库名")
    parser.add_argument("--notes-file", default=None, help="自定义 Release 说明文件（默认从 CHANGELOG.md 提取）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    total_steps = 6

    cout("=" * 68)
    cout("concept-memory 发布")
    cout("=" * 68)
    cout("版本号  : %s" % args.version)
    cout("模式    : %s" % ("dry-run（不出网、不打 tag）" if args.dry_run else "正式发布"))
    cout("仓库根  : %s" % REPO_ROOT)

    validate_version(args.version)
    build_module = load_build_module()

    # --- 1) 版本与说明校验 ------------------------------------------------
    step(1, total_steps, "校验版本号与 Release 说明")
    check_version_consistency(build_module, args.version)
    notes = read_release_notes(args.version, args.notes_file)
    cout("  CHANGELOG 段落已取到：%d 个字符" % len(notes))

    # --- 2) 工作树与 tag 状态 --------------------------------------------
    step(2, total_steps, "检查工作树与 tag 状态")
    if args.dry_run:
        cout("  dry-run：跳过工作树与 tag 检查。")
    else:
        ensure_clean_tree(args.allow_dirty)
        ensure_tag_absent(args.version)

    # --- 3) 测试 ----------------------------------------------------------
    step(3, total_steps, "跑测试")
    test_python = resolve_test_python()
    if args.skip_tests:
        cout("  已按 --skip-tests 跳过测试（风险自负）。")
    else:
        run([test_python, "-m", "pytest", "-q"], cwd=REPO_ROOT)
        cout("  测试通过。")

    # --- 4) 构建离线包 ----------------------------------------------------
    step(4, total_steps, "构建离线包")
    zip_path = build_bundle(args.version, test_python)

    # --- 5) 包验收 + 校验和 ----------------------------------------------
    step(5, total_steps, "验收离线包并生成校验和")
    verify_bundle(zip_path, test_python)
    checksum_path = write_checksum(zip_path)

    # --- 6) 发布 ----------------------------------------------------------
    step(6, total_steps, "打 tag 与发布")
    if args.dry_run:
        cout("  dry-run：以下动作不会执行，正式发布时会按顺序跑——")
        cout("    git push -u origin <当前分支>（首次发布同时建立上游跟踪）")
        cout("    git tag -a v%s -m \"concept-memory v%s\"" % (args.version, args.version))
        cout("    git push origin v%s" % args.version)
        cout("    POST %s/repos/<owner>/<repo>/releases（用 CHANGELOG 段落当说明）" % GITHUB_API)
        cout("    POST 上传 %s 与 %s" % (zip_path.name, checksum_path.name))
        cout("")
        cout("  产物：%s" % zip_path.resolve())
        cout("  校验和：%s" % checksum_path.resolve())
        return 0

    token = require_token()
    slug = resolve_remote(token, args.create_repo, args.repo_name)
    push_branch()
    git("tag", "-a", "v%s" % args.version, "-m", "concept-memory v%s" % args.version)
    push_code, _ = git("push", "origin", "v%s" % args.version, allow_fail=True)
    if push_code != 0:
        fail(
            "推送 tag 失败。本地 tag 已经建好，修好远端后可以手动重推：\n"
            "  git push origin v%s\n"
            "确实不想保留这个 tag 时：git tag -d v%s" % (args.version, args.version)
        )

    if args.no_upload:
        cout("  已按 --no-upload 停在推送 tag 这一步，没有创建 Release。")
        cout("")
        cout("  产物：%s" % zip_path.resolve())
        cout("  校验和：%s" % checksum_path.resolve())
        return 0

    release = create_release(token, slug, args.version, notes)
    release_id = release.get("id")
    if not release_id:
        fail("创建 Release 成功但拿不到 id，无法上传资产；请到网页手动补传。")
    upload_asset(token, slug, release_id, zip_path)
    upload_asset(token, slug, release_id, checksum_path)

    cout("")
    cout("=" * 68)
    cout("发布完成")
    cout("=" * 68)
    cout("Release 页面：%s" % release.get("html_url", "（见 GitHub Releases）"))
    cout("用户安装：下载 %s，解压后把包内 install\\INSTALL.md 交给自己的 agent。" % zip_path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
