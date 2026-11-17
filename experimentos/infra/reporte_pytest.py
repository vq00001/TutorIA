"""Ejecuta la selección RNF-S-1 a S-4 y registra el resultado real de cada caso."""
from collections import Counter
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import sys

import pytest

MODULES = [
    "tests/test_rnf_s1.py",
    "tests/test_rnf_s2.py",
    "tests/test_rnf_s3.py",
    "tests/test_rnf_s4.py",
    "tests/test_audit.py",
]
OUTCOMES = ("passed", "failed", "error", "xfailed", "xpassed", "skipped", "not_run")
PRECEDENCE = ("error", "failed", "xpassed", "xfailed", "skipped")


def resultado(report):
    if report.failed:
        if str(report.longrepr).startswith("[XPASS(strict)]"):
            return "xpassed"
        return "failed" if report.when == "call" else "error"
    if hasattr(report, "wasxfail"):
        return "xfailed" if report.skipped else "xpassed"
    return "skipped" if report.skipped else "passed"


class Reporte:
    def __init__(self):
        self.collected = []
        self.collection_issues = []
        self.phases = {}

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.collection_issues.append({"caso": report.nodeid, "estado": report.outcome})

    def pytest_runtest_logreport(self, report):
        phase = {"fase": report.when, "estado": resultado(report), "segundos": report.duration}
        if hasattr(report, "wasxfail"):
            phase["motivo"] = str(report.wasxfail)
        self.phases.setdefault(report.nodeid, []).append(phase)

    def guardar(self, directory, exit_code):
        cases = []
        for node in dict.fromkeys([*self.collected, *self.phases]):
            phases = self.phases.get(node, [])
            states = {phase["estado"] for phase in phases}
            status = next((name for name in PRECEDENCE if name in states), None)
            if status is None:
                status = "passed" if any(phase["fase"] == "call" for phase in phases) else "not_run"
            cases.append({"caso": node, "estado": status, "fases": phases})

        by_module = {module: Counter() for module in MODULES}
        for case in cases:
            module = case["caso"].split("::", 1)[0]
            by_module.setdefault(module, Counter())[case["estado"]] += 1
        totals = Counter(case["estado"] for case in cases)
        report = {
            "codigo_salida_pytest": int(exit_code),
            "python": platform.python_version(),
            "dependencias": sorted(
                f"{dist.metadata['Name']}=={dist.version}" for dist in distributions()
            ),
            "modulos": MODULES,
            "recolectados": len(self.collected),
            "resumen": {name: totals[name] for name in OUTCOMES},
            "resumen_por_modulo": {
                module: {name: counts[name] for name in OUTCOMES}
                for module, counts in by_module.items()
            },
            "incidencias_recoleccion": self.collection_issues,
            "casos": cases,
        }
        path = directory / "resumen.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        print("Resumen RNF-S-1 a S-4:", report["resumen"], flush=True)


if __name__ == "__main__":
    output = Path(sys.argv[1])
    report = Reporte()
    code = pytest.main(
        ["-q", "-rA", "-p", "no:cacheprovider", "--junitxml", str(output / "junit.xml"), *MODULES],
        plugins=[report],
    )
    report.guardar(output, code)
    sys.exit(code)
