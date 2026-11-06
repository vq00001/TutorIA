export interface MaterialUploadResult {
  name: string
  data?: { materialId: string; versionId: string; runId: string; status: string }
  error?: { code: string }
}

const reasons: Record<string,string> = {
  FILE_TOO_LARGE: 'supera el máximo de 30 MB',
  UNSUPPORTED_FORMAT: 'solo se admiten TXT y Markdown',
  EMPTY_DOCUMENT: 'no contiene texto utilizable',
  INVALID_ENCODING: 'debe estar codificado en UTF-8',
  BINARY_DOCUMENT: 'contiene datos binarios',
  DUPLICATE_DOCUMENT: 'ya existe en este curso',
  UPLOAD_QUOTA_EXCEEDED: 'se superó la cuota de carga',
}

export function describeUploadResults(results: MaterialUploadResult[]) {
  const count = results.filter(result=>result.data).length
  const errors = results.filter(result=>result.error).map(result=>
    `${result.name}: ${reasons[result.error!.code] ?? 'no se pudo aceptar el archivo'}.`)
  return [`${count} archivo(s) aceptado(s).`, ...errors].join(' ')
}
