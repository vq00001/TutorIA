"""Pure ownership barriers shared by the host wrapper and its tests.

This module never deletes Docker resources. Failure to establish ownership is a
hard error, including an unreadable process identity or an unexpected path.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import tempfile
from hashlib import sha256
from pathlib import Path
from uuid import UUID

OWNER = "tutoria-harness-v1"
LABEL_RUN = "org.tutoria.test.run"
LABEL_OWNER = "org.tutoria.test.owner"
LABEL_PROJECT = "com.docker.compose.project"
RUN_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SERVICES = {
    "db-test",
    "api-test",
    "worker-test",
    "scheduler-test",
    "web-test",
    "ai-stub",
    "mail-stub",
    "test-runner",
    "browser-test",
}
HOST_ENV = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME")


class GuardError(RuntimeError):
    pass


def canonical_run(value: str) -> str:
    if not isinstance(value, str) or not RUN_RE.fullmatch(value) or str(UUID(value)) != value:
        raise GuardError("Run ID must be a canonical lowercase UUID")
    return value


def project_name(run_id: str) -> str:
    return "tutoria-test-" + canonical_run(run_id)


def minimal_environment(source=None):
    source = os.environ if source is None else source
    return {key: source[key] for key in HOST_ENV if key in source}


def labels_for(run_id):
    return {
        LABEL_RUN: canonical_run(run_id),
        LABEL_OWNER: OWNER,
        LABEL_PROJECT: project_name(run_id),
    }


def check_labels(labels, run_id):
    expected = labels_for(run_id)
    if not isinstance(labels, dict) or any(labels.get(k) != v for k, v in expected.items()):
        raise GuardError("Resource ownership labels do not match this exact run")


def validate_manifest(manifest, run_id, allowed_services=SERVICES, expected_environment=None):
    """Validate rendered Compose, not source YAML or APP_ENV alone."""
    project = project_name(run_id)
    if manifest.get("name") != project:
        raise GuardError("Rendered Compose project mismatch")
    services = manifest.get("services", {})
    if set(services) != set(allowed_services):
        raise GuardError("Unexpected service inventory")
    networks = manifest.get("networks", {})
    if set(networks) != {"test-only"}:
        raise GuardError("Only the private test network is allowed")
    for network in networks.values():
        if not network.get("internal") or network.get("external"):
            raise GuardError("Test network must be internal and project-owned")
        if network.get("name") != project + "_test-only":
            raise GuardError("Unexpected global network name")
        if network.get("driver") not in (None, "bridge"):
            raise GuardError("Unexpected test network driver")
        own = network.get("labels", {})
        if own.get(LABEL_RUN) != run_id or own.get(LABEL_OWNER) != OWNER:
            raise GuardError("Network ownership labels are missing")
    cpu = memory = 0
    for name, service in services.items():
        if any(
            service.get(k)
            for k in (
                "ports",
                "container_name",
                "privileged",
                "network_mode",
                "devices",
                "external_links",
                "extends",
                "extra_hosts",
                "dns",
                "dns_search",
                "cap_add",
                "pid",
                "ipc",
                "uts",
                "userns_mode",
                "volumes_from",
            )
        ):
            raise GuardError("Unsafe test service capability")
        if service.get("build"):
            raise GuardError("Mutable build configuration is forbidden during a run")
        image = service.get("image", "")
        if not re.search(r"(?:^|@)sha256:[0-9a-f]{64}$", image):
            raise GuardError("Runtime images must be pinned by content digest")
        if set(service.get("networks", {})) != {"test-only"}:
            raise GuardError("Service connected outside the test network")
        own = service.get("labels", {})
        if own.get(LABEL_RUN) != run_id or own.get(LABEL_OWNER) != OWNER:
            raise GuardError("Service ownership labels are missing")
        if service.get("pids_limit", 0) <= 0 or service.get("pids_limit", 0) > 256:
            raise GuardError("A bounded PID limit is required")
        for volume in service.get("volumes", []):
            if volume.get("type") != "volume" or volume.get("source") not in manifest.get(
                "volumes", {}
            ):
                raise GuardError("Bind mounts and unregistered volumes are forbidden")
        env = service.get("environment", {})
        if env.get("TEST_DB_HOST", "db-test") != "db-test":
            raise GuardError("Unexpected database destination")
        for key, value in env.items():
            if key.endswith("DATABASE_URL") and value:
                # URL passwords are random hex. Never accept a host alias or fallback.
                from urllib.parse import urlsplit

                if urlsplit(value).hostname != "db-test":
                    raise GuardError("Database URL must address the isolated db-test service")
                if expected_environment:
                    parsed = urlsplit(value)
                    role = (
                        "TEST_API_PASSWORD"
                        if parsed.username == "tutoria_api"
                        else "TEST_WORKER_PASSWORD"
                        if parsed.username == "tutoria_worker"
                        else None
                    )
                    if (
                        not role
                        or parsed.password != expected_environment[role]
                        or parsed.path != "/" + expected_environment["TEST_DB_NAME"]
                    ):
                        raise GuardError(
                            "Runtime database credentials/name differ from the generated run"
                        )
            if expected_environment and key in (
                "TEST_DB_PASSWORD",
                "TEST_API_PASSWORD",
                "TEST_WORKER_PASSWORD",
                "TEST_NONCE",
                "TEST_RUN_ID",
                "LEDGER_KEY",
            ):
                expected_key = "TEST_LEDGER_KEY" if key == "LEDGER_KEY" else key
                if value != expected_environment[expected_key]:
                    raise GuardError("Synthetic secret or run identity mismatch")
            if (
                expected_environment
                and key == "POSTGRES_PASSWORD"
                and value != expected_environment["TEST_DB_PASSWORD"]
            ):
                raise GuardError("Cluster credential must be generated by this run")
            if key == "OLLAMA_URL" and value != "http://ai-stub:11434":
                raise GuardError("AI must address the isolated provider stub")
            if key == "SMTP_HOST" and value != "mail-stub":
                raise GuardError("SMTP must address the isolated mail sink")
            if key == "APP_ORIGIN" and value != "https://web-test:8443":
                raise GuardError("Runtime origin must use ephemeral HTTPS")
            if any(
                word in key
                for word in (
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "AWS_ACCESS_KEY",
                    "AZURE_OPENAI",
                )
            ):
                raise GuardError("External provider credentials are forbidden")
        cpu += float(service.get("cpus", 0))
        memory += int(service.get("mem_limit", 0))
        if float(service.get("cpus", 0)) <= 0 or int(service.get("mem_limit", 0)) <= 0:
            raise GuardError("Every service needs CPU and memory limits")
    if cpu > 2.00001 or memory > 3 * 1024**3:
        raise GuardError("Aggregate local test resource budget exceeded")
    for name, volume in manifest.get("volumes", {}).items():
        if volume.get("external") or volume.get("name") != project + "_" + name:
            raise GuardError("Unexpected global/external volume")
        labels = volume.get("labels", {})
        if labels.get(LABEL_RUN) != run_id or labels.get(LABEL_OWNER) != OWNER:
            raise GuardError("Volume ownership labels are missing")
    return {"cpu": cpu, "memoryBytes": memory}


def registry_root(repo: Path):
    identity = sha256(str(repo.resolve()).casefold().encode()).hexdigest()[:20]
    return Path(tempfile.gettempdir()).resolve() / OWNER / identity


def checked_run_path(repo: Path, run_id: str, path: Path):
    expected = registry_root(repo) / canonical_run(run_id)
    path = Path(path)
    if (
        path.is_symlink()
        or getattr(path, "is_junction", lambda: False)()
        or path != expected
        or path.resolve() != expected
    ):
        raise GuardError("Run directory is not the registered private directory")
    current = path.parent
    while current != current.parent:
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise GuardError("Run directory has a symlink ancestor")
        if current == Path(tempfile.gettempdir()).resolve():
            break
        current = current.parent
    return path


def atomic_json(path: Path, value):
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".new")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def process_identity(pid: int):
    """Return an OS creation token; None means definitely absent, errors are unsafe."""
    if pid <= 0:
        raise GuardError("Invalid process ID")
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return None
            raise GuardError("Cannot establish owner process identity")
        try:
            creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                raise GuardError("Cannot query owner creation time")
            return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    stat = Path("/proc") / str(pid) / "stat"
    try:
        return stat.read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None
    except (OSError, IndexError) as exc:
        raise GuardError("Cannot establish owner process identity") from exc


def assert_abandoned(record, repo, run_id):
    if (
        record.get("owner") != OWNER
        or record.get("runId") != canonical_run(run_id)
        or record.get("repository") != str(repo.resolve())
    ):
        raise GuardError("Control record ownership mismatch")
    checked_run_path(repo, run_id, Path(record["directory"]))
    identity = process_identity(int(record["pid"]))
    if identity is not None and identity == record["processIdentity"]:
        raise GuardError("Owner process is still alive; refuse cleanup")
    return True
