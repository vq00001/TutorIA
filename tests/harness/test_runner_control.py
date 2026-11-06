import importlib.util
import json
import signal
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

# In the source tree the script lives at repository/scripts, outside tests/.
FILE = Path(__file__).resolve().parents[2] / "scripts" / "test_runner.py"
SPEC = importlib.util.spec_from_file_location("owned_test_runner", FILE)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_signal_handler_keeps_the_original_signal_number():
    with pytest.raises(runner.Interrupted) as error:
        runner.interrupted(signal.SIGTERM, None)
    assert error.value.signum == signal.SIGTERM


def test_no_resource_creation_before_a_valid_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(runner, "registry_root", lambda repo: tmp_path / "registry")
    monkeypatch.setattr(runner, "checked_run_path", lambda repo, run, path: path)

    class Docker:
        base = ["docker", "--context", "synthetic"]
        deadline = None

        def __init__(self):
            self.commands = []

        def run(self, args, **kwargs):
            self.commands.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

    docker = Docker()
    run = runner.Run(docker, str(uuid4()))
    monkeypatch.setattr(runner, "process_identity", lambda pid: "synthetic-creation")
    run.begin({"runner": "sha256:" + "a" * 64}, "digest", 1, "standard", "normal")
    success, errors = run.cleanup()
    assert success and not errors
    assert not run.path.exists()
    assert all("rm" not in command and "down" not in command for command in docker.commands)
    listings = [command for command in docker.commands if "--filter" in command]
    assert len(listings) == 6  # Three inventory queries and three absence checks.
    for command in listings:
        filters = [command[i + 1] for i, arg in enumerate(command) if arg == "--filter"]
        assert all(value.startswith("label=") for value in filters)
        assert "label=" + runner.LABEL_RUN + "=" + run.run_id in filters
    for command in listings[:3]:
        assert "label=" + runner.LABEL_OWNER + "=" + runner.OWNER in command
        assert "label=" + runner.LABEL_PROJECT + "=" + run.project in command


def test_cleaning_a_guarded_cluster_requires_its_exact_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(runner, "registry_root", lambda repo: tmp_path / "registry")
    monkeypatch.setattr(runner, "checked_run_path", lambda repo, run, path: path)

    class Docker:
        base = ["docker", "--context", "synthetic"]
        deadline = None

        def run(self, args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "forged-marker", "")

    run = runner.Run(Docker(), str(uuid4()))
    run.record = {"environment": {"TEST_NONCE": "nonce"}, "serverId": "server", "inventory": {}}
    with pytest.raises(runner.GuardError, match="marker mismatch"):
        run.verify_cluster()


def test_summary_is_read_from_owned_volume_after_test_container_stops():
    class Run:
        def compose(self, args, **kwargs):
            assert args[:6] == ["run", "--rm", "--no-deps", "test-runner", "python", "-c"]
            assert "/reports/tutoria-summary.json" in args[6]
            assert kwargs["capture"] is True
            return subprocess.CompletedProcess(args, 0, json.dumps({"passed": 110, "tests": []}), "")

    assert runner.collect_report(Run(), "tutoria-summary.json")[0]["passed"] == 110


def test_missing_summary_fails_instead_of_silently_reporting_an_empty_list():
    class Run:
        def compose(self, args, **kwargs):
            return subprocess.CompletedProcess(args, 1, "", "")

    with pytest.raises(runner.GuardError, match="Missing test summary"):
        runner.collect_report(Run(), "tutoria-browser-summary.json")
    with pytest.raises(runner.GuardError, match="Unexpected summary filename"):
        runner.collect_report(Run(), "../secret")
