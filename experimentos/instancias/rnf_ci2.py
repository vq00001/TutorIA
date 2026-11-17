"""Verifica aceptación, indexación y recuperación de contenido por formato."""
from datetime import datetime
from hashlib import sha256
import json
import math
import os
import re
import subprocess
from uuid import uuid4

from .api import Api, ErrorApi
from .formatos_ci2 import generar_materiales
from .ingesta import observar_ingesta
from .rag import consultar


REQUIRED = {"txt", "pdf", "docx"}
DEFAULT_CONTROL = "md"
QUESTION = (
    "Segun la ficha del proyecto Faro Azul, cual es su codigo exacto de registro? "
    "Devuelve el codigo tal como aparece en el material del curso."
)


def resumir_configuracion(config):
    scenario = config["escenario"]
    control = scenario.get("control", DEFAULT_CONTROL)
    return {
        "Formatos en evaluación": ", ".join(item.upper() for item in scenario["formatos"] if item != control),
        "Control": control.upper(),
        "Orden de prueba": ", ".join(scenario["formatos"]),
        "URL": scenario["url"],
        "Plazo de indexación": f'{config["tiempos"]["ingesta_segundos"]} s',
        "Plazo de consulta": f'{config["tiempos"]["respuesta_segundos"]} s',
    }


def verificar_formato(api, material, expected, times, result):
    filename, content = material
    course = api.call("/courses", payload={
        "name": f'RNF-CI-2 {result["formato"].upper()} {uuid4().hex[:8]}',
        "description": "Un curso por formato para verificar recuperación documental.",
        "objectives": "Consultar el código de registro del proyecto Faro Azul.",
    })["data"]["id"]
    result["curso_id"] = course
    base = f"/courses/{course}"
    result["carga"] = {"estado": "en_curso"}
    try:
        uploaded, = api.call(
            f"{base}/documents", upload=(filename, content), expected_status=202,
        )["data"]
    except ErrorApi as error:
        rejected = error.status in (413, 415, 422)
        result.update(
            estado="carga_rechazada" if rejected else "error_de_carga",
            carga={"estado": "rechazada" if rejected else "error",
                   "http": error.status, "codigo": error.code, "detalle": str(error)},
            cumple=False if rejected else None,
        )
        return
    document_id = uploaded["documentId"]
    if uploaded["duplicate"] or not document_id:
        raise RuntimeError("La carga no devolvió un documento nuevo.")
    result["carga"] = {"estado": "aceptada", "http": 202, "documento_id": document_id, "job_id": uploaded["jobId"]}
    result["ingesta"] = {"estado": "en_curso"}
    result["ingesta"] = observar_ingesta(api, base, document_id, times)
    if result["ingesta"]["estado"] != "ready":
        result["estado"] = "ingesta_" + result["ingesta"]["estado"]
        return

    result["consulta"] = {"estado": "en_curso"}
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
    scenario = config["escenario"]
    formats = scenario["formatos"]
    control = scenario.get("control", DEFAULT_CONTROL)
    if (not isinstance(formats, list) or len(formats) < 2
            or any(not isinstance(item, str) or item not in REQUIRED | {DEFAULT_CONTROL} for item in formats)
            or len(set(formats)) != len(formats) or control not in formats):
        raise ValueError("Elige formatos distintos entre md, txt, pdf y docx, e incluye el formato de control.")
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
    variant = "rnf-ci-2-pdf" if set(formats) == {"pdf", "txt"} and control == "txt" else "rnf-ci-2"
    output = results_dir / f"{variant}-{processor}-{started:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    report = {
        "inicio": started.isoformat(timespec="seconds"),
        "revision_checkout": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "configuracion": config,
        "procesador_solicitado": processor,
        "alcance": {"formatos": formats, "control": control, "cubre_todos_los_formatos_del_rnf": REQUIRED <= set(formats)},
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
            "formato": format_name, "control": format_name == control,
            "archivo": filename, "tamano_bytes": len(content), "sha256": sha256(content).hexdigest(),
            "carga": {"estado": "no_ejecutada"}, "ingesta": {"estado": "no_ejecutada"},
            "consulta": {"estado": "no_ejecutada"}, "cumple": False,
        }
        report["resultados"].append(row)
        try:
            verificar_formato(api, (filename, content), expected, times, row)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            stage = next((name for name in ("carga", "ingesta", "consulta")
                          if row[name]["estado"] == "en_curso"), "preparacion")
            if stage != "preparacion":
                row[stage] = {"estado": "error", "detalle": str(error)}
            row.update(estado="error", cumple=None, fase_error=stage, detalle=str(error))
        save()
        print(
            f'{format_name.upper()}: carga={row["carga"]["estado"]}, '
            f'indexación={row["ingesta"]["estado"]}, consulta={row["consulta"]["estado"]}',
            flush=True,
        )

    mandatory = [row["cumple"] for row in report["resultados"] if not row["control"]]
    control_ok = next(row["cumple"] for row in report["resultados"] if row["control"])
    report["resumen"] = {
        "cumple": None if None in mandatory else all(mandatory),
        "control_ok": control_ok,
    }
    if control == DEFAULT_CONTROL:
        report["resumen"]["control_markdown_ok"] = control_ok
    save()
    print(json.dumps(report["resumen"], ensure_ascii=False, indent=2), flush=True)
    if any(row["cumple"] is None for row in report["resultados"]):
        return 2
    return 0 if report["resumen"]["cumple"] and control_ok else 1
