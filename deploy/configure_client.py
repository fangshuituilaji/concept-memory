"""Register an already checked bundle in a client selected by the installing agent.

Uses only the standard library and never claims the live agent has loaded tools.
Unknown clients use the printed descriptor and their own documented registration.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
import uuid

SERVER_NAME = "concept-memory"
CLIENTS = ("codex", "claude-code", "claude-desktop", "cursor", "cline", "zcode", "vscode", "opencode", "generic")


def descriptor(package: Path) -> dict:
    package = package.resolve()
    for name in ("python/python.exe", "src/memory_system/mcp_server.py", "bin/memory-mcp.cmd", "VERSION"):
        if not (package / name).is_file():
            raise ValueError(f"Missing bundle file: {name}; run the bundle self-check first")
    return {
        "command": str(package / "python/python.exe"),
        "args": ["-m", "memory_system.mcp_server"],
        "env": {"PYTHONPATH": f"{package / 'python/Lib/site-packages'};{package / 'src'}"},
    }


def config_location(client: str, project: Path, scope: str) -> tuple[Path, tuple[str, ...]]:
    home = Path.home()
    locations = {
        "codex": ((project / ".codex/config.toml") if scope == "project" else Path(os.environ.get("CODEX_HOME", home / ".codex")) / "config.toml", ("mcp_servers",)),
        "claude-code": ((project / ".mcp.json") if scope == "project" else home / ".claude.json", ("mcpServers",)),
        "cursor": ((project / ".cursor/mcp.json") if scope == "project" else home / ".cursor/mcp.json", ("mcpServers",)),
        "zcode": (project / ".zcode/config.json", ("mcp", "servers")),
        "vscode": (project / ".vscode/mcp.json", ("servers",)),
        "opencode": ((project / "opencode.json") if scope == "project" else Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "opencode/opencode.json", ("mcp",)),
    }
    if client == "claude-desktop":
        if not os.environ.get("APPDATA"):
            raise ValueError("APPDATA is missing; supply --config explicitly")
        return Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json", ("mcpServers",)
    if client == "cline":
        current = home / ".cline/data/settings/cline_mcp_settings.json"
        old = Path(os.environ.get("APPDATA", home)) / "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
        if current.exists() and old.exists():
            raise ValueError("Multiple Cline configs exist; select the active file with --config")
        return (old if old.exists() else current), ("mcpServers",)
    if client == "generic":
        raise ValueError("Unknown clients need --config and --servers-key based on their documentation")
    if scope == "user" and client in ("zcode", "vscode"):
        raise ValueError("This adapter supports project scope only; use the client's native registration for user scope")
    path, keys = locations[client]
    if client == "opencode" and path.with_suffix(".jsonc").exists():
        raise ValueError("An opencode.jsonc exists; use the client's native registration without creating a competing JSON config")
    return path, keys


def merge_json(text: str, keys: tuple[str, ...], server: dict, client: str) -> str:
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be an object")
    parent = data
    for key in keys:
        if key not in parent:
            parent[key] = {}
        if not isinstance(parent[key], dict):
            raise ValueError(f"Configuration section {key} is not an object")
        parent = parent[key]
    old = parent.get(SERVER_NAME, {})
    if not isinstance(old, dict):
        raise ValueError("Existing concept-memory config must be an object")
    merged = deepcopy(old)
    if client == "opencode":
        if old.get("type", "local") != "local":
            raise ValueError("Existing server is remote; inspect it before replacing its transport")
        merged.update(type="local", command=[server["command"], *server["args"]], enabled=True)
        env = merged.setdefault("environment", {})
    else:
        if "url" in old or old.get("type", "stdio") not in ("stdio", "local"):
            raise ValueError("Existing server uses another transport; inspect it before replacement")
        merged.update(command=server["command"], args=server["args"])
        env = merged.setdefault("env", {})
        if client == "vscode":
            merged["type"] = "stdio"
        if client == "cline":
            merged["disabled"] = False
            merged.setdefault("autoApprove", [])
    if not isinstance(env, dict):
        raise ValueError("Server environment must be an object")
    env.update(server["env"])
    parent[SERVER_NAME] = merged
    # Keep existing secrets in place, but never print config contents in the report.
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def merge_toml(text: str, server: dict) -> str:
    data = tomllib.loads(text)
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict): raise ValueError("mcp_servers must be a TOML table")
    old = servers.get(SERVER_NAME, {})
    if not isinstance(old, dict): raise ValueError("Invalid existing MCP table")
    if "url" in old:
        raise ValueError("Existing server uses HTTP; inspect it before replacing its transport")
    if not isinstance(old, dict) or not isinstance(old.get("env", {}), dict):
        raise ValueError("Invalid existing MCP table")
    merged = deepcopy(old)
    merged.update(command=server["command"], args=server["args"], enabled=True)
    merged.setdefault("env", {}).update(server["env"])
    # Refuse unfamiliar nested tables rather than deleting settings we cannot serialize.
    if any(isinstance(v, dict) for k, v in merged.items() if k != "env"):
        raise ValueError("Server has nested TOML options; use the Codex CLI to preserve them")
    def value(v):
        if isinstance(v, str): return json.dumps(v, ensure_ascii=False)
        if isinstance(v, bool): return "true" if v else "false"
        if isinstance(v, (int, float)): return str(v)
        if isinstance(v, list): return "[" + ", ".join(value(i) for i in v) + "]"
        raise ValueError("Unsupported TOML value; use native registration")
    # Remove only this server's tables; preserve other sections and comments verbatim.
    header = re.compile(r'^\s*\[(?!\[)([^\]\r\n]+)\]\s*(?:#.*)?$', re.MULTILINE)
    matches = list(header.finditer(text))
    kept, cursor = [], 0
    for i, match in enumerate(matches):
        name = match.group(1).strip().replace('"', '').replace("'", '')
        if name == "mcp_servers.concept-memory" or name.startswith("mcp_servers.concept-memory."):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            kept.append(text[cursor:match.start()]); cursor = end
    kept.append(text[cursor:])
    result = "".join(kept).rstrip() + "\n\n[mcp_servers.concept-memory]\n"
    result += "\n".join(f"{json.dumps(k)} = {value(v)}" for k, v in merged.items() if k != "env")
    result += "\n\n[mcp_servers.concept-memory.env]\n"
    result += "\n".join(f"{json.dumps(k)} = {value(v)}" for k, v in merged["env"].items()) + "\n"
    expected = deepcopy(data)
    expected.setdefault("mcp_servers", {})[SERVER_NAME] = merged
    if tomllib.loads(result) != expected:
        raise ValueError("TOML structure is not supported safely; use native registration")
    return result


def replace_with_backup(path: Path, content: str) -> str | None:
    if path.exists() and path.read_text(encoding="utf-8-sig") == content:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".concept-memory-backup-" + uuid.uuid4().hex)
        backup.write_bytes(path.read_bytes())
    fd, temporary = tempfile.mkstemp(prefix=".concept-memory-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
            out.write(content)
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists(): Path(temporary).unlink()
    return str(backup) if backup else None


def configure(args) -> dict:
    package, project = Path(args.package).resolve(), Path(args.project).resolve()
    if not project.is_dir(): raise ValueError("Project directory does not exist")
    server = descriptor(package)
    if args.descriptor:
        return {"status": "registration_required", "server": server, "package_root": str(package)}
    if not args.client: raise ValueError("The installing agent must select its CURRENT client with --client")
    if args.client == "generic" and (not args.config or not args.servers_key):
        raise ValueError("Generic JSON registration requires both --config and --servers-key")
    keys = tuple(args.servers_key.split(".")) if args.servers_key else None
    if args.config:
        path = Path(args.config).resolve()
        if keys is None:
            keys = {"zcode": ("mcp", "servers"), "vscode": ("servers",), "opencode": ("mcp",), "codex": ("mcp_servers",)}.get(args.client, ("mcpServers",))
    else:
        path, default_keys = config_location(args.client, project, args.scope)
        keys = keys or default_keys
    if not all(keys): raise ValueError("Empty configuration section name")
    if path.is_symlink(): raise ValueError("Config is a symlink; use the client's native registration")
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if args.client == "codex":
        updated = merge_toml(text, server)
    else:
        updated = merge_json(text, keys, server, args.client)
    rules = Path(args.rules_file).resolve() if args.rules_file else project / ("CLAUDE.md" if args.client == "claude-code" else "AGENTS.md")
    if rules == path: raise ValueError("Config and rules must be different files")
    template_path = package / "install/agent-rules-template.md"
    template = template_path.read_text(encoding="utf-8")
    marker = "## 代码定位规则（concept memory）"
    if marker not in template or "## 规则正文结束" not in template: raise ValueError("Invalid rules template")
    body = template[template.index(marker):template.index("## 规则正文结束")].strip()
    old_rules = rules.read_text(encoding="utf-8-sig") if rules.exists() else ""
    new_rules = old_rules if marker in old_rules else old_rules.rstrip() + "\n\n" + body + "\n"
    report = {"status": "dry_run" if args.dry_run else "pending_client_reload", "client": args.client,
              "config_file": str(path), "rules_file": str(rules), "package_root": str(package),
              "config_changed": text != updated, "rules_changed": old_rules != new_rules,
              "next_step": "Reload this client's MCP connection. Confirm tools in the actual agent session, scan_codebase(path=project), query and fetch cards. A subprocess probe alone is not completed installation."}
    if not args.dry_run:
        report["config_backup"] = replace_with_backup(path, updated)
        try:
            report["rules_backup"] = replace_with_backup(rules, new_rules)
        except OSError:
            # Restore configuration if rule persistence fails: no half-applied registration.
            if report["config_backup"]:
                path.write_bytes(Path(report["config_backup"]).read_bytes())
            elif text != updated:
                path.unlink(missing_ok=True)
            raise
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--client", choices=CLIENTS)
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument("--config", help="Active config file, established by the installing agent")
    parser.add_argument("--servers-key", help="Object path for a generic JSON config, e.g. mcp.servers")
    parser.add_argument("--rules-file", help="Rules file actually read by this client")
    parser.add_argument("--descriptor", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    try:
        print(json.dumps(configure(parser.parse_args(argv)), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Do not echo malformed configuration text: it may contain credentials.
        message = "Invalid JSON/TOML configuration; nothing was overwritten" if isinstance(exc, (json.JSONDecodeError, tomllib.TOMLDecodeError)) else str(exc)
        print(json.dumps({"status": "failed", "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
