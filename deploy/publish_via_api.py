#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通过 GitHub 官方接口把当前 HEAD 推到远端并创建 Release。

用途：有些网络环境下 `git push`（github.com:443）不通，但 api.github.com 与
uploads.github.com 正常。这个脚本用 Git Data API 复刻一次推送：
blobs → tree → commit → refs/heads、refs/tags，然后再建 Release 并上传资产。

用法（在仓库根目录执行）：

    set GITHUB_TOKEN=ghp_xxx            # PowerShell: $env:GITHUB_TOKEN = "ghp_xxx"
    python deploy/publish_via_api.py --version 0.1.0

参数：
    --version    必填，tag 用 v<版本>；资产默认在 deploy/dist 下按该版本号查找；
    --repo       远端仓库名，默认从 origin 推导，推不出就用 concept-memory；
    --owner      仓库归属账号，默认用令牌对应账号；
    --create-repo 仓库不存在时创建为公开仓库；
    --branch     默认 main；
    --dry-run    只打印将要调用的接口，不发任何写请求；
    --skip-release 只推代码与 tag，不创建 Release、不上传资产。

硬约束：
  - 令牌只从环境变量 GITHUB_TOKEN 读取，绝不写入文件、绝不打印；
  - 资产文件缺失时直接失败，不创建只有 tag 的空 Release。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"
HTTP_TIMEOUT = 300.0

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


REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "deploy" / "dist"


def git_bytes(*args: str) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        fail(
            "git %s 失败：\n%s"
            % (" ".join(args), proc.stderr.decode("utf-8", errors="replace").strip())
        )
    return proc.stdout


def git_text(*args: str) -> str:
    return git_bytes(*args).decode("utf-8", errors="replace")


def api(method: str, url: str, token: str, payload=None, body: bytes | None = None,
        content_type: str | None = None, allow_fail: bool = False):
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
    request.add_header("User-Agent", "concept-memory-publish")
    if content_type:
        request.add_header("Content-Type", content_type)

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if allow_fail:
            return exc.code, detail
        fail("GitHub 接口返回 %s：%s\n%s" % (exc.code, url, detail[:600]))
    except urllib.error.URLError as exc:
        fail("访问 GitHub 失败（网络或代理问题）：%s\n%s" % (url, exc.reason))
    if not raw:
        return 200, {}
    return 200, json.loads(raw.decode("utf-8"))


def parse_remote_slug(url: str) -> tuple[str, str] | None:
    import re

    text = url.strip()
    patterns = (
        r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group(1), match.group(2)
    return None


def resolve_owner_and_repo(token: str, owner_arg: str | None, repo_arg: str | None):
    if owner_arg and repo_arg:
        return owner_arg, repo_arg

    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    slug = None
    if proc.returncode == 0:
        slug = parse_remote_slug(proc.stdout.decode("utf-8", errors="replace").strip())

    owner = owner_arg or (slug[0] if slug else None)
    repo = repo_arg or (slug[1] if slug else "concept-memory")
    if not owner:
        _, account = api("GET", GITHUB_API + "/user", token)
        owner = account.get("login")
        if not owner:
            fail("无法确定仓库归属账号，请用 --owner 指定。")
    cout("  目标仓库：%s/%s" % (owner, repo))
    return owner, repo


def ensure_repo(token: str, owner: str, repo: str, create: bool) -> None:
    status, body = api("GET", "%s/repos/%s/%s" % (GITHUB_API, owner, repo), token, allow_fail=True)
    if status == 200:
        cout("  仓库已存在。")
        return
    if status != 404:
        fail("检查仓库时返回 %s：%s" % (status, str(body)[:300]))
    if not create:
        fail("仓库 %s/%s 不存在。加 --create-repo 让脚本创建，或先去网页建好。" % (owner, repo))
    _, created = api(
        "POST",
        GITHUB_API + "/user/repos",
        token,
        payload={
            "name": repo,
            "private": False,
            "description": "Local concept memory MCP service for coding agents",
            "has_issues": True,
            "has_wiki": False,
        },
    )
    cout("  已创建公开仓库：%s" % created.get("html_url", "%s/%s" % (owner, repo)))


def local_tree_entries():
    """读取 HEAD 的全部文件条目（path/mode/blob sha）。"""

    output = git_text("ls-tree", "-r", "HEAD")
    entries = []
    for line in output.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        mode, kind, sha = meta.split()
        if kind != "blob":
            continue
        entries.append({"path": path, "mode": mode, "sha": sha})
    if not entries:
        fail("HEAD 里没有任何文件，无法发布。")
    return entries


def ensure_initial_commit(token: str, owner: str, repo: str, branch: str) -> None:
    """空仓库不能直接写 Git Data 接口（会返回 409），先建一次初始提交。"""

    status, _ = api(
        "GET", "%s/repos/%s/%s/git/ref/heads/%s" % (GITHUB_API, owner, repo, branch), token,
        allow_fail=True,
    )
    if status == 200:
        return
    placeholder = (
        "# concept-memory\n\n"
        "这是发布流程自动创建的占位提交，紧接着会被完整的项目内容覆盖。\n"
    )
    api(
        "PUT",
        "%s/repos/%s/%s/contents/README.md" % (GITHUB_API, owner, repo),
        token,
        payload={
            "message": "chore: initialize repository",
            "content": base64.b64encode(placeholder.encode("utf-8")).decode("ascii"),
            "branch": branch,
        },
    )
    cout("  空仓库：已创建初始提交（随后被完整内容覆盖）")


def upload_tree(token: str, owner: str, repo: str):
    entries = local_tree_entries()
    cout("  本地文件数：%d，开始上传 blob" % len(entries))
    tree = []
    for index, entry in enumerate(entries, start=1):
        content = git_bytes("cat-file", "-p", entry["sha"])
        _, blob = api(
            "POST",
            "%s/repos/%s/%s/git/blobs" % (GITHUB_API, owner, repo),
            token,
            payload={
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            },
        )
        tree.append(
            {
                "path": entry["path"],
                "mode": entry["mode"],
                "type": "blob",
                "sha": blob["sha"],
            }
        )
        if index % 10 == 0 or index == len(entries):
            cout("    已上传 %d/%d" % (index, len(entries)))
    _, created_tree = api(
        "POST",
        "%s/repos/%s/%s/git/trees" % (GITHUB_API, owner, repo),
        token,
        payload={"tree": tree},
    )
    cout("  已创建 tree：%s" % created_tree["sha"])
    return created_tree["sha"]


def create_commit(token: str, owner: str, repo: str, tree_sha: str, branch: str) -> str:
    message = git_text("log", "-1", "--format=%B").strip()
    author_name = git_text("log", "-1", "--format=%an").strip()
    author_email = git_text("log", "-1", "--format=%ae").strip()

    parents = []
    status, body = api(
        "GET", "%s/repos/%s/%s/git/ref/heads/%s" % (GITHUB_API, owner, repo, branch), token,
        allow_fail=True,
    )
    if status == 200 and isinstance(body, dict):
        parents = [body["object"]["sha"]]
        cout("  远端分支已存在，作为父提交：%s" % parents[0][:12])

    _, commit = api(
        "POST",
        "%s/repos/%s/%s/git/commits" % (GITHUB_API, owner, repo),
        token,
        payload={
            "message": message,
            "tree": tree_sha,
            "parents": parents,
            "author": {"name": author_name, "email": author_email},
            "committer": {"name": author_name, "email": author_email},
        },
    )
    cout("  已创建 commit：%s" % commit["sha"][:12])
    return commit["sha"]


def update_branch(token: str, owner: str, repo: str, branch: str, commit_sha: str) -> None:
    ref = "refs/heads/%s" % branch
    status, _ = api(
        "GET", "%s/repos/%s/%s/git/ref/heads/%s" % (GITHUB_API, owner, repo, branch), token,
        allow_fail=True,
    )
    if status == 200:
        api(
            "PATCH",
            "%s/repos/%s/%s/git/refs/heads/%s" % (GITHUB_API, owner, repo, branch),
            token,
            payload={"sha": commit_sha, "force": False},
        )
    else:
        api(
            "POST",
            "%s/repos/%s/%s/git/refs" % (GITHUB_API, owner, repo),
            token,
            payload={"ref": ref, "sha": commit_sha},
        )
    cout("  分支 %s 已指向 %s" % (branch, commit_sha[:12]))


def create_tag(token: str, owner: str, repo: str, version: str, commit_sha: str) -> None:
    tag = "v%s" % version
    status, _ = api(
        "GET", "%s/repos/%s/%s/git/ref/tags/%s" % (GITHUB_API, owner, repo, tag), token,
        allow_fail=True,
    )
    if status == 200:
        fail("远端已存在 tag %s。升版本号再发，或先到网页删掉它。" % tag)
    _, tag_object = api(
        "POST",
        "%s/repos/%s/%s/git/tags" % (GITHUB_API, owner, repo),
        token,
        payload={
            "tag": tag,
            "message": "concept-memory %s" % tag,
            "object": commit_sha,
            "type": "commit",
        },
    )
    api(
        "POST",
        "%s/repos/%s/%s/git/refs" % (GITHUB_API, owner, repo),
        token,
        payload={"ref": "refs/tags/%s" % tag, "sha": tag_object["sha"]},
    )
    cout("  已创建 tag：%s" % tag)


def release_notes(version: str) -> str:
    path = REPO_ROOT / "CHANGELOG.md"
    if not path.is_file():
        fail("找不到 CHANGELOG.md，无法生成 Release 说明。")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith("## ") and ("[%s]" % version) in line:
            start = index
            break
    if start is None:
        fail("CHANGELOG.md 里找不到 [%s] 版本段。" % version)
    collected = []
    for line in lines[start + 1 :]:
        if line.strip().startswith("## "):
            break
        collected.append(line)
    notes = "\n".join(collected).strip()
    if not notes:
        fail("CHANGELOG.md 的 [%s] 段落是空的。" % version)
    return notes


def create_release(token: str, owner: str, repo: str, version: str):
    tag = "v%s" % version
    status, body = api(
        "POST",
        "%s/repos/%s/%s/releases" % (GITHUB_API, owner, repo),
        token,
        payload={
            "tag_name": tag,
            "name": tag,
            "body": release_notes(version),
            "draft": False,
            "prerelease": False,
        },
        allow_fail=True,
    )
    if status != 200 and status != 201:
        fail("创建 Release 失败（%s）：%s" % (status, str(body)[:400]))
    cout("  已创建 Release：%s" % body.get("html_url", tag))
    return body


def upload_assets(token: str, owner: str, repo: str, release_id: int, version: str) -> None:
    base = "concept-memory-offline-win64-v%s.zip" % version
    assets = [DIST_DIR / base, DIST_DIR / (base + ".sha256")]
    for asset in assets:
        if not asset.is_file():
            fail("资产文件不存在：%s\n先跑 deploy/release.py --dry-run 生成产物。" % asset)
    for asset in assets:
        content_type = "application/zip" if asset.suffix == ".zip" else "text/plain"
        url = "%s/repos/%s/%s/releases/%s/assets?name=%s" % (
            GITHUB_UPLOADS,
            owner,
            repo,
            release_id,
            urllib.parse.quote(asset.name),
        )
        _, result = api(
            "POST", url, token, body=asset.read_bytes(), content_type=content_type
        )
        cout("  已上传 %s（%s bytes）" % (result.get("name", asset.name), result.get("size", 0)))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="用 GitHub 官方接口把 HEAD 推到远端并创建 Release（git push 不通时的备用通道）"
    )
    parser.add_argument("--version", required=True, help="版本号，形如 0.1.0（tag 为 v<版本>）")
    parser.add_argument("--owner", default=None, help="仓库归属账号（默认取令牌账号或 origin）")
    parser.add_argument("--repo", default=None, help="仓库名（默认取 origin 或 concept-memory）")
    parser.add_argument("--create-repo", action="store_true", help="仓库不存在时创建为公开仓库")
    parser.add_argument("--branch", default="main", help="目标分支，默认 main")
    parser.add_argument("--skip-release", action="store_true", help="只推代码与 tag，不建 Release")
    parser.add_argument("--dry-run", action="store_true", help="只打印将调用的接口，不发写请求")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    total_steps = 4

    cout("=" * 68)
    cout("concept-memory：经 GitHub 接口发布")
    cout("=" * 68)
    cout("版本号  : %s" % args.version)
    cout("模式    : %s" % ("dry-run" if args.dry_run else "正式发布"))

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        fail(
            "缺少环境变量 GITHUB_TOKEN。\n"
            "  PowerShell: $env:GITHUB_TOKEN = \"ghp_xxx\"   然后重跑本脚本"
        )

    owner, repo = resolve_owner_and_repo(token, args.owner, args.repo)
    commit_sha = git_text("rev-parse", "HEAD").strip()
    cout("本地 HEAD：%s" % commit_sha[:12])

    if args.dry_run:
        step(1, total_steps, "dry-run：将要执行的接口调用")
        cout("  GET  /repos/%s/%s" % (owner, repo))
        cout("  POST /repos/%s/%s/git/blobs   × 本地文件数" % (owner, repo))
        cout("  POST /repos/%s/%s/git/trees" % (owner, repo))
        cout("  POST /repos/%s/%s/git/commits" % (owner, repo))
        cout("  POST /repos/%s/%s/git/refs    （refs/heads/%s）" % (owner, repo, args.branch))
        cout("  POST /repos/%s/%s/git/tags    （v%s）" % (owner, repo, args.version))
        if not args.skip_release:
            cout("  POST /repos/%s/%s/releases" % (owner, repo))
            cout("  POST uploads.github.com/.../assets × 2（zip 与 .sha256）")
        return 0

    step(1, total_steps, "确认远端仓库")
    ensure_repo(token, owner, repo, args.create_repo)

    step(2, total_steps, "上传文件并创建提交")
    ensure_initial_commit(token, owner, repo, args.branch)
    tree_sha = upload_tree(token, owner, repo)
    commit_sha = create_commit(token, owner, repo, tree_sha, args.branch)
    update_branch(token, owner, repo, args.branch, commit_sha)
    api(
        "PATCH",
        "%s/repos/%s/%s" % (GITHUB_API, owner, repo),
        token,
        payload={"default_branch": args.branch},
        allow_fail=True,
    )

    step(3, total_steps, "创建 tag")
    create_tag(token, owner, repo, args.version, commit_sha)

    step(4, total_steps, "创建 Release 并上传资产")
    if args.skip_release:
        cout("  已按 --skip-release 跳过 Release。")
        return 0
    release = create_release(token, owner, repo, args.version)
    upload_assets(token, owner, repo, release["id"], args.version)

    cout("")
    cout("=" * 68)
    cout("发布完成")
    cout("=" * 68)
    cout("仓库：https://github.com/%s/%s" % (owner, repo))
    cout("Release：%s" % release.get("html_url", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
