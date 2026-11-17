"""Mide el RPO a partir de respaldos AC-2 existentes y restaurados en aislamiento."""
from datetime import datetime, timedelta, timezone
from hashlib import file_digest
import json
import math
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from .api import ErrorApi
from .rnf_ac2 import iniciar_sesion, operacion, verificar_material

RPO = timedelta(hours=24)


def instante(value):
    timestamp = datetime.fromisoformat(value)
    if timestamp.utcoffset() is None:
        raise ValueError("La fecha debe incluir una zona horaria.")
    return timestamp.astimezone(timezone.utc)


def resumir_configuracion(config):
    backup, infra = config["respaldo"], config["infra"]
    if config["ejecucion"]["arrancar_servicios"] or config["ejecucion"].get("procesador", "cpu") != "cpu":
        raise ValueError("AC-3 gestiona su propio stack aislado en CPU.")
    if backup["referencia"]:
        instante(backup["referencia"])
    if infra["url"].rstrip("/") not in {"http://127.0.0.1:18080", "http://localhost:18080"}:
        raise ValueError("AC-3 requiere el proxy local aislado en el puerto 18080.")
    timeout = infra["operacion_segundos"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("infra.operacion_segundos debe ser un número positivo y finito.")
    return {
        "Directorio de respaldos": backup["directorio"],
        "Referencia": backup["referencia"] or "inicio de la corrida (UTC)",
        "RPO máximo": "24 h",
        "Restauración": "volumen nuevo por candidato, con apagado posterior",
    }


def leer_candidato(report_path, reference, evidence):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    backup = report.get("respaldo")
    if backup is None:
        evidence["estado"] = "sin_respaldo"
        return None

    start = instante(backup["captura_inicio"])
    end = instante(backup["captura_fin"])
    available = instante(backup["disponible_desde"])
    if not start <= end <= available:
        raise ValueError("Marcas temporales del respaldo fuera de orden.")
    evidence.update({
        "captura_inicio": start.isoformat(),
        "captura_fin": end.isoformat(),
        "disponible_desde": available.isoformat(),
    })
    if available > reference:
        evidence["estado"] = "posterior_a_referencia"
        return None

    archive = report_path.with_suffix(".dump")
    if backup["archivo"] != archive.name:
        raise ValueError("El informe debe apuntar al .dump homónimo.")
    fixture = report["material"]
    for key in ("curso_id", "documento_id"):
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", fixture[key]):
            raise ValueError(f"Identificador de material inválido: {key}.")
    evidence["archivo"] = str(archive)
    return start, end, archive, backup["sha256"], fixture, evidence


def restaurar(repo, infra, candidate):
    _, _, archive, digest, fixture, evidence = candidate
    try:
        stream = archive.open("rb")
    except FileNotFoundError:
        evidence["estado"] = "archivo_ausente"
        return

    with stream:
        if stream.read(5) != b"PGDMP":
            evidence["estado"] = "formato_invalido"
            return
        stream.seek(0)
        evidence["sha256_archivo"] = file_digest(stream, "sha256").hexdigest()
        if evidence["sha256_archivo"] != digest:
            evidence["estado"] = "hash_no_coincide"
            return
        stream.seek(0)

        volume = f"tutoria-exp-rnf-ac-3-cpu-{uuid4().hex}-restaurado"
        evidence["volumen"] = volume
        print(f"Verificando {archive.name} en {volume}…", flush=True)
        timeout = infra["operacion_segundos"]
        try:
            operacion(repo, volume, "cpu", "up-db", timeout, solo_carga=True)
            operacion(repo, volume, "cpu", "restaurar", timeout, solo_carga=True, stdin=stream)
            operacion(repo, volume, "cpu", "up", timeout, solo_carga=True)
            api = iniciar_sesion(infra["url"], {
                "email": os.getenv("DEV_ACCOUNT_EMAIL", "dev@example.com"),
                "password": os.getenv("DEV_ACCOUNT_PASSWORD", "TutorIA-dev-123"),
            })
            try:
                checks, recovered_hash = verificar_material(api, fixture)
            except ErrorApi as error:
                if error.status != 404:
                    raise
                evidence.update(estado="material_no_recuperado", detalle=str(error))
                return
            evidence.update(
                estado="restaurado" if all(checks.values()) else "material_no_recuperado",
                comprobaciones=checks,
                descarga_sha256=recovered_hash,
            )
        finally:
            operacion(repo, volume, "cpu", "down", timeout, solo_carga=True)


def evaluar(reference, recovered, latest_end, uncertain):
    if recovered:
        minimum, maximum = reference - latest_end, reference - recovered[0]
        if maximum <= RPO:
            return "cumple", minimum, maximum
        if minimum > RPO and not uncertain:
            return "no_cumple", minimum, maximum
        return "indeterminado", minimum, maximum
    return "indeterminado", None, None


def run(config, repo, results_dir, *, processor, manages_services):
    resumir_configuracion(config)
    started = datetime.now(timezone.utc)
    settings = config["respaldo"]
    reference = instante(settings["referencia"]) if settings["referencia"] else started
    if reference > started:
        raise ValueError("La referencia del incidente debe ser igual o anterior al inicio de la prueba.")

    directory = (repo / Path(settings["directorio"])).resolve()
    evidence_rows, candidates = [], []
    for report_path in sorted(directory.glob("rnf-ac-2-*.json")):
        evidence = {"informe": str(report_path)}
        evidence_rows.append(evidence)
        try:
            candidate = leer_candidato(report_path, reference, evidence)
        except (AttributeError, KeyError, OSError, TypeError, ValueError) as error:
            evidence.update(estado="metadatos_insuficientes", detalle=f"{type(error).__name__}: {error}")
        else:
            if candidate:
                evidence["estado"] = "pendiente"
                candidates.append(candidate)

    recovered = latest_end = None
    uncertain = any(item["estado"] == "metadatos_insuficientes" for item in evidence_rows)
    # Usa el límite superior de la captura para priorizar los puntos posiblemente más recientes.
    for candidate in sorted(candidates, key=lambda item: (item[1], item[0]), reverse=True):
        evidence = candidate[-1]
        if recovered and candidate[1] <= recovered[0]:
            evidence["estado"] = "anterior_al_restaurado"
            continue
        try:
            restaurar(repo, config["infra"], candidate)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            evidence.update(estado="restauracion_no_verificada", detalle=f"{type(error).__name__}: {error}")
            uncertain = True
        if evidence["estado"] == "restaurado":
            latest_end = max(latest_end, candidate[1]) if latest_end else candidate[1]
            if recovered is None or candidate[0] > recovered[0]:
                recovered = candidate

    status, minimum, maximum = evaluar(reference, recovered, latest_end, uncertain)
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"rnf-ac-3-{started:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json"
    report = {
        "estado": status,
        "referencia": reference.isoformat(),
        "rpo_maximo_segundos": RPO.total_seconds(),
        "directorio": str(directory),
        "respaldo_verificado": recovered[-1]["informe"] if recovered else None,
        "antiguedad_minima_segundos": minimum.total_seconds() if minimum is not None else None,
        "antiguedad_maxima_segundos": maximum.total_seconds() if maximum is not None else None,
        "candidatos": evidence_rows,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"RNF-AC-3 · {status}. Informe: {output}", flush=True)
    return {"cumple": 0, "no_cumple": 1, "indeterminado": 2}[status]
