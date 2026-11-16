#!/usr/bin/env python3
"""Piloto y medición de RNF-R-1 por la API pública del flujo RAG."""
from datetime import datetime
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from .estado import GpuNoDisponible

class Api:
    def __init__(self, base):
        self.base = base.rstrip("/") + "/api/v1"
        self.token = ""

    def call(self, path, *, payload=None, upload=None, timeout=10):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        if upload is not None:
            filename, content = upload
            filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            boundary = uuid4().hex
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            part = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            )
            data = part.encode() + content + f"\r\n--{boundary}--\r\n".encode()
        if data is not None:
            headers["Idempotency-Key"] = str(uuid4())
        request = Request(self.base + path, data=data, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"{path}: HTTP {error.code}") from error


def prepare(api, material, ingestion_timeout):
    print("Creando curso de prueba y cargando los apuntes…", flush=True)
    course = api.call("/courses", payload={
        "name": f"RNF-R-1 Cálculo III {uuid4().hex[:8]}",
        "description": "Curso aislado para medir la latencia del tutor.",
        "objectives": "Consultar apuntes de integración multivariable.",
    })["data"]["id"]
    base = f"/courses/{course}"
    upload = api.call(f"{base}/documents", upload=material)["data"][0]
    document_id = upload["documentId"]
    deadline = time.monotonic() + ingestion_timeout
    while time.monotonic() < deadline:
        status = api.call(f"{base}/documents/{document_id}/status")["data"]
        if status["status"] == "ready":
            return base, document_id
        if status["status"] == "failed":
            raise RuntimeError(f'Falló la ingesta: {status["errorCode"]}')
        time.sleep(2)
    raise TimeoutError("El documento no quedó listo dentro del plazo de ingesta.")


def measure(api, base, document_id, question, *, timeout, interval, threshold):
    result = {"pregunta": question, "estado": "error"}
    started = time.monotonic()

    def call(path, **kwargs):
        left = timeout - (time.monotonic() - started)
        if left <= 0:
            raise TimeoutError("Se agotó el plazo de observación.")
        return api.call(path, timeout=min(left, 10), **kwargs)

    try:
        sent = call(f"{base}/conversations", payload={
            "question": question, "includeDocuments": True,
        })["data"]
        result["job_id"] = sent["jobId"]
        while True:
            job = call(f'{base}/jobs/{sent["jobId"]}')["data"]
            if job["state"] == "failed":
                raise RuntimeError(job.get("errorCode") or "Falló la generación.")
            if job["state"] == "succeeded":
                break
            time.sleep(min(interval, max(0, timeout - (time.monotonic() - started))))
        history = call(f'{base}/conversations/{sent["conversationId"]}')
        elapsed = time.monotonic() - started
        reply = next((message for message in history["messages"]
                      if message["role"] == "assistant"
                      and message["inReplyTo"] == sent["messageId"]), None)
        if not reply or not reply["content"].strip():
            raise RuntimeError("La conversación no contiene una respuesta válida.")
        sources = reply["sourceDocuments"]
        result.update(
            segundos=elapsed,
            respuesta=reply["content"],
            fuentes=sources,
            estado="ok" if any(source["documentId"] == document_id for source in sources)
            else "sin_fuente_del_curso",
        )
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        result.update(segundos=time.monotonic() - started, detalle=str(error))
    result["a_tiempo"] = result["estado"] == "ok" and result["segundos"] <= threshold
    return result


def summarize(records, threshold):
    valid_times = sorted(row["segundos"] for row in records if row["estado"] == "ok")
    valid_count = len(valid_times)
    rank = math.ceil(0.90 * len(records))
    p90 = valid_times[rank - 1] if 0 < rank <= valid_count else None
    valid_rank = math.ceil(0.90 * valid_count)
    return {
        "consultas": len(records),
        "a_tiempo": sum(row["a_tiempo"] for row in records),
        "tardias_con_fuente": sum(row["estado"] == "ok" and not row["a_tiempo"] for row in records),
        "fallidas_o_sin_fuente": sum(row["estado"] != "ok" for row in records),
        "rango_p90": rank,
        "p90_segundos": p90,
        "p90_respuestas_con_fuente_segundos": valid_times[valid_rank - 1] if valid_rank else None,
        "cumple": p90 is not None and p90 <= threshold,
    }


def resumir_configuracion(config):
    mode = config["ejecucion"]["modo"]
    scenario = config["escenario"]
    times = config["tiempos"]
    question_count = 0 if mode == "piloto" else len(config["preguntas"]["consultas"])
    return {
        "Modo": mode,
        "Documento": scenario["documento"],
        "URL": scenario["url"],
        "Consultas medidas": question_count,
        "Umbral p90": f'{config["criterio"]["umbral_segundos"]} s',
        "Observación por consulta": f'{times["observacion_segundos"]} s',
    }


def run(config, repo, results_dir, *, processor, manages_services):
    mode = config["ejecucion"]["modo"]
    if mode not in ("completo", "piloto"):
        raise ValueError('ejecucion.modo debe ser "completo" o "piloto".')
    scenario = config["escenario"]
    prompts = config["preguntas"]
    times = config["tiempos"]
    threshold = config["criterio"]["umbral_segundos"]
    questions = prompts["consultas"]
    if not isinstance(questions, list) or not questions or any(
        not isinstance(question, str) or not question.strip() for question in questions
    ):
        raise ValueError("consultas debe contener al menos una pregunta no vacía.")
    if not isinstance(prompts["piloto"], str) or not prompts["piloto"].strip():
        raise ValueError("piloto debe contener una pregunta.")
    numeric_settings = {
        "umbral_segundos": threshold,
        "observacion_segundos": times["observacion_segundos"],
        "ingesta_segundos": times["ingesta_segundos"],
        "sondeo_segundos": times["sondeo_segundos"],
    }
    for key, value in numeric_settings.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} debe ser un número positivo y finito.")
    if times["observacion_segundos"] < threshold:
        raise ValueError("La observación debe cubrir al menos el umbral.")
    material_path = repo / scenario["documento"]
    if material_path.suffix.lower() not in {".md", ".txt", ".pdf"}:
        raise ValueError("El documento debe ser Markdown, texto o PDF.")
    material = material_path.read_bytes()
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    started = datetime.now().astimezone()
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"rnf-r1-{processor}-{started:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    report = {
        "inicio": started.isoformat(timespec="seconds"),
        "revision_checkout": revision,
        "configuracion": config,
        "procesador_solicitado": processor,
        "documento_sha256": hashlib.sha256(material).hexdigest(),
        "medicion": "API pública, desde el envío hasta recibir el historial con la respuesta.",
        "consultas": [],
    }

    def save():
        temporary = output.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        temporary.replace(output)

    print(f"Informe: {output}", flush=True)
    save()
    api = Api(scenario["url"])
    try:
        api.token = api.call("/auth/login", payload={
            "email": os.getenv("DEV_ACCOUNT_EMAIL", "dev@example.com"),
            "password": os.getenv("DEV_ACCOUNT_PASSWORD", "TutorIA-dev-123"),
        })["data"]["accessToken"]
        base, document_id = prepare(
            api, (material_path.name, material), times["ingesta_segundos"],
        )
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        report["error_preparacion"] = str(error)
        save()
        raise
    report["curso_id"] = base.rsplit("/", 1)[-1]
    report["documento_id"] = document_id

    def timed_question(question):
        return measure(
            api, base, document_id, question,
            timeout=times["observacion_segundos"], interval=times["sondeo_segundos"],
            threshold=threshold,
        )

    print("Ejecutando piloto…", flush=True)
    pilot = report["piloto"] = timed_question(prompts["piloto"])
    save()
    print(f'Piloto: {pilot["estado"]} en {pilot["segundos"]:.2f} s', flush=True)
    if pilot["estado"] != "ok":
        return 2
    if manages_services:
        try:
            models = subprocess.check_output(
                ["docker", "exec", "tutoria-rnf-r1-ollama", "ollama", "ps"],
                text=True, timeout=10,
            )
            report["ollama_ps"] = models
            model = next((line for line in models.splitlines()
                          if line.split()[:1] == ["qwen3:1.7b"]), "")
            if processor == "gpu" and "100% CPU" in model:
                raise GpuNoDisponible("Ollama cargó qwen3:1.7b en CPU.")
            expected = "GPU" if processor == "gpu" else "100% CPU"
            if expected not in model:
                raise RuntimeError(f"Ollama no confirmó el modo {processor.upper()} para qwen3:1.7b.")
        except GpuNoDisponible as error:
            report["estado"] = "omitido"
            report["motivo_omision"] = str(error)
            save()
            raise
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            report["error_procesador"] = str(error)
            save()
            raise RuntimeError(str(error)) from error
        save()
        print(f"Ollama confirmó el uso de {processor.upper()} para qwen3:1.7b.", flush=True)
    if mode == "piloto":
        return 0
    for number, question in enumerate(questions, 1):
        row = timed_question(question)
        report["consultas"].append(row)
        save()
        print(f'{number:02d}/{len(questions)}  {row["segundos"]:6.2f} s  {row["estado"]}', flush=True)
    report["resumen"] = summarize(report["consultas"], threshold)
    save()
    print(json.dumps(report["resumen"], ensure_ascii=False, indent=2))
    return 0 if report["resumen"]["cumple"] else 1
