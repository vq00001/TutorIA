import type { MaterialDto } from '../types'
import type { TutorIADataApi } from './databaseApi'

const PROCESSING_TIMEOUT_MS = 15 * 60_000
const POLL_INTERVAL_MS = 1000

/**
 * Sube los archivos iniciales y devuelve sus materiales listos.
 * Rechaza la operación ante fallos, vencimiento o documentos desaparecidos.
 */
export async function prepareCourseMaterials(
  api: Pick<TutorIADataApi, 'createCourseMaterials' | 'getCourseMaterials'>,
  courseId: string,
  files: File[],
): Promise<MaterialDto[]> {
  if (files.length === 0) return []

  let materials = (await api.createCourseMaterials(courseId, files)).data
  if (materials.length === 0) throw new Error('La carga no devolvió materiales para procesar.')
  const documentIds = new Set(materials.map((material) => material.id))
  // El backend concede 15 minutos a los trabajos de ingesta.
  const deadline = Date.now() + PROCESSING_TIMEOUT_MS

  while (materials.some((material) => material.status !== 'ready')) {
    if (materials.some((material) => material.status === 'failed')) {
      throw new Error('No se pudo procesar uno de los archivos.')
    }
    if (Date.now() >= deadline) {
      throw new Error('Se agotó el plazo para procesar los archivos.')
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    const latest = (await api.getCourseMaterials(courseId)).data
    materials = latest.filter((material) => documentIds.has(material.id))
    if (materials.length !== documentIds.size) {
      throw new Error('No se pudo comprobar el estado de todos los archivos.')
    }
  }

  return materials
}
