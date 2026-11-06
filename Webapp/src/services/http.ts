export class ApiError extends Error {
  constructor(public code: string, public status: number, message: string) { super(message) }
}
let csrfToken = ''
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? 'GET'
  if (!['GET', 'HEAD'].includes(method) && !csrfToken) {
    const response = await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' })
    if (!response.ok) throw new Error('No se pudo iniciar una sesión segura.')
    csrfToken = (await response.json()).data.csrfToken
  }
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD'].includes(method)) {
    headers.set('X-CSRF-Token', csrfToken)
    if (!headers.has('Idempotency-Key')) headers.set('Idempotency-Key', crypto.randomUUID())
  }
  const response = await fetch('/api/v1' + path, { ...init, headers, credentials: 'same-origin' })
  if (response.status === 204) return undefined as T
  const result = await response.json()
  if (!response.ok) throw new ApiError(result.error?.code ?? 'HTTP_ERROR', response.status, result.error?.message ?? 'No se pudo completar la operación.')
  return result as T
}
export async function allPages<T>(path: string): Promise<{ data: T[] }> {
  const data: T[] = []
  let cursor: string | null = null
  do {
    const response: { data: T[]; meta?: { nextCursor: string | null } } = await request(path + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''))
    data.push(...response.data)
    cursor = response.meta?.nextCursor ?? null
  } while (cursor)
  return { data }
}
