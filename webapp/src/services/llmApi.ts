import type { ConversationDto, JsonApiResponse } from '../types'
import { authorizedFetch } from './authApi'
import { responseError } from './utils'

interface AskRequest {
  question: string
  includeDocuments: boolean
}

export async function uploadPromptFiles(courseId: string, files: File[]) {
  if (files.length === 0) return

  const form = new FormData()
  files.forEach((file) => form.append('files', file))

  const response = await authorizedFetch(
    `/api/v1/courses/${toApiCourseId(courseId)}/documents`,
    { method: 'POST', body: form },
  )

  if (!response.ok) {
    throw new Error(await responseError(response, 'No se pudieron subir los archivos.'))
  }
}

export async function sendPrompt(
  courseId: string,
  question: string,
): Promise<JsonApiResponse<ConversationDto>> {
  const body: AskRequest = { question, includeDocuments: true }
  const response = await authorizedFetch(
    `/api/v1/courses/${toApiCourseId(courseId)}/conversations/direct`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )

  if (!response.ok) {
    throw new Error(await responseError(response, 'No se pudo enviar la pregunta.'))
  }

  const result: unknown = await response.json()
  if (!isConversationResponse(result)) {
    throw new Error('El tutor devolvió una respuesta con formato inválido.')
  }

  return result
}

function isConversationResponse(
  value: unknown,
): value is JsonApiResponse<ConversationDto> {
  if (!value || typeof value !== 'object' || !('data' in value)) return false

  const data = value.data
  return Boolean(
    data &&
      typeof data === 'object' &&
      'messages' in data &&
      Array.isArray(data.messages) &&
      data.messages.every(
        (message) =>
          message &&
          typeof message === 'object' &&
          'role' in message &&
          (message.role === 'user' || message.role === 'assistant') &&
          'content' in message &&
          typeof message.content === 'string',
      ),
  )
}

// The current UI's demo courses use slugs, while the API contract requires UUIDs.
// Keep demo interactions compatible with the API stubs until courses come from the backend.
function toApiCourseId(courseId: string) {
  const demoCourseIds: Record<string, string> = {
    'calculo-i': '00000000-0000-4000-8000-000000000002',
    fisica: '00000000-0000-4000-8000-00000000000c',
    programacion: '00000000-0000-4000-8000-00000000000d',
  }
  return demoCourseIds[courseId] ?? courseId
}


