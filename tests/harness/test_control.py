from copy import deepcopy
from uuid import uuid4

import pytest

from tests.harness import control

RUN = "56d43f83-8d24-45a9-b59a-644c731b1f96"


def manifest():
    project = control.project_name(RUN)
    labels = {control.LABEL_RUN: RUN, control.LABEL_OWNER: control.OWNER}
    service = {
        "image": "sha256:" + "a" * 64,
        "networks": {"test-only": {}},
        "labels": labels,
        "cpus": 0.20,
        "mem_limit": 128 * 1024**2,
        "pids_limit": 64,
        "environment": {"TEST_DB_HOST": "db-test"},
    }
    return {
        "name": project,
        "services": {n: deepcopy(service) for n in control.SERVICES},
        "networks": {
            "test-only": {"internal": True, "name": project + "_test-only", "labels": dict(labels)}
        },
        "volumes": {},
    }


@pytest.mark.parametrize(
    "value", ["all", "", RUN.upper(), "../" + RUN, RUN + "\n", str(uuid4()).replace("-", "")]
)
def test_TEST_009_run_ids_cannot_be_fuzzy(value):
    with pytest.raises(control.GuardError):
        control.canonical_run(value)


def test_TEST_007_host_credentials_are_not_inherited():
    value = control.minimal_environment(
        {
            "PATH": "safe",
            "DATABASE_URL": "main",
            "COMPOSE_PROJECT_NAME": "main",
            "OPENAI_API_KEY": "secret",
            "HOME": "user",
        }
    )
    assert value == {"PATH": "safe", "HOME": "user"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["services"]["api-test"].update(ports=["8000:8000"]),
        lambda x: x["services"]["api-test"].update(container_name="main"),
        lambda x: x["services"]["api-test"].update(privileged=True),
        lambda x: x["services"]["api-test"].update(network_mode="host"),
        lambda x: x["services"]["api-test"].update(
            volumes=[{"type": "bind", "source": "/home", "target": "/data"}]
        ),
        lambda x: x["services"]["api-test"].update(image="app:latest"),
        lambda x: x["services"]["api-test"]["environment"].update(
            DATABASE_URL="postgresql://u:p@host.docker.internal/main"
        ),
        lambda x: x["networks"]["test-only"].update(external=True),
        lambda x: x["networks"]["test-only"].update(name="main-network"),
        lambda x: x["services"]["api-test"].update(cpus=9),
        lambda x: x["services"]["api-test"].update(mem_limit=5 * 1024**3),
        lambda x: x["services"]["api-test"].update(extra_hosts={"db-test": "127.0.0.1"}),
        lambda x: x["services"]["api-test"]["environment"].update(
            OLLAMA_URL="https://external-provider.example"
        ),
        lambda x: x["services"]["api-test"]["environment"].update(SMTP_HOST="smtp.example.com"),
        lambda x: x["services"]["api-test"]["environment"].update(OPENAI_API_KEY="must-not-arrive"),
        lambda x: x["services"]["api-test"]["labels"].update({control.LABEL_RUN: str(uuid4())}),
    ],
)
def test_TEST_008_unsafe_rendered_configuration_is_rejected(mutation):
    value = manifest()
    mutation(value)
    with pytest.raises(control.GuardError):
        control.validate_manifest(value, RUN)


def test_expected_manifest_is_within_budget():
    budget = control.validate_manifest(manifest(), RUN)
    assert budget["cpu"] == pytest.approx(1.8)
    assert budget["memoryBytes"] == 9 * 128 * 1024**2


def test_resource_labels_require_run_owner_and_compose_project():
    value = control.labels_for(RUN)
    control.check_labels(value, RUN)
    for key in value:
        forged = dict(value)
        forged[key] = "wrong"
        with pytest.raises(control.GuardError):
            control.check_labels(forged, RUN)


def test_TEST_009_cleanup_path_must_be_exact(tmp_path):
    repo = tmp_path / "repository"
    with pytest.raises(control.GuardError):
        control.checked_run_path(repo, RUN, repo)
    expected = control.registry_root(repo) / RUN
    assert control.checked_run_path(repo, RUN, expected) == expected
    with pytest.raises(control.GuardError):
        control.checked_run_path(repo, RUN, expected.parent)


def test_TEST_004_live_owner_is_never_reconciled(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    record = {
        "runId": RUN,
        "owner": control.OWNER,
        "repository": str(repo.resolve()),
        "directory": str(control.registry_root(repo) / RUN),
        "pid": 123,
        "processIdentity": "creation-A",
    }
    monkeypatch.setattr(control, "process_identity", lambda pid: "creation-A")
    with pytest.raises(control.GuardError, match="still alive"):
        control.assert_abandoned(record, repo, RUN)
    monkeypatch.setattr(control, "process_identity", lambda pid: "creation-B")
    assert control.assert_abandoned(record, repo, RUN)


def test_control_records_are_atomically_replaceable(tmp_path):
    target = tmp_path / "record.json"
    control.atomic_json(target, {"nonce": "one"})
    control.atomic_json(target, {"nonce": "two"})
    assert target.read_text() == '{"nonce": "two"}'
    assert list(tmp_path.iterdir()) == [target]
