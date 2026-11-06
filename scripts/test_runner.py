"""Owned, disposable Docker runs. Incomplete acceptance coverage fails closed."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.harness.control import (  # noqa: E402
    LABEL_OWNER,
    LABEL_PROJECT,
    LABEL_RUN,
    OWNER,
    GuardError,
    assert_abandoned,
    atomic_json,
    canonical_run,
    check_labels,
    checked_run_path,
    minimal_environment,
    process_identity,
    project_name,
    registry_root,
    validate_manifest,
)

COMPOSE = ROOT / "docker" / "compose.test.yaml"
TIMEOUTS = {"standard": 2700, "full": 7200, "live-ai": 2700, "performance": 5400}
KINDS = {
    "container": ["ps", "-aq"],
    "volume": ["volume", "ls", "-q"],
    "network": ["network", "ls", "-q"],
}


class Interrupted(RuntimeError):
    def __init__(self, signum):
        self.signum = signum


def interrupted(signum, frame):
    raise Interrupted(signum)


def source_digest():
    value = sha256()
    ignored = {
        ".venv",
        "node_modules",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".hypothesis",
        ".git",
    }
    suffixes = {
        ".py",
        ".sql",
        ".lock",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".css",
        ".conf",
        ".html",
        ".sh",
        ".ps1",
    }
    for folder in ("backend", "Webapp", "tests", "docker", "scripts"):
        for path in sorted((ROOT / folder).rglob("*")):
            if (
                path.is_file()
                and not any(part in ignored for part in path.parts)
                and (path.suffix in suffixes or "Dockerfile" in path.name or path.name == "test")
            ):
                value.update(path.relative_to(ROOT).as_posix().encode())
                value.update(path.read_bytes())
    value.update((ROOT / "alembic.ini").read_bytes())
    return value.hexdigest()


class Docker:
    def __init__(self, context):
        executable = shutil.which("docker")
        if not executable:
            raise GuardError("Docker CLI is required")
        if not context or any(c.isspace() for c in context):
            raise GuardError("An explicit Docker context is required")
        self.base = [executable, "--context", context]
        self.env = minimal_environment()
        self.deadline = None

    def run(self, args, *, env=None, capture=False, timeout=120):
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("cleanup deadline", 120)
            timeout = min(timeout, remaining)
        process = subprocess.Popen(
            self.base + args,
            cwd=ROOT,
            env=env or self.env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            raise

    def json(self, args, **kwargs):
        result = self.run(args, capture=True, **kwargs)
        if result.returncode:
            raise GuardError("Docker command failed: " + args[0])
        return json.loads(result.stdout)

    def inspect(self, kind, identifier):
        result = self.json([kind, "inspect", identifier])
        if len(result) != 1:
            raise GuardError("Ambiguous Docker resource identity")
        return result[0]


def labels_of(kind, resource):
    return (
        resource.get("Config", {}).get("Labels", {})
        if kind == "container"
        else resource.get("Labels", {})
    )


class Run:
    def __init__(self, docker, run_id, *, record=None):
        self.docker = docker
        self.run_id = canonical_run(run_id)
        self.project = project_name(run_id)
        self.path = checked_run_path(ROOT, run_id, registry_root(ROOT) / run_id)
        self.lock = RLock()
        self.stop = Event()
        self.thread = None
        self.record = record
        self.deadline = None
        self.cleaning = False

    def save(self):
        with self.lock:
            self.record["heartbeatAt"] = datetime.now(timezone.utc).isoformat()
            atomic_json(self.path / "record.json", self.record)

    def begin(self, images, digest, seed, profile, order):
        identity = process_identity(os.getpid())
        if identity is None:
            raise GuardError("Cannot establish this wrapper process identity")
        self.path.mkdir(parents=True, exist_ok=False)
        if os.name != "nt":
            os.chmod(self.path, 0o700)
        env = minimal_environment()
        password = secrets.token_hex(32)
        env.update(
            TEST_RUN_ID=self.run_id,
            TEST_NONCE=secrets.token_hex(32),
            TEST_DB_PASSWORD=password,
            TEST_API_PASSWORD=secrets.token_hex(32),
            TEST_WORKER_PASSWORD=secrets.token_hex(32),
            TEST_LEDGER_KEY=__import__("base64").b64encode(secrets.token_bytes(32)).decode(),
            TEST_DB_NAME="tutoria_test_" + self.run_id.replace("-", "") + "_browser",
            TEST_API_IMAGE=images.get("api", images["runner"]),
            TEST_WEB_IMAGE=images.get("web", images["runner"]),
            TEST_BROWSER_IMAGE=images.get("browser", images["runner"]),
            TEST_RUNNER_IMAGE=images["runner"],
            TEST_SEED=str(seed),
            TEST_ORDER=order,
            TEST_PROFILE=profile,
            TEST_HYPOTHESIS_EXAMPLES="500" if profile == "full" else "100",
        )
        self.record = {
            "version": 1,
            "owner": OWNER,
            "runId": self.run_id,
            "project": self.project,
            "repository": str(ROOT.resolve()),
            "directory": str(self.path),
            "pid": os.getpid(),
            "processIdentity": identity,
            "context": self.docker.base[2],
            "sourceDigest": digest,
            "environment": env,
            "inventory": {k: [] for k in KINDS},
            "state": "created",
            "provisioned": False,
        }
        self.save()
        (self.path / "empty.env").write_text("", encoding="utf-8")
        self.deadline = time.monotonic() + TIMEOUTS[profile]
        self.thread = Thread(target=self.heartbeat, daemon=True)
        self.thread.start()

    def heartbeat(self):
        while not self.stop.wait(5):
            try:
                self.save()
            except OSError:
                self.stop.set()

    def compose_args(self):
        frozen = self.path / "compose.json"
        return [
            "compose",
            "-p",
            self.project,
            "--env-file",
            str(self.path / "empty.env"),
            "-f",
            str(frozen if frozen.exists() else COMPOSE),
        ]

    def compose(self, args, *, capture=False, timeout=120):
        if self.deadline and not self.cleaning:
            timeout = min(timeout, max(1, self.deadline - time.monotonic()))
            if self.deadline <= time.monotonic():
                raise subprocess.TimeoutExpired(
                    "global test deadline", TIMEOUTS[self.record["environment"]["TEST_PROFILE"]]
                )
        if args and args[0] in ("up", "run", "create", "start"):
            with self.lock:
                self.record["state"] = "provisioning"
                self.record["provisioned"] = True
                self.save()
        try:
            return self.docker.run(
                self.compose_args() + args,
                env=self.record["environment"],
                capture=capture,
                timeout=timeout,
            )
        finally:
            if args and args[0] in ("up", "run", "create", "start"):
                self.snapshot()

    def snapshot(self):
        for kind, args in KINDS.items():
            result = self.docker.run(
                args
                + [
                    "--filter",
                    "label=" + LABEL_RUN + "=" + self.run_id,
                    "--filter",
                    "label=" + LABEL_OWNER + "=" + OWNER,
                    "--filter",
                    "label=" + LABEL_PROJECT + "=" + self.project,
                ],
                capture=True,
            )
            if result.returncode:
                raise GuardError("Cannot inventory " + kind)
            for identifier in result.stdout.split():
                data = self.docker.inspect(kind, identifier)
                check_labels(labels_of(kind, data), self.run_id)
                if identifier not in self.record["inventory"][kind]:
                    with self.lock:
                        self.record["inventory"][kind].append(identifier)
                        self.save()

    def validate(self):
        result = self.compose(["config", "--format", "json"], capture=True)
        if result.returncode:
            raise GuardError("Invalid test Compose configuration")
        manifest = json.loads(result.stdout)
        budget = validate_manifest(
            manifest, self.run_id, expected_environment=self.record["environment"]
        )
        frozen = self.path / "compose.json"
        if not frozen.exists():
            atomic_json(frozen, manifest)
        return budget

    def verify_cluster(self):
        result = self.compose(
            [
                "exec",
                "-T",
                "db-test",
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-Atqc",
                "SELECT run_id::text || ':' || nonce || ':' || (SELECT system_identifier::text FROM pg_control_system()) FROM test_harness.run_guard",
            ],
            capture=True,
            timeout=15,
        )
        expected = (
            self.run_id
            + ":"
            + self.record["environment"]["TEST_NONCE"]
            + ":"
            + self.record["serverId"]
        )
        if result.returncode or result.stdout.strip() != expected:
            raise GuardError("Cluster marker mismatch; refuse destructive cleanup")

    def verify_limits(self):
        for identifier in self.record["inventory"]["container"]:
            resource = self.docker.inspect("container", identifier)
            check_labels(labels_of("container", resource), self.run_id)
            host = resource["HostConfig"]
            if resource.get("State", {}).get("OOMKilled"):
                raise GuardError("An owned test container exceeded its memory limit")
            if host.get("Privileged") or host.get("NetworkMode") == "host" or host.get("Devices"):
                raise GuardError("Unsafe effective container capabilities")
            if (
                not 0 < host.get("Memory", 0) <= 1024**3
                or not 0 < host.get("NanoCpus", 0) <= 2_000_000_000
                or not 0 < host.get("PidsLimit", 0) <= 256
            ):
                raise GuardError("Effective container resource limits missing")
            if any(
                m["Type"] != "volume" and m["Type"] != "tmpfs" for m in resource.get("Mounts", [])
            ):
                raise GuardError("Unexpected effective bind mount")

    def cleanup(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=6)
        self.cleaning = True
        previous_deadline = self.docker.deadline
        self.docker.deadline = time.monotonic() + 120
        errors = []
        try:
            self.snapshot()
            if not self.record.get("provisioned", self.record["state"] != "created"):
                if any(self.record["inventory"].values()):
                    raise GuardError("Unexpected resources before provisioning journal")
            else:
                self.validate()
                if self.record.get("clusterGuarded"):
                    self.verify_cluster()
                result = self.compose(
                    ["down", "--volumes", "--remove-orphans", "--timeout", "20"], timeout=75
                )
                if result.returncode:
                    errors.append("Compose teardown failed")
            # A partially-created one-off may not be removed by Compose. Only
            # inventory entries whose exact labels still match may be removed.
            for kind in ("container", "network", "volume"):
                for identifier in self.record["inventory"][kind]:
                    probe = self.docker.run([kind, "inspect", identifier], capture=True)
                    if probe.returncode:
                        # Distinguish absence from daemon loss before treating it as gone.
                        if self.docker.run(["info"], capture=True, timeout=10).returncode:
                            raise GuardError("Daemon unavailable during final verification")
                        continue
                    resource = json.loads(probe.stdout)[0]
                    check_labels(labels_of(kind, resource), self.run_id)
                    command = (
                        [kind, "rm"] + (["--force"] if kind == "container" else []) + [identifier]
                    )
                    if self.docker.run(command, capture=True, timeout=20).returncode:
                        errors.append("Unable to remove inventoried " + kind)
            for kind, args in KINDS.items():
                result = self.docker.run(
                    args + ["--filter", "label=" + LABEL_RUN + "=" + self.run_id],
                    capture=True,
                    timeout=10,
                )
                if result.returncode or result.stdout.strip():
                    errors.append("Unverified " + kind + " absence")
        except (OSError, ValueError, GuardError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc) if isinstance(exc, GuardError) else type(exc).__name__)
        if errors:
            with self.lock:
                self.record["state"] = "cleanup_pending"
                self.record["cleanupErrors"] = errors
                self.save()
            self.docker.deadline = previous_deadline
            return False, errors
        try:
            checked_run_path(ROOT, self.run_id, self.path)
            shutil.rmtree(self.path)
        except (OSError, GuardError) as exc:
            self.record["state"] = "cleanup_pending"
            self.record["cleanupErrors"] = [type(exc).__name__]
            self.save()
            self.docker.deadline = previous_deadline
            return False, self.record["cleanupErrors"]
        # Empty control parents are not run data and are removed when possible.
        for parent in (self.path.parent, self.path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
        self.docker.deadline = previous_deadline
        return not self.path.exists(), []


def recover(docker, run_id=None):
    root = registry_root(ROOT)
    paths = (
        [root / canonical_run(run_id)] if run_id else list(root.iterdir()) if root.exists() else []
    )
    failures = []
    for path in paths:
        try:
            identifier = canonical_run(path.name)
            checked_run_path(ROOT, identifier, path)
            if not path.exists():
                if run_id:
                    raise GuardError("No owned control record for this run")
                continue
            record_path = path / "record.json"
            if record_path.is_symlink():
                raise GuardError("Control records cannot be symlinks")
            record = json.loads(record_path.read_text(encoding="utf-8"))
            try:
                assert_abandoned(record, ROOT, identifier)
            except GuardError as exc:
                if "still alive" in str(exc) and not run_id:
                    continue
                raise
            if record["context"] != docker.base[2]:
                raise GuardError("Docker context differs from the recorded run")
            ok, errors = Run(docker, identifier, record=record).cleanup()
            if not ok:
                failures.append({"runId": identifier, "errors": errors})
        except (OSError, ValueError, KeyError, GuardError) as exc:
            failures.append({"runId": path.name, "error": str(exc)})
    return failures


def missing_requirements(profile):
    path = ROOT / "tests" / "manifest.yaml"
    manifest = json.loads(path.read_text(encoding="utf-8"))  # JSON is a strict subset of YAML.
    rows = manifest["cases"]
    ids = [r["id"] for r in rows]
    expected = {f"DAT-{i:02d}" for i in range(1, 29)} | {f"TEST-{i:03d}" for i in range(1, 85)}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise GuardError("Acceptance manifest is incomplete or has duplicate IDs")
    missing = []
    for case in rows:
        if profile not in case["profiles"]:
            continue
        if case["status"] == "not_applicable" and case.get("justification"):
            continue
        if case["status"] != "implemented" or not case["tests"]:
            missing.append(case["id"])
        elif any(not (ROOT / node.split("::", 1)[0]).is_file() for node in case["tests"]):
            missing.append(case["id"])
    return missing


def prepared_images(docker, digest, prepare=False, with_browser=False):
    specs = {"runner": ("docker/Dockerfile.tests", "tutoria-test-runner")}
    if with_browser:
        specs["api"] = ("backend/Dockerfile", "tutoria-test-api")
        specs["web"] = ("docker/Dockerfile.web-tests", "tutoria-test-web")
        specs["browser"] = ("docker/Dockerfile.browser-tests", "tutoria-test-browser")
    images = {}
    for name, (dockerfile, tag) in specs.items():
        reference = tag + ":" + digest[:20]
        if prepare:
            result = docker.run(
                [
                    "build",
                    "--label",
                    "org.tutoria.source-digest=" + digest,
                    "-t",
                    reference,
                    "-f",
                    str(ROOT / dockerfile),
                    str(ROOT),
                ],
                timeout=1800,
            )
            if result.returncode:
                raise GuardError("Preparing " + name + " image failed")
        inspection = docker.json(["image", "inspect", reference])
        image = inspection[0]
        if image["Config"].get("Labels", {}).get("org.tutoria.source-digest") != digest:
            raise GuardError("Prepared " + name + " image does not match this source tree")
        images[name] = image["Id"]
    return images


def collect_report(run, filename):
    if filename not in ("tutoria-summary.json", "tutoria-browser-summary.json"):
        raise GuardError("Unexpected summary filename")
    # /tmp is a tmpfs and disappears when the test process stops. Read only
    # content-free summaries from the owned report volume via a fresh helper.
    result = run.compose(
        [
            "run",
            "--rm",
            "--no-deps",
            "test-runner",
            "python",
            "-c",
            "import json; from pathlib import Path; "
            "print(json.dumps(json.loads(Path('/reports/" + filename + "').read_text())))",
        ],
        capture=True,
        timeout=30,
    )
    if result.returncode:
        raise GuardError("Missing test summary: " + filename)
    report = json.loads(result.stdout)
    if not isinstance(report, dict) or not isinstance(report.get("tests"), list):
        raise GuardError("Invalid test summary: " + filename)
    return [report]


def pytest_run(run, suite):
    if suite == "unit":
        paths = ["backend/tests", "tests/harness"]
    elif suite == "integration":
        paths = ["tests/integration"]
    else:
        paths = ["backend/tests", "tests/harness", "tests/integration"]
    command = [
        "pytest",
        "-p",
        "no:cacheprovider",
        "-p",
        "tests.harness.pytest_report",
        "-q",
        "--tb=short",
        "--hypothesis-seed=" + run.record["environment"]["TEST_SEED"],
    ] + paths
    result = run.compose(
        ["run", "--no-deps", "test-runner"] + command,
        timeout=TIMEOUTS[run.record["environment"]["TEST_PROFILE"]],
    )
    return result.returncode, collect_report(run, "tutoria-summary.json")


def browser_run(run):
    prepared = run.compose(
        ["run", "--rm", "--no-deps", "test-runner", "python", "-m", "tests.harness.runtime_setup"],
        timeout=120,
    )
    if prepared.returncode:
        raise GuardError("Runtime case provisioning failed")
    services = ["ai-stub", "mail-stub", "api-test", "worker-test", "scheduler-test", "web-test"]
    if run.compose(
        ["up", "-d", "--wait", "--wait-timeout", "120"] + services, timeout=150
    ).returncode:
        raise GuardError("Runtime readiness failed")
    run.verify_limits()
    unit = run.compose(["run", "--no-deps", "browser-test", "npm", "run", "test:unit"], timeout=120)
    browser = run.compose(["run", "--no-deps", "browser-test"], timeout=300)
    reports = collect_report(run, "tutoria-browser-summary.json")
    if browser.returncode or reports[0].get("failed"):
        diagnostic = run.compose(
            [
                "run",
                "--rm",
                "--no-deps",
                "test-runner",
                "python",
                "-m",
                "tests.harness.runtime_diagnostics",
            ],
            capture=True,
            timeout=30,
        )
        if not diagnostic.returncode:
            reports[0]["runtimeStates"] = json.loads(diagnostic.stdout)
        logs = run.compose(
            ["logs", "--no-color", "--no-log-prefix", "worker-test"], capture=True, timeout=15
        )
        failures = []
        if not logs.returncode:
            for line in logs.stdout.splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("event") == "worker_failure":
                    # Only diagnostics produced by our allowlisted logger.
                    kind = entry.get("kind")
                    category = entry.get("category")
                    state = entry.get("sqlState")
                    if kind not in (
                        "ingest_document",
                        "generate_chat",
                        "generate_exam",
                        "grade_attempt",
                        "purge",
                        "extract_concepts",
                        "refresh_roadmap",
                        "unknown",
                    ):
                        continue
                    if category not in (
                        "ValueError",
                        "ValidationError",
                        "IntegrityError",
                        "ProgrammingError",
                        "OperationalError",
                        "HTTPError",
                        "ConnectionError",
                        "ReadTimeout",
                        "ProcessingError",
                    ):
                        continue
                    failures.append(
                        {
                            "kind": kind,
                            "category": category,
                            "sqlState": state
                            if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state)
                            else None,
                        }
                    )
            reports[0]["workerFailures"] = failures
        print(json.dumps({"browserDiagnostics": reports[0]}))
    return max(unit.returncode, browser.returncode), reports


def one_run(docker, images, digest, args, index, order):
    identifier = str(uuid4())
    run = Run(docker, identifier)
    summary = {
        "runId": identifier,
        "profile": args.profile,
        "suite": args.suite or "standard",
        "seed": args.seed + index,
        "order": order,
        "sourceDigest": digest,
        "images": images,
        "tests": [],
        "missingRequirements": [],
        "testExit": 2,
    }
    result = 2
    cleaned = False
    try:
        run.begin(images, digest, args.seed + index, args.profile, order)
        summary["budget"] = run.validate()
        if docker.run(["info", "--format", "{{json .}}"], capture=True).returncode:
            raise GuardError("Docker daemon became unavailable")
        if run.compose(
            ["up", "-d", "--wait", "--wait-timeout", "120", "db-test"], timeout=150
        ).returncode:
            raise GuardError("Database readiness failed")
        disk = run.compose(
            ["exec", "-T", "db-test", "df", "-Pk", "/var/lib/postgresql"], capture=True, timeout=15
        )
        if (
            disk.returncode
            or int(disk.stdout.strip().splitlines()[-1].split()[3]) * 1024 < 2 * 1024**3
        ):
            raise GuardError("The isolated database filesystem needs at least 2 GiB free")
        marker = run.compose(
            [
                "run",
                "--rm",
                "--no-deps",
                "test-runner",
                "python",
                "-m",
                "tests.harness.cluster_guard",
                "create",
            ],
            capture=True,
        )
        if marker.returncode:
            raise GuardError("Cluster guard provisioning failed")
        guard = json.loads(marker.stdout.strip().splitlines()[-1])
        if guard.get("runId") != identifier or guard.get("guard") != "verified":
            raise GuardError("Cluster identity did not match this run")
        with run.lock:
            run.record["clusterGuarded"] = True
            run.record["serverId"] = guard["serverId"]
            run.save()
        run.verify_limits()
        result, reports = pytest_run(run, args.suite)
        summary["tests"] = reports
        run.verify_limits()
        result = 0 if result == 0 else 1 if result == 1 else 2
        if args.suite is None or args.with_browser:
            browser_code, browser_reports = browser_run(run)
            summary["browserTests"] = browser_reports
            result = max(result, 0 if browser_code == 0 else 1)
        if args.suite is None:
            missing = missing_requirements(args.profile)
            summary["missingRequirements"] = missing
            if missing:
                print("Acceptance gate incomplete: " + ", ".join(missing), file=sys.stderr)
                result = max(result, 2)
    except Interrupted as exc:
        result = 128 + exc.signum
    except KeyboardInterrupt:
        result = 130
    except (OSError, ValueError, KeyError, GuardError, subprocess.TimeoutExpired) as exc:
        summary["infrastructureError"] = (
            str(exc) if isinstance(exc, GuardError) else type(exc).__name__
        )
        result = 2
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        summary["testExit"] = result
        if run.record:
            cleaned, errors = run.cleanup()
            summary["cleanupErrors"] = errors
        else:
            cleaned = not run.path.exists()
        summary["cleanup"] = "verified" if cleaned else "pending"
        print(json.dumps(summary, sort_keys=True))
        signal.signal(signal.SIGINT, interrupted)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, interrupted)
    return result if cleaned or result in (130, 143) else 3, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--with-browser", action="store_true")
    parser.add_argument("--suite", choices=["unit", "integration", "smoke"])
    parser.add_argument("--profile", choices=list(TIMEOUTS), default="standard")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--random-order", action="store_true")
    parser.add_argument("--reverse-order", action="store_true")
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--reports-dir", type=Path)
    parser.add_argument("--clean-run-id")
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10 or args.random_order and args.reverse_order:
        parser.error("repeat must be 1..10; choose only one ordering mode")
    if args.reports_dir and args.reports_dir.exists():
        parser.error("reports-dir must be a new directory")
    signal.signal(signal.SIGINT, interrupted)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, interrupted)
    try:
        docker = Docker(os.environ.get("TUTORIA_TEST_DOCKER_CONTEXT", "desktop-linux"))
        if docker.run(["info"], capture=True, timeout=20).returncode:
            raise GuardError("Docker daemon inaccessible; no new resources created")
        failures = recover(docker, args.clean_run_id)
        if failures:
            print(json.dumps({"cleanup": "pending", "failures": failures}), file=sys.stderr)
            return 3
        if args.clean_run_id or args.reconcile:
            print(json.dumps({"cleanup": "verified", "runId": args.clean_run_id}))
            return 0
        digest = source_digest()
        images = prepared_images(
            docker, digest, args.prepare, args.with_browser or args.suite is None
        )
        if args.prepare:
            print(json.dumps({"prepared": sorted(images), "sourceDigest": digest}))
            return 0
        if (
            args.profile in ("full", "live-ai", "performance")
            and os.environ.get("TUTORIA_DEDICATED_RUNNER") != "1"
        ):
            raise GuardError("Heavy profiles require an explicitly designated disposable runner")
        if args.profile in ("live-ai", "performance"):
            raise GuardError(
                "This release has no executable live-AI/performance campaign; refusing to report acceptance"
            )
        results = []
        for i in range(args.repeat):
            order = "random" if args.random_order else "reverse" if args.reverse_order else "normal"
            code, summary = one_run(docker, images, digest, args, i, order)
            results.append((code, summary))
            if code in (3, 130, 143):
                break
        if args.reports_dir:
            args.reports_dir.mkdir(parents=True, exist_ok=False)
            for code, summary in results:
                (args.reports_dir / (summary["runId"] + ".json")).write_text(
                    json.dumps(summary, indent=2), encoding="utf-8"
                )
        codes = [r[0] for r in results]
        return (
            3
            if 3 in codes
            else 143
            if 143 in codes
            else 130
            if 130 in codes
            else max(codes, default=2)
        )
    except Interrupted as exc:
        return 128 + exc.signum
    except (OSError, ValueError, KeyError, GuardError, subprocess.TimeoutExpired) as exc:
        print(
            str(exc)
            if isinstance(exc, GuardError)
            else type(exc).__name__ + ": test infrastructure failed",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
