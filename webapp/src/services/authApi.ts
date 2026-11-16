const ACCESS_TOKEN_KEY = 'tutoria.accessToken'
const AUTH_EXPIRED_EVENT = 'tutoria:auth-expired'

interface AuthResponse {
  data: {
    accessToken: string
    tokenType: 'Bearer'
    user: {
      id: string
      email: string
      displayName: string
      role?: string
      status?: 'pending' | 'active'
    }
  }
}

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function saveAccessToken(token: string) {
  localStorage.setItem(ACCESS_TOKEN_KEY, token)
}

export function clearAccessToken() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
}

export async function login(email: string, password: string) {
  const response = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })

  if (!response.ok) {
    throw new Error(await getErrorMessage(response, 'No se pudo iniciar sesión.'))
  }

  const result = (await response.json()) as AuthResponse
  if (result.data.user.status === 'pending') {
    throw new Error('Verifica tu correo antes de entrar. Usa la opción «Ya tengo un código de verificación».')
  }
  saveAccessToken(result.data.accessToken)
  return result.data.user
}

export async function register(displayName: string, email: string, password: string): Promise<'pending' | 'active'> {
  const response = await fetch('/api/v1/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ displayName, email, password }),
  })
  if (!response.ok) throw new Error(await getErrorMessage(response, 'No se pudo crear la cuenta.'))
  const result = (await response.json()) as { data: { status: 'pending' | 'active' } }
  return result.data.status
}

export async function requestEmailVerification(email: string) {
  const response = await fetch('/api/v1/auth/email-verification/request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  if (!response.ok) throw new Error(await getErrorMessage(response, 'No se pudo enviar el código.'))
}

export async function confirmEmailVerification(token: string) {
  const response = await fetch('/api/v1/auth/email-verification/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  if (!response.ok) throw new Error(await getErrorMessage(response, 'No se pudo verificar el correo.'))
}

export async function authorizedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
) {
  const token = getAccessToken()
  if (!token) throw new Error('Inicia sesión para continuar.')

  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(input, { ...init, headers })
  if (response.status === 401) {
    clearAccessToken()
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw new Error('Tu sesión expiró. Inicia sesión de nuevo.')
  }

  return response
}

export function listenForAuthExpiration(listener: () => void) {
  window.addEventListener(AUTH_EXPIRED_EVENT, listener)
  return () => window.removeEventListener(AUTH_EXPIRED_EVENT, listener)
}

export async function logout() {
  try {
    await authorizedFetch('/api/v1/auth/logout', { method: 'POST' })
  } catch {
    // Eliminar credenciales locales aunque el servidor no este disponible.
  }
  clearAccessToken()
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}

async function getErrorMessage(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as {
      message?: string
      detail?: string
    }
    return body.message ?? body.detail ?? `${fallback} (${response.status})`
  } catch {
    return `${fallback} (${response.status})`
  }
}
