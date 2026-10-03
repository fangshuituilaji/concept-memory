"""Installation safety: preserve unrelated settings, state boundaries and rollback."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_client", ROOT / "deploy/configure_client.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def bundle(tmp_path):
    package = tmp_path / "含中文 空格" / "concept-memory"
    for name in ("python/python.exe", "src/memory_system/mcp_server.py", "bin/memory-mcp.cmd"):
        file = package / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
    (package / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    (package / "install").mkdir()
    (package / "install/agent-rules-template.md").write_bytes((ROOT / "deploy/agent-rules-template.md").read_bytes())
    return package


def options(bundle, project, **kwargs):
    defaults = dict(package=str(bundle), project=str(project), client="cursor", scope="project", config=None,
                    servers_key=None, rules_file=None, descriptor=False, dry_run=False)
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.mark.parametrize("client,keys", [
    ("cursor", ("mcpServers",)), ("claude-code", ("mcpServers",)), ("claude-desktop", ("mcpServers",)),
    ("cline", ("mcpServers",)), ("zcode", ("mcp", "servers")), ("vscode", ("servers",)),
    ("opencode", ("mcp",)), ("generic", ("custom", "servers")),
])
def test_json_preserves_settings_and_is_repeatable(bundle, tmp_path, client, keys):
    config = tmp_path / "active.json"
    previous = {"unrelated": {"keep": 42}}
    parent = previous
    for key in keys: parent = parent.setdefault(key, {})
    parent["other-service"] = {"command": "keep-me"}
    parent["concept-memory"] = {"environment" if client == "opencode" else "env": {"DASHSCOPE_API_KEY": "secret-fixture"}}
    config.write_text(json.dumps(previous), encoding="utf-8")
    args = options(bundle, tmp_path, client=client, config=str(config), servers_key=".".join(keys))
    first = installer.configure(args)
    parsed = json.loads(config.read_text(encoding="utf-8"))
    assert parsed["unrelated"] == previous["unrelated"]
    parent = parsed
    for key in keys: parent = parent[key]
    assert parent["other-service"] == {"command": "keep-me"}
    server = parent["concept-memory"]
    assert server["environment" if client == "opencode" else "env"]["DASHSCOPE_API_KEY"] == "secret-fixture"
    assert "secret-fixture" not in json.dumps(first)
    assert first["status"] == "pending_client_reload"
    assert Path(first["config_backup"]).read_text() == json.dumps(previous)
    second = installer.configure(args)
    assert not second["config_changed"] and not second["rules_changed"]
    assert second["config_backup"] is None
    assert Path(second["rules_file"]).read_text(encoding="utf-8").count("## 代码定位规则（concept memory）") == 1


def test_codex_preserves_other_toml_sections_and_enables_server(bundle, tmp_path):
    config = tmp_path / "config.toml"
    original = '# user comment\nmodel = "existing"\n[mcp_servers.other]\ncommand = "other"\n\n[mcp_servers."concept-memory"]\ncommand = "old"\nenabled = false\nstartup_timeout_sec = 60\n[mcp_servers."concept-memory".env]\nDASHSCOPE_API_KEY = "secret-fixture"\n\n[features]\nkeep = true\n'
    config.write_text(original, encoding="utf-8")
    args = options(bundle, tmp_path, client="codex", config=str(config))
    report = installer.configure(args)
    text = config.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert text.startswith('# user comment\nmodel = "existing"')
    assert data["features"] == {"keep": True}
    assert data["mcp_servers"]["other"] == {"command": "other"}
    server = data["mcp_servers"]["concept-memory"]
    assert server["enabled"] and server["startup_timeout_sec"] == 60
    assert server["env"]["DASHSCOPE_API_KEY"] == "secret-fixture"
    assert "secret-fixture" not in json.dumps(report)
    assert not installer.configure(args)["config_changed"]


def test_dry_run_changes_nothing(bundle, tmp_path):
    report = installer.configure(options(bundle, tmp_path, dry_run=True))
    assert report["status"] == "dry_run"
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / "AGENTS.md").exists()


@pytest.mark.parametrize("text", ['{bad-json', '{// comment\n"mcpServers":{}}', '{"mcpServers":[]}'])
def test_bad_configuration_is_not_overwritten(bundle, tmp_path, text):
    config = tmp_path / "bad.json"
    config.write_text(text)
    with pytest.raises(ValueError): installer.configure(options(bundle, tmp_path, config=str(config)))
    assert config.read_text() == text
    assert not (tmp_path / "AGENTS.md").exists()


def test_existing_remote_transport_is_not_replaced(bundle, tmp_path):
    config = tmp_path / "remote.json"
    text = '{"mcpServers":{"concept-memory":{"url":"https://example.invalid/mcp"}}}'
    config.write_text(text)
    with pytest.raises(ValueError, match="transport"): installer.configure(options(bundle, tmp_path, config=str(config)))
    assert config.read_text() == text


def test_rules_failure_restores_configuration(bundle, tmp_path, monkeypatch):
    config = tmp_path / "active.json"
    config.write_text('{"custom":1}')
    old = config.read_bytes()
    real_replace = installer.replace_with_backup
    def failing_replace(path, content):
        if path.name == "AGENTS.md": raise OSError("Read-only rules")
        return real_replace(path, content)
    monkeypatch.setattr(installer, "replace_with_backup", failing_replace)
    with pytest.raises(OSError): installer.configure(options(bundle, tmp_path, config=str(config)))
    assert config.read_bytes() == old


def test_custom_client_requires_actual_schema(bundle, tmp_path):
    with pytest.raises(ValueError, match="servers-key"):
        installer.configure(options(bundle, tmp_path, client="generic", config=str(tmp_path / "unknown.json")))


def test_descriptor_never_claims_live_registration(bundle, tmp_path):
    report = installer.configure(options(bundle, tmp_path, descriptor=True, client=None))
    assert report["status"] == "registration_required"
    assert report["server"]["args"] == ["-m", "memory_system.mcp_server"]
    assert "DASHSCOPE_API_KEY" not in report["server"]["env"]


@pytest.mark.parametrize("client,relative", [("codex", ".codex/config.toml"), ("cursor", ".cursor/mcp.json"),
    ("claude-code", ".mcp.json"), ("zcode", ".zcode/config.json"), ("vscode", ".vscode/mcp.json"), ("opencode", "opencode.json")])
def test_project_scope_locations(client, relative, tmp_path):
    assert installer.config_location(client, tmp_path, "project")[0] == tmp_path / relative


def test_powershell_bootstrap_rejects_bad_checksum_without_touching_install(tmp_path):
    archive = tmp_path / "concept-memory-offline-win64-v0.2.0.zip"
    with zipfile.ZipFile(archive, "w") as z: z.writestr("concept-memory/VERSION", "0.2.0")
    checksum = tmp_path / "bundle.sha256"
    checksum.write_text("0" * 64 + "  " + archive.name)
    target = tmp_path / "existing"
    (target / "bin").mkdir(parents=True)
    (target / "python").mkdir()
    (target / "VERSION").write_text("0.1.0")
    (target / "bin/memory-mcp.cmd").write_text("fixture")
    (target / "python/python.exe").write_text("fixture")
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "deploy/bootstrap.ps1"),
                             "-InstallRoot", str(target), "-ArchivePath", str(archive), "-ChecksumPath", str(checksum)], capture_output=True, timeout=30)
    assert result.returncode != 0
    assert b"SHA256 mismatch" in result.stderr
    assert (target / "VERSION").read_text() == "0.1.0"
    assert not list(tmp_path.glob(".cm-install-*"))


@pytest.mark.parametrize("entry", ["concept-memory/../escape.txt", "another-root/file", "concept-memory/C:/escape.txt"])
def test_powershell_rejects_archive_path_escape(tmp_path, entry):
    archive = tmp_path / "concept-memory-offline-win64-v0.2.0.zip"
    with zipfile.ZipFile(archive, "w") as z: z.writestr(entry, "untrusted")
    checksum = tmp_path / "bundle.sha256"
    checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name)
    target = tmp_path / "safe-target"
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "deploy/bootstrap.ps1"),
                             "-InstallRoot", str(target), "-ArchivePath", str(archive), "-ChecksumPath", str(checksum)], capture_output=True, timeout=30)
    assert result.returncode != 0 and b"Unsafe archive entry" in result.stderr
    assert not target.exists()
    assert not (tmp_path / "escape.txt").exists()


def test_powershell_final_check_failure_restores_existing_installation(tmp_path):
    # Staging passes; failure happens only at the stable installation path.
    archive = tmp_path / "concept-memory-offline-win64-v0.2.0.zip"
    checker = "if ($PSScriptRoot -like '*\\e\\*') { Write-Output 'PASS'; exit 0 }; Write-Output 'FAIL'; exit 1"
    with zipfile.ZipFile(archive, "w") as z:
        for name in ("python/python.exe", "bin/memory-mcp.cmd", "src/memory_system/mcp_server.py", "install/configure_client.py", "install/INSTALL.md"):
            z.writestr("concept-memory/" + name, "fixture")
        z.writestr("concept-memory/VERSION", "0.2.0")
        z.writestr("concept-memory/install/check_install.ps1", checker)
    checksum = tmp_path / "bundle.sha256"
    checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name)
    target = tmp_path / "existing"
    (target / "bin").mkdir(parents=True)
    (target / "python").mkdir()
    for name, text in (("VERSION", "0.1.0"), ("bin/memory-mcp.cmd", "old"), ("python/python.exe", "old"), ("keep.txt", "keep")):
        (target / name).write_text(text)
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "deploy/bootstrap.ps1"),
                             "-InstallRoot", str(target), "-ArchivePath", str(archive), "-ChecksumPath", str(checksum)], capture_output=True, timeout=30)
    assert result.returncode != 0 and b"Bundle self-check failed" in result.stderr
    assert (target / "VERSION").read_text() == "0.1.0"
    assert (target / "keep.txt").read_text() == "keep"
    assert not list(tmp_path.glob("existing.previous-*"))


def test_powershell_download_branch_uses_release_assets_and_verifies_checksum(tmp_path):
    archive = tmp_path / "concept-memory-offline-win64-v0.2.0.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name in ("python/python.exe", "bin/memory-mcp.cmd", "src/memory_system/mcp_server.py", "install/configure_client.py", "install/INSTALL.md"):
            z.writestr("concept-memory/" + name, "fixture")
        z.writestr("concept-memory/VERSION", "0.2.0")
        z.writestr("concept-memory/install/check_install.ps1", "Write-Output 'PASS'; exit 0")
    checksum = tmp_path / "bundle.sha256"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(digest + "  " + archive.name)
    driver = tmp_path / "driver.ps1"
    # Only HTTP responses are simulated; download selection, hash, extraction,
    # child-process self-checks and destination moves run through the actual script.
    driver.write_text("""param($Bootstrap,$Archive,$Sum,$Target)
$global:ConceptMemoryMockArchive = $Archive
$global:ConceptMemoryMockSum = $Sum
function Invoke-RestMethod { param($Uri,$Headers,$TimeoutSec)
    if ($Uri -ne 'https://api.github.com/repos/fangshuituilaji/concept-memory/releases/latest') { throw 'Wrong metadata URL' }
    return @{tag_name='v0.2.0'; assets=@(@{name='concept-memory-offline-win64-v0.2.0.zip';id=11},@{name='concept-memory-offline-win64-v0.2.0.zip.sha256';id=12})}
}
function Invoke-WebRequest { param($Uri,$Headers,$OutFile,$TimeoutSec,[switch]$UseBasicParsing)
    if ($Headers.Accept -ne 'application/octet-stream') { throw 'Wrong header' }
    if ($Uri -eq 'https://api.github.com/repos/fangshuituilaji/concept-memory/releases/assets/11') { Copy-Item -LiteralPath $global:ConceptMemoryMockArchive -Destination $OutFile }
    elseif ($Uri -eq 'https://api.github.com/repos/fangshuituilaji/concept-memory/releases/assets/12') { Copy-Item -LiteralPath $global:ConceptMemoryMockSum -Destination $OutFile }
    else { throw 'Wrong asset URL' }
}
& $Bootstrap -InstallRoot $Target
""", encoding="utf-8-sig")
    target = tmp_path / "downloaded"
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver),
                             str(ROOT / "deploy/bootstrap.ps1"), str(archive), str(checksum), str(target)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    report = json.loads(result.stdout.decode("utf-8-sig"))
    assert report["status"] == "package_ready" and report["sha256"] == digest
    assert (target / "VERSION").read_text() == "0.2.0"
