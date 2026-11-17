"""Test del límite de carga individual a través del proxy web de TutorIA."""
from datetime import datetime
import json
import math
import os
import subprocess
from uuid import uuid4

from .api import Api, ErrorApi


def resumir_configuracion(config):
    return {
        "Proxy": config["escenario"]["url_proxy"],
        "Casos (bytes → HTTP)": ", ".join(
            f'{case["tamano_bytes"]:_} → {case["http_esperado"]}'.replace("_", ".")
            for case in config["casos"]
        ),
        "Plazo por carga": f'{config["tiempos"]["carga_segundos"]} s',
    }


def comprobar_carga(api, course, case, timeout):
    size = case["tamano_bytes"]
    expected = case["http_esperado"]
    result = dict(case)
    result["archivo"] = f"limite-{size}.txt"
    try:
        response = api.call(
            f"/courses/{course}/documents",
            upload=(result["archivo"], b"x" * size),
            timeout=timeout, expected_status=202,
        )
    except ErrorApi as error:
        result.update(
            http_recibido=error.status, codigo_recibido=error.code,
            cumple=expected == 413 and error.status == 413 and error.code == case["codigo_esperado"],
        )
    else:
        result["http_recibido"] = 202
        items = response.get("data", []) if isinstance(response, dict) else []
        uploaded = (items[0] if isinstance(items, list) and len(items) == 1
                    and isinstance(items[0], dict) else {})
        result["documento_id"] = uploaded.get("documentId")
        result["trabajo_id"] = uploaded.get("jobId")
        result["cumple"] = (
            expected == 202 and uploaded.get("duplicate") is False
            and bool(result["documento_id"]) and bool(result["trabajo_id"])
        )
    return result


def run(config, repo, results_dir, *, processor, manages_services):
    cases = config["casos"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("Define al menos un caso de carga.")
    for case in cases:
        if type(case["tamano_bytes"]) is not int or case["tamano_bytes"] <= 0:
            raise ValueError("tamano_bytes debe ser un entero positivo.")
        if case["http_esperado"] not in (202, 413):
            raise ValueError("http_esperado debe ser 202 o 413.")
        if case["http_esperado"] == 413 and not case.get("codigo_esperado"):
            raise ValueError("Un rechazo esperado requiere codigo_esperado.")
    timeout = config["tiempos"]["carga_segundos"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("carga_segundos debe ser positivo y finito.")

    started = datetime.now().astimezone()
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"rnf-r4-{started:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    report = {
        "inicio": started.isoformat(timespec="seconds"),
        "revision_checkout": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "configuracion": config,
        "resultados": [],
    }

    def save():
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    print(f"Informe: {output}", flush=True)
    api = Api(config["escenario"]["url_proxy"])
    try:
        api.token = api.call("/auth/login", payload={
            "email": os.getenv("DEV_ACCOUNT_EMAIL", "dev@example.com"),
            "password": os.getenv("DEV_ACCOUNT_PASSWORD", "TutorIA-dev-123"),
        })["data"]["accessToken"]
        course = api.call("/courses", payload={
            "name": f"RNF-R-4 Límite de carga {uuid4().hex[:8]}",
            "description": "Curso de prueba para el límite individual de carga.",
            "objectives": "Verificar la aceptación y el rechazo de documentos por tamaño.",
        })["data"]["id"]
        report["curso_id"] = course
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        report["resumen"] = {"cumple": None, "error_preparacion": str(error)}
        save()
        raise

    for case in cases:
        try:
            result = comprobar_carga(api, course, case, timeout)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            result = case | {"cumple": None, "detalle": str(error)}
        report["resultados"].append(result)
        save()
        print(json.dumps(result, ensure_ascii=False), flush=True)
    verdicts = [row["cumple"] for row in report["resultados"]]
    report["resumen"] = {"cumple": None if None in verdicts else all(verdicts)}
    save()
    if report["resumen"]["cumple"] is None:
        return 2
    return 0 if report["resumen"]["cumple"] else 1
