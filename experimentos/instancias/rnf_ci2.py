"""Verifica aceptación, indexación y recuperación de contenido por formato."""
from datetime import datetime
from hashlib import sha256
import json
import math
import os
import re
import subprocess
import time
from uuid import uuid4

from .api import Api, ErrorApi
from .formatos_ci2 import generar_materiales
from .rag import consultar


REQUIRED = {"txt", "pdf", "docx"}
CONTROL = "md"
QUESTION = (
    "Segun la ficha del proyecto Faro Azul, cual es su codigo exacto de registro? "
    "Devuelve el codigo tal como aparece en el material del curso."
)


def resumir_configuracion(config):
    return {
        "Formatos obligatorios": "TXT, PDF, DOCX",
        "Control": "Markdown",
        "Orden de prueba": ", ".join(config["escenario"]["formatos"]),
        "URL": config["escenario"]["url"],
        "Plazo de indexación": f'{config["tiempos"]["ingesta_segundos"]} s',
        "Plazo de consulta": f'{config["tiempos"]["respuesta_segundos"]} s',
    }


def observar_ingesta(api, base, document_id, times):
    started = time.monotonic()
    deadline = started + times["ingesta_segundos"]
    last_state = None
    changes = []
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            status = api.call(f"{base}/documents/{document_id}/status", timeout=min(10, remaining))["data"]
        except ErrorApi as error:
            if error.status != 404 or last_state or time.monotonic() - started >= 5:
                raise
        except TimeoutError:
            if time.monotonic() < deadline:
                raise
            break
        else:
            state = status["status"]
            if state != last_state:
                changes.append({"estado": state, "segundos": time.monotonic() - started})
                last_state = state
            if state in ("ready", "failed"):
                return {"estado": state, "transiciones": changes, "error": status.get("errorCode")}
        time.sleep(max(0, min(times["sondeo_segundos"], deadline - time.monotonic())))
    return {"estado": "plazo_agotado", "transiciones": changes}


def verificar_formato(api, material, expected, times, result):
    filename, content = material
    course = api.call("/courses", payload={
        "name": f'RNF-CI-2 {result["formato"].upper()} {uuid4().hex[:8]}',
        "description": "Un curso por formato para verificar recuperación documental.",
        "objectives": "Consultar el código de registro del proyecto Faro Azul.",
    })["data"]["id"]
    result["curso_id"] = course
    base = f"/courses/{course}"
    try:
        uploaded, = api.call(f"{base}/documents", upload=(filename, content))["data"]
    except ErrorApi as error:
        if error.status not in (413, 415, 422):
            raise
        result.update(estado="carga_rechazada", carga={"estado": "rechazada", "http": error.status})
        return
    document_id = uploaded["documentId"]
    if uploaded["duplicate"] or not document_id:
        raise RuntimeError("La carga no devolvió un documento nuevo.")
    result["carga"] = {"estado": "aceptada", "documento_id": document_id, "job_id": uploaded["jobId"]}
    result["ingesta"] = observar_ingesta(api, base, document_id, times)
    if result["ingesta"]["estado"] != "ready":
        result["estado"] = "ingesta_" + result["ingesta"]["estado"]
        return

    reply = consultar(
        api, base, document_id, QUESTION,
        timeout=times["respuesta_segundos"], interval=times["sondeo_segundos"],
        tolerancia_visibilidad=5,
    )
    recovered = re.search(
        rf"(?<![A-Za-z0-9-]){re.escape(expected)}(?![A-Za-z0-9-])",
        reply.get("respuesta", ""), re.IGNORECASE,
    ) is not None
    cited = reply["estado"] == "ok"
    result["consulta"] = reply | {"codigo_recuperado": recovered, "documento_citado": cited}
    result["cumple"] = recovered and cited
    result["estado"] = {
        "ok": "cumple" if recovered else "codigo_no_recuperado",
        "sin_fuente_del_curso": "sin_cita_del_documento",
        "error": "consulta_fallida",
    }[reply["estado"]]


def run(config, repo, results_dir, *, processor, manages_services):
    formats = config["escenario"]["formatos"]
    expected_formats = REQUIRED | {CONTROL}
    if not isinstance(formats, list) or len(formats) != len(expected_formats) or set(formats) != expected_formats:
        raise ValueError("formatos debe incluir md, txt, pdf y docx una vez cada uno.")
    times = config["tiempos"]
    for key in ("ingesta_segundos", "respuesta_segundos", "sondeo_segundos"):
        value = times[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} debe ser un número positivo y finito.")

    expected = "NIV-" + uuid4().hex[:10].upper()
    lines = [
        "Ficha de registro del proyecto Faro Azul",
        "El proyecto Faro Azul es una iniciativa ficticia de seguimiento costero.",
        f"El codigo exacto de registro del proyecto Faro Azul es {expected}.",
        "Ese codigo identifica esta ficha en el inventario interno del proyecto.",
    ]
    materials = generar_materiales(lines)
    started = datetime.now().astimezone()
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"rnf-ci-2-{processor}-{started:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    report = {
        "inicio": started.isoformat(timespec="seconds"),
        "revision_checkout": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "configuracion": config,
        "procesador_solicitado": processor,
        "contenido_base": lines,
        "codigo_esperado": expected,
        "pregunta": QUESTION,
        "resultados": [],
    }

    def save():
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    print(f"Informe: {output}", flush=True)
    api = Api(config["escenario"]["url"])
    try:
        api.token = api.call("/auth/login", payload={
            "email": os.getenv("DEV_ACCOUNT_EMAIL", "dev@example.com"),
            "password": os.getenv("DEV_ACCOUNT_PASSWORD", "TutorIA-dev-123"),
        })["data"]["accessToken"]
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        report["error_preparacion"] = str(error)
        save()
        raise

    for format_name in formats:
        content = materials[format_name]
        filename = f"faro-azul.{format_name}"
        row = {
            "formato": format_name, "control": format_name == CONTROL,
            "archivo": filename, "tamano_bytes": len(content), "sha256": sha256(content).hexdigest(),
            "carga": None, "ingesta": None, "consulta": None, "cumple": False,
        }
        report["resultados"].append(row)
        try:
            verificar_formato(api, (filename, content), expected, times, row)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            row.update(estado="error", cumple=None, detalle=str(error))
        save()
        print(f'{format_name.upper()}: {row["estado"]}', flush=True)

    mandatory = [row["cumple"] for row in report["resultados"] if not row["control"]]
    control_ok = next(row["cumple"] for row in report["resultados"] if row["control"])
    report["resumen"] = {
        "cumple": None if None in mandatory else all(mandatory),
        "control_markdown_ok": control_ok,
    }
    save()
    print(json.dumps(report["resumen"], ensure_ascii=False, indent=2), flush=True)
    if any(row["cumple"] is None for row in report["resultados"]):
        return 2
    return 0 if report["resumen"]["cumple"] and control_ok else 1
