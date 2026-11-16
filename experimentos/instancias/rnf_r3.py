"""Mide el tiempo que tarda un documento cargado en quedar indexado."""
from datetime import datetime
from hashlib import sha256
import json
import math
import os
import subprocess
import time
from uuid import uuid4

from .api import Api, ErrorApi


TEMPLATE = "recursos/integracion_multivariable_calculo_iii.md"


def resumir_configuracion(config):
    criterion = config["criterio"]
    scenario = config["escenario"]
    return {
        "Tamaño del archivo": f'{criterion["tamano_bytes"]:_} bytes'.replace("_", "."),
        "Umbral de procesamiento": f'{criterion["umbral_segundos"]} s',
        "Documento": scenario["documento"] or "Markdown generado a partir de los apuntes de Cálculo III",
        "URL": scenario["url"],
        "Observación": f'{config["tiempos"]["observacion_segundos"]} s',
    }


def cargar_material(repo, document, size):
    if document:
        path = repo / document
        if path.suffix.lower() not in {".txt", ".md", ".pdf"}:
            raise ValueError("El documento debe ser texto, Markdown o PDF.")
        if path.stat().st_size != size:
            raise ValueError(f"El documento debe pesar exactamente {size} bytes.")
        return path.name, path.read_bytes()

    seed = (repo / TEMPLATE).read_bytes() + b"\n\n"
    copies, remainder = divmod(size, len(seed))
    # El corte puede atravesar un carácter UTF-8; los espacios completan el tamaño exacto.
    tail = seed[:remainder].decode("utf-8", errors="ignore").encode("utf-8")
    return "calculo-iii-rnf-r3.md", seed * copies + tail.ljust(remainder, b" ")


def medir_procesamiento(api, course, material, threshold, times):
    endpoint = f"/courses/{course}/documents"
    upload_started = time.monotonic()
    try:
        uploaded, = api.call(endpoint, upload=material, timeout=times["carga_segundos"])["data"]
    except ErrorApi as error:
        if error.status != 413:
            raise
        return {
            "estado": "carga_rechazada", "cumple": False, "http": 413,
            "carga_segundos": time.monotonic() - upload_started, "detalle": str(error),
        }
    started = time.monotonic()
    document_id = uploaded["documentId"]
    if uploaded["duplicate"] or not document_id:
        raise RuntimeError("La API no devolvió un documento nuevo para medir.")
    result = {
        "documento_id": document_id,
        "job_id": uploaded["jobId"],
        "carga_segundos": started - upload_started,
        "procesamiento_segundos": None,
        "estados": [],
        "cumple": False,
    }
    print(f"Documento aceptado en {result['carga_segundos']:.2f} s. Esperando indexación…", flush=True)
    deadline = started + times["observacion_segundos"]
    status_path = f"{endpoint}/{document_id}/status"
    previous = None
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            response = api.call(status_path, timeout=min(remaining, 10))["data"]
        except ErrorApi as error:
            # El primer GET puede llegar antes de que se confirme la transacción del POST.
            if error.status != 404 or previous or time.monotonic() - started > 5:
                raise
        except TimeoutError:
            if time.monotonic() < deadline:
                raise
            break
        else:
            elapsed = time.monotonic() - started
            status = response["status"]
            if status != previous:
                result["estados"].append({"estado": status, "segundos": elapsed})
                print(f"  {elapsed:.2f} s · {status}", flush=True)
                previous = status
            if status == "ready":
                result.update(estado="ready", procesamiento_segundos=elapsed, cumple=elapsed <= threshold)
                return result
            if status == "failed":
                result.update(estado="failed", error_procesamiento=response["errorCode"])
                return result
        time.sleep(max(0, min(times["sondeo_segundos"], deadline - time.monotonic())))
    result.update(estado="plazo_agotado", observacion_segundos=time.monotonic() - started)
    return result


def run(config, repo, results_dir, *, processor, manages_services):
    criterion, scenario, times = config["criterio"], config["escenario"], config["tiempos"]
    size, threshold = criterion["tamano_bytes"], criterion["umbral_segundos"]
    if type(size) is not int or not 1 <= size <= 30_000_000:
        raise ValueError("tamano_bytes debe ser un entero entre 1 y 30.000.000.")
    for key, value in {"umbral_segundos": threshold, **times}.items():
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} debe ser un número positivo y finito.")
    if times["observacion_segundos"] < threshold:
        raise ValueError("observacion_segundos debe cubrir al menos el umbral.")
    filename, material = cargar_material(repo, scenario["documento"], size)
    if len(material) != size:
        raise ValueError(f"El documento debe pesar exactamente {size} bytes.")

    started = datetime.now().astimezone()
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"rnf-r3-{processor}-{started:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    report = {
        "inicio": started.isoformat(timespec="seconds"),
        "revision_checkout": subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "configuracion": config,
        "procesador_solicitado": processor,
        "documento": {
            "nombre": filename, "tamano_bytes": size, "sha256": sha256(material).hexdigest(),
        },
        "medicion": "Recepción de la confirmación de carga hasta observar ready por la API.",
    }

    def save():
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    print(f"Informe: {output}", flush=True)
    api = Api(scenario["url"])
    try:
        api.token = api.call("/auth/login", payload={
            "email": os.getenv("DEV_ACCOUNT_EMAIL", "dev@example.com"),
            "password": os.getenv("DEV_ACCOUNT_PASSWORD", "TutorIA-dev-123"),
        })["data"]["accessToken"]
        course = api.call("/courses", payload={
            "name": f"RNF-R-3 Ingesta {uuid4().hex[:8]}",
            "description": "Prueba de indexación de un documento.",
            "objectives": "Medir la disponibilidad del documento para consultas.",
        })["data"]["id"]
        report["curso_id"] = course
        save()
        report["resultado"] = medir_procesamiento(api, course, (filename, material), threshold, times)
    except Exception as error:
        report["resultado"] = {"estado": "error", "cumple": None, "detalle": str(error)}
        save()
        raise
    save()
    print(json.dumps(report["resultado"], ensure_ascii=False, indent=2), flush=True)
    return 0 if report["resultado"]["cumple"] else 1
