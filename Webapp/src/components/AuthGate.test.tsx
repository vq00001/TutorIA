import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthGate } from './AuthGate'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('../services/http', () => ({ request }))

describe('account access boundary', () => {
  beforeEach(() => {
    request.mockReset()
    history.replaceState({}, '', '/')
  })

  it('shows protected content only after an active profile response', async () => {
    request.mockResolvedValue({ data: { status: 'active' } })
    render(<AuthGate><p>Private course</p></AuthGate>)
    expect(await screen.findByText('Private course')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Cerrar sesión' })).toBeVisible()
  })

  it('withholds course content for a pending account', async () => {
    request.mockResolvedValue({ data: { status: 'pending' } })
    render(<AuthGate><p>Private course</p></AuthGate>)
    expect(await screen.findByText(/cuenta está pendiente de verificación/)).toBeVisible()
    expect(screen.queryByText('Private course')).not.toBeInTheDocument()
  })

  it('signs in and reloads the authenticated profile', async () => {
    let loggedIn = false
    request.mockImplementation(async (path: string) => {
      if (path === '/student/me') {
        if (!loggedIn) throw new Error('AUTHENTICATION_REQUIRED')
        return { data: { status: 'active' } }
      }
      if (path === '/auth/login') { loggedIn = true; return { data: {} } }
      throw new Error('Unexpected request')
    })
    render(<AuthGate><p>Private course</p></AuthGate>)
    fireEvent.change(await screen.findByLabelText('Correo'), { target: { value: 'synthetic@example.com' } })
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'a synthetic passphrase' } })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('Private course')).toBeVisible()
    const mutation = request.mock.calls.find(c => c[0] === '/auth/login')
    expect(JSON.parse(mutation?.[1].body)).toEqual({ email: 'synthetic@example.com', password: 'a synthetic passphrase' })
  })

  it('keeps rejected credentials outside protected content and presents the failure', async () => {
    request.mockRejectedValue(new Error('Credenciales no válidas'))
    render(<AuthGate><p>Private course</p></AuthGate>)
    fireEvent.change(await screen.findByLabelText('Correo'), { target: { value: 'synthetic@example.com' } })
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'wrong passphrase' } })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Credenciales no válidas')
    expect(screen.queryByText('Private course')).not.toBeInTheDocument()
  })

  it('sends explicit policy acknowledgements when registering', async () => {
    request.mockImplementation(async (path: string) => {
      if (path === '/student/me') throw new Error('Unauthorized')
      return { data: {} }
    })
    render(<AuthGate><p>Private course</p></AuthGate>)
    fireEvent.click(await screen.findByRole('button', { name: 'Crear una cuenta' }))
    fireEvent.change(screen.getByLabelText('Alias'), { target: { value: 'Synthetic' } })
    fireEvent.change(screen.getByLabelText('Nickname'), { target: { value: 'synthetic_user' } })
    fireEvent.change(screen.getByLabelText('Correo'), { target: { value: 'synthetic@example.com' } })
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'synthetic passphrase' } })
    for (const checkbox of screen.getAllByRole('checkbox')) fireEvent.click(checkbox)
    fireEvent.click(screen.getByRole('button', { name: 'Registrarme' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Revisa tu correo')
    const registration = request.mock.calls.find(c => c[0] === '/auth/register')
    expect(JSON.parse(registration?.[1].body)).toMatchObject({ acceptTerms: true, acknowledgeAI: true, nickname: 'synthetic_user' })
  })

  it('consumes an email-verification link without retaining its token in the URL', async () => {
    history.replaceState({}, '', '/cuenta/verify_email?token=synthetic-one-use-token')
    request.mockImplementation(async (path: string) => {
      if (path === '/student/me') throw new Error('Unauthorized')
      return { data: {} }
    })
    render(<AuthGate><p>Private course</p></AuthGate>)
    expect(await screen.findByRole('alert')).toHaveTextContent('Correo verificado')
    expect(location.search).toBe('')
    expect(request).toHaveBeenCalledWith('/auth/verify-email', expect.objectContaining({ method: 'POST' }))
  })

  it('shows an invalid verification link as a failure', async () => {
    history.replaceState({}, '', '/cuenta/verify_email?token=expired-synthetic-token')
    request.mockRejectedValue(new Error('El enlace venció'))
    render(<AuthGate><p>Private course</p></AuthGate>)
    expect(await screen.findByRole('alert')).toHaveTextContent('El enlace venció')
    expect(screen.queryByText('Private course')).not.toBeInTheDocument()
  })

  it('requests password recovery without claiming whether the account exists', async () => {
    request.mockImplementation(async (path: string) => {
      if (path === '/student/me') throw new Error('Unauthorized')
      return { data: {} }
    })
    render(<AuthGate><p>Private course</p></AuthGate>)
    fireEvent.click(await screen.findByRole('button', { name: 'Olvidé mi contraseña' }))
    fireEvent.change(screen.getByLabelText('Correo'), { target: { value: 'synthetic@example.com' } })
    expect(screen.queryByLabelText('Contraseña')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Enviar enlace' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Si la cuenta existe')
    expect(request).toHaveBeenCalledWith('/auth/forgot-password', expect.anything())
  })

  it('uses the recovery token once and clears it from browser history', async () => {
    history.replaceState({}, '', '/cuenta/reset_password?token=synthetic-reset-token')
    request.mockResolvedValue({ data: {} })
    render(<AuthGate><p>Private course</p></AuthGate>)
    const password = await screen.findByLabelText('Contraseña')
    expect(location.search).toBe('')
    expect(screen.queryByLabelText('Correo')).not.toBeInTheDocument()
    fireEvent.change(password, { target: { value: 'new synthetic passphrase' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cambiar contraseña' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/auth/reset-password', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByRole('alert')).toHaveTextContent('Contraseña cambiada')
  })
})
