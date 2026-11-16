import { useState, type FormEvent } from 'react'
import { confirmEmailVerification, login, register, requestEmailVerification } from '../services/authApi'

type AccessMode = 'login' | 'register' | 'verify'

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<AccessMode>('login')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [passwordAgain, setPasswordAgain] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function changeMode(next: AccessMode) {
    setMode(next)
    setError(null)
    setNotice(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setNotice(null)
    setSubmitting(true)

    try {
      if (mode === 'login') {
        await login(email.trim(), password)
        onLogin()
      } else if (mode === 'register') {
        if (password !== passwordAgain) {
          setError('Las contraseñas no coinciden.')
          return
        }
        const status = await register(displayName.trim(), email.trim(), password)
        if (status === 'active') {
          await login(email.trim(), password)
          onLogin()
          return
        }
        setMode('verify')
        setPassword('')
        setPasswordAgain('')
        try {
          await requestEmailVerification(email.trim())
          setNotice('Cuenta creada. Enviamos un código de verificación a tu correo.')
        } catch (cause) {
          setError(cause instanceof Error ? `Cuenta creada, pero ${cause.message}` : 'Cuenta creada, pero no se pudo enviar el código.')
        }
      } else {
        await confirmEmailVerification(token.trim())
        setToken('')
        setMode('login')
        setNotice('Correo verificado. Ya puedes iniciar sesión.')
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo iniciar sesión.')
    } finally {
      setSubmitting(false)
    }
  }

  async function resendCode() {
    setError(null)
    setNotice(null)
    setSubmitting(true)
    try {
      await requestEmailVerification(email.trim())
      setNotice('Si la cuenta está pendiente, enviamos un nuevo código a tu correo.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo enviar el código.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">t<span>·</span> tutor<span>ia</span></div>
        <h1>{mode === 'register' ? 'Crear cuenta' : mode === 'verify' ? 'Verificar correo' : 'Inicia sesión'}</h1>
        <p>{mode === 'register' ? 'Regístrate para comenzar a aprender.' : mode === 'verify' ? 'Introduce el código que recibiste por correo.' : 'Accede a tu tutor de aprendizaje.'}</p>

        {mode === 'register' && <>
          <label htmlFor="register-name">Nombre</label>
          <input id="register-name" type="text" autoComplete="name" maxLength={100} value={displayName} onChange={(event) => setDisplayName(event.target.value)} required />
        </>}

        <label htmlFor="login-email">Correo electrónico{mode === 'verify' ? ' (para reenviar el código)' : ''}</label>
        <input
          id="login-email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required={mode !== 'verify'}
        />

        {mode === 'verify' ? <>
          <label htmlFor="verification-token">Código de verificación</label>
          <input id="verification-token" type="text" autoComplete="one-time-code" minLength={43} maxLength={43} value={token} onChange={(event) => setToken(event.target.value.trim())} required />
        </> : <>
          <label htmlFor="login-password">Contraseña</label>
          <input id="login-password" type="password" autoComplete={mode === 'register' ? 'new-password' : 'current-password'} minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required />
          {mode === 'register' && <>
            <label htmlFor="register-password-again">Repite la contraseña</label>
            <input id="register-password-again" type="password" autoComplete="new-password" minLength={8} maxLength={128} value={passwordAgain} onChange={(event) => setPasswordAgain(event.target.value)} required />
          </>}
        </>}

        {notice && <div className="login-notice" role="status">{notice}</div>}
        {error && <div className="login-error" role="alert">{error}</div>}

        <button className="login-submit" type="submit" disabled={submitting}>
          {submitting ? 'Procesando…' : mode === 'register' ? 'Crear cuenta' : mode === 'verify' ? 'Verificar correo' : 'Entrar'}
        </button>
        {mode === 'verify' && <button className="login-link" type="button" disabled={submitting || !email.trim()} onClick={() => { void resendCode() }}>Reenviar código</button>}
        <button className="login-link" type="button" onClick={() => changeMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? '¿No tienes cuenta? Regístrate' : 'Volver a iniciar sesión'}
        </button>
        {mode === 'login' && <button className="login-link" type="button" onClick={() => changeMode('verify')}>Ya tengo un código de verificación</button>}
      </form>
    </main>
  )
}
