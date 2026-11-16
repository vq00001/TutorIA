class GpuNoDisponible(RuntimeError):
    """La fase solicitó GPU, pero el entorno no puede usarla."""
