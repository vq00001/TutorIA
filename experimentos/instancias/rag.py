"""Consulta asíncrona del tutor y comprobación de la fuente del curso."""
import time

from .api import ErrorApi


def consultar(api, base, document_id, question, *, timeout, interval, tolerancia_visibilidad=0):
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
        visible_until = time.monotonic() + tolerancia_visibilidad
        observed = False
        while True:
            try:
                job = call(f'{base}/jobs/{sent["jobId"]}')["data"]
            except ErrorApi as error:
                if error.status != 404 or observed or time.monotonic() >= visible_until:
                    raise
                time.sleep(min(interval, max(0, visible_until - time.monotonic())))
                continue
            observed = True
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
    return result
