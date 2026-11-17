"""Respalda y restaura un curso aislado y verifica su uso desde TutorIA."""
from datetime import datetime
from hashlib import file_digest, sha256
import json
import math
import os
import re
import subprocess
import time
from uuid import uuid4

from .api import Api, ErrorApi
from .ingesta import observar_ingesta
from .rag import consultar


def resumir_configuracion(config):
    if config["ejecucion"]["arrancar_servicios"] is not True:
        raise ValueError("RNF-AC-2 requiere arrancar_servicios = true para aislar origen y destino.")
    if config["escenario"]["url"].rstrip("/") not in {"http://127.0.0.1:18080", "http://localhost:18080"}:
        raise ValueError("RNF-AC-2 requiere la URL del proxy local del stack aislado (puerto 18080).")
    return {
        "Material": f'Ficha Markdown del proyecto {config["escenario"]["proyecto"]}',
        "Respaldo": "PostgreSQL, con un curso de prueba",
        "Destino": "volumen PostgreSQL nuevo",
        "URL": config["escenario"]["url"],
        "Plazo por consulta": f'{config["tiempos"]["respuesta_segundos"]} s',
    }


def operacion(repo, volume, processor, action, timeout, **streams):
    script = repo / "experimentos" / "infra" / "arrancar-rag.sh"
    subprocess.run(
        ["bash", str(script), action],
        env={
            **os.environ,
            "TUTORIA_DB_VOLUME": volume,
            "TUTORIA_GPU": "1" if processor == "gpu" else "0",
            "TUTORIA_SOLO_CARGA": "0",
        },
        check=True, timeout=timeout, **streams,
    )


def iniciar_sesion(url, credentials):
    api = Api(url)
    api.token = api.call("/auth/login", payload=credentials)["data"]["accessToken"]
    return api


def verificar_curso(api, fixture, times):
    base = f'/courses/{fixture["curso_id"]}'
    document_id = fixture["documento_id"]
    course = api.call(base)["data"]
    doc_base = f"{base}/documents/{document_id}"
    state = api.call(f"{doc_base}/status")["data"]
    physical = api.call(f"{doc_base}/download", expected_status=200, raw=True)
    digest = sha256(physical).hexdigest()
    reply = consultar(
        api, base, document_id, fixture["pregunta"],
        timeout=times["respuesta_segundos"], interval=times["sondeo_segundos"],
        tolerancia_visibilidad=5,
    )
    code = fixture["codigo_ficticio"]
    answer = reply.get("respuesta", "")
    recovered = re.search(
        rf"(?<![A-Za-z0-9-]){re.escape(code)}(?![A-Za-z0-9-])", answer, re.IGNORECASE,
    ) is not None
    source_ids = {source["documentId"] for source in reply.get("fuentes", [])}
    checks = {
        "curso_recuperado": course["id"] == fixture["curso_id"] and course["name"] == fixture["curso"],
        "documento_listo": state["documentId"] == document_id and state["status"] == "ready",
        "archivo_identico": digest == fixture["sha256"],
        "codigo_recuperado": recovered,
        "documento_citado": reply["estado"] == "ok" and source_ids == {document_id},
    }
    return {"comprobaciones": checks, "descarga_sha256": digest, "consulta": reply, "cumple": all(checks.values())}


def run(config, repo, results_dir, *, processor, manages_services):
    source = config.get("entorno_ejecucion", {}).get("volumen_postgres", "")
    if not manages_services or not re.fullmatch(r"tutoria-exp-rnf-ac-2-(?:cpu|gpu)-[0-9a-f]{32}", source):
        raise ValueError("RNF-AC-2 solo opera en un volumen de prueba asignado por el lanzador.")
    target = f"{source}-restaurado"
    scenario, times = config["escenario"], config["tiempos"]
    project = scenario["proyecto"]
    if not isinstance(project, str) or not project.strip() or len(project) > 100 or "\n" in project or "\r" in project:
        raise ValueError("escenario.proyecto debe ser un nombre de hasta 100 caracteres en una sola línea.")
    for key in ("ingesta_segundos", "respuesta_segundos", "sondeo_segundos", "infra_segundos"):
        value = times[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} debe ser un número positivo y finito.")

    started = datetime.now().astimezone()
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"rnf-ac-2-{processor}-{started:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    backup = output.with_suffix(".dump")
    backup_temp = backup.with_suffix(".dump.tmp")
    code = f"REC-{uuid4().hex[:12].upper()}"
    content = (
        f"# Ficha de registro del proyecto {project}\n\n"
        f"El código exacto de registro del proyecto {project} es {code}.\n"
        "Este es un dato ficticio de la prueba de recuperación.\n"
    ).encode("utf-8")
    fixture = {
        "curso": f"RNF-AC-2 {project} {uuid4().hex[:8]}",
        "archivo": "ficha-recuperacion.md",
        "sha256": sha256(content).hexdigest(),
        "codigo_ficticio": code,
        "pregunta": f"Según la ficha del proyecto {project}, ¿cuál es su código exacto de registro?",
    }
    report = {
        "inicio": started.isoformat(timespec="seconds"),
        "revision_checkout": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "configuracion": config,
        "procesador_solicitado": processor,
        "volumen_origen": source,
        "volumen_destino": target,
        "material": fixture,
    }

    def save():
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    credentials = {
        "email": os.getenv("DEV_ACCOUNT_EMAIL", "dev@example.com"),
        "password": os.getenv("DEV_ACCOUNT_PASSWORD", "TutorIA-dev-123"),
    }
    print(f"Informe: {output}", flush=True)
    stage = "preparacion"
    try:
        api = iniciar_sesion(scenario["url"], credentials)
        course = api.call("/courses", payload={
            "name": fixture["curso"], "description": "Curso sintético de recuperación.",
            "objectives": "Comprobar la recuperación documental después de restaurar PostgreSQL.",
        })["data"]
        fixture["curso_id"] = course["id"]
        base = f'/courses/{course["id"]}'
        uploaded, = api.call(f"{base}/documents", upload=(fixture["archivo"], content))["data"]
        if uploaded["duplicate"] or not uploaded["documentId"]:
            raise RuntimeError("La carga no devolvió un documento nuevo.")
        fixture["documento_id"] = uploaded["documentId"]
        report["ingesta"] = observar_ingesta(api, base, uploaded["documentId"], times)
        save()
        if report["ingesta"]["estado"] != "ready":
            raise RuntimeError("No se pudo preparar un documento indexado en el origen.")

        stage = "control_origen"
        report["antes"] = verificar_curso(api, fixture, times)
        save()
        if not report["antes"]["cumple"]:
            raise RuntimeError("El control antes del respaldo falló. No se puede evaluar la restauración.")

        stage = "respaldo"
        print("Generando respaldo del volumen de origen…", flush=True)
        before_backup = time.monotonic()
        with backup_temp.open("wb") as stream:
            operacion(repo, source, processor, "respaldo", times["infra_segundos"], stdout=stream)
        with backup_temp.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise RuntimeError("El respaldo no tiene la cabecera del formato personalizado de PostgreSQL.")
            stream.seek(0)
            backup_hash = file_digest(stream, "sha256").hexdigest()
        backup_temp.replace(backup)
        report["respaldo"] = {
            "archivo": backup.name, "tamano_bytes": backup.stat().st_size,
            "sha256": backup_hash, "segundos": time.monotonic() - before_backup,
        }
        save()

        stage = "restauracion"
        print(f"Restaurando en el volumen aislado {target}…", flush=True)
        recovery_started = time.monotonic()
        operacion(repo, source, processor, "down", times["infra_segundos"])
        operacion(repo, target, processor, "up-db", times["infra_segundos"])
        restore_started = time.monotonic()
        with backup.open("rb") as stream:
            operacion(repo, target, processor, "restaurar", times["infra_segundos"], stdin=stream)
        report["restauracion_segundos"] = time.monotonic() - restore_started
        save()

        stage = "comprobacion_destino"
        operacion(repo, target, processor, "up", times["infra_segundos"])
        restored = iniciar_sesion(scenario["url"], credentials)
        try:
            report["despues"] = verificar_curso(restored, fixture, times)
        except ErrorApi as error:
            if error.status != 404:
                raise
            report["despues"] = {"cumple": False, "detalle": str(error)}
        report["recuperacion_segundos"] = time.monotonic() - recovery_started
        report["resumen"] = {"cumple_escenario": report["despues"]["cumple"]}
    except Exception as error:
        report["resumen"] = {"cumple_escenario": None, "fase_interrumpida": stage, "detalle": str(error)}
        save()
        raise
    finally:
        backup_temp.unlink(missing_ok=True)

    save()
    print(json.dumps(report["resumen"], ensure_ascii=False, indent=2), flush=True)
    return 0 if report["resumen"]["cumple_escenario"] else 1
