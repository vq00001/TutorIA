"""Comprueba el aislamiento de cursos en las respuestas y fuentes del tutor."""
from datetime import datetime
import json
import math
import re
from secrets import token_urlsafe
import subprocess
from uuid import uuid4

from .api import Api
from .ingesta import observar_ingesta
from .rag import consultar


def resumir_configuracion(config):
    return {
        "Cursos": "A y B con material propio; C sin material",
        "Cuentas": "una cuenta de prueba por curso",
        "Tema compartido": config["escenario"]["proyecto"],
        "URL": config["escenario"]["url"],
        "Plazo por consulta": f'{config["tiempos"]["respuesta_segundos"]} s',
    }


def preparar_curso(api, alias, code, project, times):
    credentials = {
        "email": f"rnf-s-5-{uuid4().hex}@example.com",
        "password": token_urlsafe(24),
    }
    user = api.call("/auth/register", payload={
        **credentials,
        "displayName": f"Prueba RNF-S-5 {alias}",
    })["data"]
    if user["status"] != "active":
        raise RuntimeError("La cuenta de prueba quedó pendiente. Usa el arranque local con verificación automática.")
    session = api.call("/auth/login", payload=credentials)["data"]
    if session["user"]["id"] != user["id"]:
        raise RuntimeError("La sesión no corresponde a la cuenta registrada.")
    api.token = session["accessToken"]
    course = api.call("/courses", payload={
        "name": f"RNF-S-5 Clase {alias} {uuid4().hex[:8]}",
        "description": "Clase de prueba para comprobar el aislamiento documental.",
        "objectives": f"Consultar la ficha del proyecto {project}.",
    })["data"]
    case = {"clase": alias, "usuario_id": user["id"], "curso_id": course["id"], "documento_id": None}
    if code is None:
        return case

    content = (
        f"# Ficha de registro del proyecto {project}\n\n"
        f"El proyecto {project} es un ejercicio ficticio de archivo documental.\n\n"
        f"El código exacto de registro del proyecto {project} es {code}. "
        "Ese código identifica la ficha en el inventario.\n"
    ).encode("utf-8")
    base = f'/courses/{course["id"]}'
    uploaded, = api.call(f"{base}/documents", upload=("ficha-proyecto.md", content))["data"]
    if uploaded["duplicate"] or not uploaded["documentId"]:
        raise RuntimeError("La carga no devolvió un documento nuevo.")
    case["documento_id"] = uploaded["documentId"]
    case["ingesta"] = observar_ingesta(api, base, case["documento_id"], times)
    return case


def evaluar_respuesta(case, reply, codes):
    source_ids = [source["documentId"] for source in reply.get("fuentes", [])]
    answer = reply.get("respuesta", "")
    own_code = codes.get(case["clase"])
    leaked_codes = [alias for alias, code in codes.items()
                    if code != own_code and code.casefold() in answer.casefold()]
    own_document = case["documento_id"]
    foreign_sources = [source_id for source_id in source_ids if source_id != own_document]
    own_found = bool(own_code and re.search(
        rf"(?<![A-Za-z0-9-]){re.escape(own_code)}(?![A-Za-z0-9-])", answer, re.IGNORECASE,
    ))
    own_cited = own_document is not None and own_document in source_ids
    violations = []
    if leaked_codes:
        violations.append("codigo_ajeno_en_respuesta")
    if foreign_sources:
        violations.append("cita_fuera_de_la_clase")
    if reply["estado"] == "error":
        violations.append("consulta_sin_respuesta_valida")
    elif own_code:
        if not own_found:
            violations.append("codigo_propio_no_recuperado")
        if not own_cited:
            violations.append("documento_propio_no_citado")
    return {
        "consulta": reply,
        "codigo_propio_recuperado": own_found if own_code else None,
        "documento_propio_citado": own_cited if own_document else None,
        "clases_con_codigo_filtrado": leaked_codes,
        "documentos_citados_fuera_de_la_clase": foreign_sources,
        "incidencias": violations,
        "cumple": not violations,
    }


def run(config, repo, results_dir, *, processor, manages_services):
    scenario, times = config["escenario"], config["tiempos"]
    project = scenario["proyecto"]
    if not isinstance(project, str) or not project.strip() or "\n" in project or "\r" in project:
        raise ValueError("escenario.proyecto debe ser un nombre en una sola línea.")
    for key in ("ingesta_segundos", "respuesta_segundos", "sondeo_segundos"):
        value = times[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} debe ser un número positivo y finito.")

    codes = {"A": f"A-{uuid4().hex[:12].upper()}", "B": f"B-{uuid4().hex[:12].upper()}"}
    question = (
        f"Según la ficha del proyecto {project}, ¿cuál es su código exacto de registro? "
        "Devuelve el código tal como aparece en el material del curso."
    )
    started = datetime.now().astimezone()
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"rnf-s-5-{processor}-{started:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    report = {
        "inicio": started.isoformat(timespec="seconds"),
        "revision_checkout": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "configuracion": config,
        "procesador_solicitado": processor,
        "codigos_ficticios": codes,
        "pregunta": question,
        "casos": [],
    }

    def save():
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    print(f"Informe: {output}", flush=True)
    clients = {}
    try:
        for alias in ("A", "B", "C"):
            api = clients[alias] = Api(scenario["url"])
            case = preparar_curso(api, alias, codes.get(alias), project, times)
            report["casos"].append(case)
            save()
            if alias in codes and case["ingesta"]["estado"] != "ready":
                raise RuntimeError(f'El documento de la clase {alias} no quedó listo: {case["ingesta"]["estado"]}.')
        for field in ("usuario_id", "curso_id"):
            if len({row[field] for row in report["casos"]}) != 3:
                raise RuntimeError(f"La prueba requiere tres valores distintos de {field}.")
        if len({row["documento_id"] for row in report["casos"]}) != 3:
            raise RuntimeError("La prueba requiere un documento distinto en A y B.")
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        report["resumen"] = {"cumple": None, "error_preparacion": str(error)}
        save()
        raise

    for case in report["casos"]:
        alias = case["clase"]
        reply = consultar(
            clients[alias], f'/courses/{case["curso_id"]}', case["documento_id"], question,
            timeout=times["respuesta_segundos"], interval=times["sondeo_segundos"],
            tolerancia_visibilidad=5,
        )
        case.update(evaluar_respuesta(case, reply, codes))
        save()
        outcome = ", ".join(case["incidencias"]) or "cumple"
        print(f"Clase {alias}: {outcome}", flush=True)
    report["resumen"] = {"cumple": all(case["cumple"] for case in report["casos"])}
    save()
    print(json.dumps(report["resumen"], ensure_ascii=False, indent=2), flush=True)
    return 0 if report["resumen"]["cumple"] else 1
