import type { MessageDto } from '../types'
import { authorizedFetch } from './authApi'
import { responseError } from './utils'

interface MessagePage {
  messages: MessageDto[]
  nextAfterSequence: number | null
}

/**
 * Lee el historial en orden, una página cada vez.
 * El consumidor puede detener el recorrido al encontrar la respuesta buscada.
 */
export async function* iterateConversationMessagePages(
  courseId: string,
  conversationId: string,
  errorMessage = 'No se pudo obtener el historial del curso.',
): AsyncGenerator<MessageDto[]> {
  const path = `/api/v1/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(conversationId)}`
  let afterSequence: number | null = 0
  do {
    const query = new URLSearchParams({ afterSequence: String(afterSequence) })
    const response = await authorizedFetch(`${path}?${query}`)
    if (!response.ok) {
      throw new Error(await responseError(response, errorMessage))
    }
    const page = (await response.json()) as MessagePage
    yield page.messages
    afterSequence = page.nextAfterSequence ?? null
  } while (afterSequence !== null)
}
