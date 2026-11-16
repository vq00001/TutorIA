#!/usr/bin/env python3
"""Prepara y ejecuta un experimento de TutorIA o todos los registrados."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tomllib

sys.dont_write_bytecode = True
from instancias import rnf_r1
from instancias.estado import GpuNoDisponible

DIRECTORY = Path(__file__).resolve().parent
EXPERIMENTS = {
    "rnf-r1": (rnf_r1.run, rnf_r1.resumir_configuracion, "arrancar-rag.sh"),
}


def mostrar_configuracion(name, processor, configured_processor, services, details):
    processor_label = processor.upper()
    if configured_processor == "both":
        phase = "1/2" if processor == "cpu" else "2/2"
        processor_label = f"{processor_label} (fase {phase} de both)"
    rows = {"Procesador de esta fase": processor_label, "Servicios": services}
    rows.update(details)
    lines = [f"=== {name} · {processor_label} ==="]
    lines.extend(f"  {label}: {value}" for label, value in rows.items())
    print("\n".join(lines), flush=True)


def preparar_servicios(script, processor):
    command = ["bash", str(DIRECTORY / "infra" / script), "up"]
    environment = {**os.environ, "TUTORIA_GPU": "1" if processor == "gpu" else "0"}
    gpu_error = None
    with subprocess.Popen(
        command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    ) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            message = line.lower()
            if processor == "gpu" and (
                "failed to discover gpu vendor from cdi" in message
                or ("unresolvable cdi devices" in message and "nvidia.com/gpu" in message)
                or ("could not select device driver" in message and "gpu" in message)
                or "nvidia-container-cli: initialization error" in message
            ):
                gpu_error = line.strip()
        returncode = process.wait()
    if returncode:
        if gpu_error:
            raise GpuNoDisponible(gpu_error)
        raise subprocess.CalledProcessError(returncode, command)


def mostrar_omision(name, error):
    badge = "[OMITIDO]"
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        badge = f"\033[1;33m{badge}\033[0m"
    print(f"{badge} {name} (GPU): GPU no disponible. {error}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "seleccion", nargs="?", default="todos",
        choices=[*EXPERIMENTS, "todos", "preparar"],
        help="ID del experimento, todos (por defecto) o preparar (solo servicios).",
    )
    selection = parser.parse_args().seleccion
    prepare_only = selection == "preparar"
    names = (selection,) if selection in EXPERIMENTS else EXPERIMENTS
    started = {}
    status = 0
    skipped = 0
    for name in names:
        try:
            run, summarize, setup_script = EXPERIMENTS[name]
            with (DIRECTORY / "config" / f"{name}.toml").open("rb") as config_file:
                config = tomllib.load(config_file)
            execution = config["ejecucion"]
            start_services = execution["arrancar_servicios"]
            if not isinstance(start_services, bool):
                raise ValueError("ejecucion.arrancar_servicios debe ser true o false.")
            processor = execution.get("procesador", "cpu")
            if processor not in ("cpu", "gpu", "both"):
                raise ValueError('ejecucion.procesador debe ser "cpu", "gpu" o "both".')
            manages_services = bool(setup_script and start_services)
            if processor == "both" and not manages_services:
                raise ValueError('"both" requiere arrancar_servicios = true para cambiar de modo.')
            processors = ("cpu", "gpu") if processor == "both" else (processor,)
            if prepare_only:
                processors = processors[:1]
            details = summarize(config)
        except Exception as error:
            print(f"{name}: configuración inválida: {error}", file=sys.stderr)
            status = 2
            continue

        for device in processors:
            needs_setup = manages_services and started.get(setup_script) != device
            services = "sin script de arranque"
            if setup_script:
                services = f"arrancar con {setup_script}" if needs_setup else "usar instancia existente"
            mostrar_configuracion(name, device, processor, services, details)
            try:
                if needs_setup:
                    started.pop(setup_script, None)
                    print(f"Preparando {setup_script}", flush=True)
                    preparar_servicios(setup_script, device)
                    started[setup_script] = device
                    print(f"Servicios listos para {device.upper()}.", flush=True)
                code = 0 if prepare_only else run(
                    config, DIRECTORY.parent, DIRECTORY / "resultados",
                    processor=device, manages_services=manages_services,
                )
            except GpuNoDisponible as error:
                mostrar_omision(name, error)
                skipped += 1
                continue
            except Exception as error:
                print(f"{name} ({device}): corrida interrumpida: {type(error).__name__}: {error}", file=sys.stderr)
                code = 2
            status = max(status, code)
    if skipped:
        print(f"Fases GPU omitidas: {skipped}", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
