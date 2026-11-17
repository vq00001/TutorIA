"""Seguimiento de la indexación de documentos por la API."""
import time

from .api import ErrorApi


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
