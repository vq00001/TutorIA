import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { request } from '../services/http'

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState('loading')
  const [register, setRegister] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [resetToken, setResetToken] = useState('')
  const [forgot, setForgot] = useState(false)
  const [pendingEmail, setPendingEmail] = useState('')
  const refresh = () => request<{data:{status:string}}>('/student/me').then(r => setState(r.data.status)).catch(() => setState('anonymous'))
  useEffect(() => {
    const url = new URL(location.href)
    const token = url.searchParams.get('token')
    if(token && url.pathname === '/cuenta/reset_password') {
      history.replaceState({}, '', '/')
      setResetToken(token); setState('anonymous')
    } else if (token && url.pathname === '/cuenta/verify_email') {
      history.replaceState({}, '', '/')
      void request('/auth/verify-email', { method: 'POST', body: JSON.stringify({ token }) })
        .then(() => { setError('Correo verificado. Ya puedes iniciar sesión.'); return refresh() })
        .catch(e => { setError(e.message); setState('anonymous') })
    } else void refresh()
  }, [])
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError('')
    const data = new FormData(event.currentTarget)
    const body = { email: data.get('email'), password: data.get('password') }
    try {
      if(resetToken) {
        await request('/auth/reset-password',{method:'POST',body:JSON.stringify({token:resetToken,password:data.get('password')})})
        setResetToken('');setError('Contraseña cambiada. Inicia sesión con la nueva contraseña.')
      } else if(forgot) {
        await request('/auth/forgot-password',{method:'POST',body:JSON.stringify({email:data.get('email')})})
        setForgot(false);setError('Si la cuenta existe, recibirás un enlace de recuperación.')
      } else if (register) {
        await request('/auth/register', { method: 'POST', body: JSON.stringify({ ...body, nickname: data.get('nickname'), displayName: data.get('displayName'), acceptTerms: data.get('terms') === 'on', acknowledgeAI: data.get('ai') === 'on' }) })
        setRegister(false); setError('Revisa tu correo para verificar la cuenta.')
      } else {
        await request('/auth/login', { method: 'POST', body: JSON.stringify(body) }); setPendingEmail(String(data.get('email') ?? '')); await refresh()
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'No se pudo acceder.') }
    finally { setBusy(false) }
  }
  if (state === 'active' && !resetToken) return <><button style={{position:'fixed',bottom:12,left:12,zIndex:100}} className="secondary-button" onClick={()=>{void request('/auth/logout',{method:'POST'}).then(()=>location.reload()).catch(e=>setError(e.message))}}>Cerrar sesión</button>{children}</>
  if (state === 'loading') return <div className="page-loading">Comprobando sesión…</div>
  return <main style={{ maxWidth: 440, margin: '8vh auto', padding: 24 }}><h1>TutorIA</h1>
    {state === 'pending' && !resetToken ? <div><p>Tu cuenta está pendiente de verificación. Abre el enlace que enviamos a tu correo y recarga esta página.</p><form onSubmit={event=>{event.preventDefault();setBusy(true);void request('/auth/resend-verification',{method:'POST',body:JSON.stringify({email:pendingEmail})}).then(()=>setError('Si la cuenta está pendiente, enviaremos un nuevo enlace.')).catch(e=>setError(e.message)).finally(()=>setBusy(false))}}><label className="exam-field">Correo de la cuenta pendiente<input type="email" required value={pendingEmail} onChange={e=>setPendingEmail(e.target.value)}/></label><button className="secondary-button" disabled={busy}>Reenviar verificación</button></form><button className="secondary-button" onClick={()=>{void request('/auth/logout',{method:'POST'}).then(()=>setState('anonymous')).catch(e=>setError(e.message))}}>Usar otra cuenta</button></div> : <form onSubmit={submit}>
      <h2>{resetToken?'Nueva contraseña':forgot?'Recuperar acceso':register ? 'Crear cuenta' : 'Iniciar sesión'}</h2>
      {register && <><label className="exam-field">Alias<input name="displayName" required maxLength={100} /></label><label className="exam-field">Nickname<input name="nickname" required pattern="[a-zA-Z0-9_]{3,50}" /></label></>}
      {!resetToken&&<label className="exam-field">Correo<input name="email" type="email" autoComplete="email" required /></label>}
      {!forgot&&<label className="exam-field">Contraseña<input name="password" type="password" autoComplete={register||resetToken ? 'new-password' : 'current-password'} minLength={register||resetToken ? 12 : 1} required /></label>}
      {register && <><label><input type="checkbox" name="terms" required /> Acepto las condiciones del servicio y la conservación de materiales compartidos.</label><p>Los chats y prácticas son privados. Los originales compartidos se conservan al salir del curso; puedes solicitar su retiro. Se usa IA para procesar materiales y generar respuestas, que pueden contener errores.</p><label><input type="checkbox" name="ai" required /> He leído el aviso de uso de IA.</label></>}
      <p><button className="primary-button" disabled={busy}>{busy ? 'Procesando…' : resetToken?'Cambiar contraseña':forgot?'Enviar enlace':register ? 'Registrarme' : 'Entrar'}</button></p>
      <button type="button" className="secondary-button" onClick={() => setRegister(!register)}>{register ? 'Ya tengo cuenta' : 'Crear una cuenta'}</button>
      {!register&&!resetToken&&<button type="button" className="secondary-button" onClick={()=>setForgot(!forgot)}>{forgot?'Volver al acceso':'Olvidé mi contraseña'}</button>}
    </form>}
    {error && <p role="alert">{error}</p>}
  </main>
}
