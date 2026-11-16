import type { JsonApiResponse, MessageDto } from '../types'
import { authorizedFetch } from './authApi'
import { responseError } from './utils'

interface AskRequest {
  question: string
  includeDocuments: boolean
}

interface AcceptedMessage {
  conversationId: string
  messageId: string
  generationId: string
  jobId: string
}

interface JobResponse {
  data: { state: 'queued' | 'running' | 'succeeded' | 'failed'; errorCode: string | null }
}

interface HistoryResponse {
  messages: MessageDto[]
}

export async function uploadPromptFiles(courseId: string, files: File[]) {
  if (files.length === 0) return

  const form = new FormData()
  files.forEach((file) => form.append('files', file))

  const response = await authorizedFetch(
    `/api/v1/courses/${toApiCourseId(courseId)}/documents`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: form,
    },
  )

  if (!response.ok) {
    throw new Error(await responseError(response, 'No se pudieron subir los archivos.'))
  }
}

export async function sendPrompt(
  courseId: string,
  question: string,
  conversationId?: string,
): Promise<JsonApiResponse<AcceptedMessage>> {
  const body: AskRequest = { question, includeDocuments: true }
  const path = conversationId
    ? `/api/v1/courses/${toApiCourseId(courseId)}/conversations/${conversationId}/messages`
    : `/api/v1/courses/${toApiCourseId(courseId)}/conversations`
  const response = await authorizedFetch(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(body),
    },
  )

  if (!response.ok) {
    throw new Error(await responseError(response, 'No se pudo enviar la pregunta.'))
  }

  const result: unknown = await response.json()
  if (!isAcceptedResponse(result)) {
    throw new Error('El tutor devolvió una solicitud con formato inválido.')
  }

  return result
}

export async function waitForAssistant(
  courseId: string,
  conversationId: string,
  jobId: string,
  messageId: string,
): Promise<MessageDto> {
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    const jobResponse = await authorizedFetch(
      `/api/v1/courses/${toApiCourseId(courseId)}/jobs/${jobId}`,
    )
    if (!jobResponse.ok) throw new Error(await responseError(jobResponse, 'No se pudo consultar la respuesta.'))
    const job = (await jobResponse.json()) as JobResponse
    if (job.data.state === 'failed') throw new Error('El tutor no pudo generar una respuesta.')
    if (job.data.state === 'succeeded') {
      const historyResponse = await authorizedFetch(
        `/api/v1/courses/${toApiCourseId(courseId)}/conversations/${conversationId}`,
      )
      if (!historyResponse.ok) throw new Error(await responseError(historyResponse, 'No se pudo cargar la respuesta.'))
      const history = (await historyResponse.json()) as HistoryResponse
      const assistant = history.messages.find(
        (message) => message.role === 'assistant' && message.inReplyTo === messageId,
      )
      if (!assistant) throw new Error('El tutor terminó el trabajo sin devolver una respuesta.')
      return assistant
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error('La respuesta del tutor tardó demasiado.')
}

function isAcceptedResponse(
  value: unknown,
): value is JsonApiResponse<AcceptedMessage> {
  if (!value || typeof value !== 'object' || !('data' in value)) return false

  const data = value.data
  return Boolean(
    data &&
      typeof data === 'object' &&
      'conversationId' in data && typeof data.conversationId === 'string' &&
      'messageId' in data && typeof data.messageId === 'string' &&
      'jobId' in data && typeof data.jobId === 'string',
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


