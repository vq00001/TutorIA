import { useEffect, useState, type FormEvent } from 'react'
import { useAppData } from '../contexts/AppDataContext'
import { request } from '../services/http'

export function AccountPage() {
  const { profile, refreshAccount } = useAppData()
  const [name,setName]=useState(''), [nickname,setNickname]=useState(''), [zone,setZone]=useState('')
  const [email,setEmail]=useState(''), [password,setPassword]=useState(''), [nextPassword,setNextPassword]=useState('')
  const [confirmation,setConfirmation]=useState(''), [notice,setNotice]=useState(''), [busy,setBusy]=useState(false)
  useEffect(()=>{if(profile){setName(profile.displayName);setNickname(profile.nickname??'');setZone(profile.timezone??'America/Santiago');setEmail(profile.email??'')}},[profile])
  async function act(event:FormEvent, operation:()=>Promise<unknown>, success:string) {
    event.preventDefault();setBusy(true);setNotice('')
    try { await operation();setNotice(success) } catch(e){setNotice(e instanceof Error?e.message:'No se pudo guardar.')} finally{setBusy(false)}
  }
  if(!profile)return <p>Cargando perfil…</p>
  return <div className="management-page"><h1>Mi cuenta</h1><p>Estado: {profile.status} · Identificador: {profile.id}</p>
    {notice&&<p role="status">{notice}</p>}
    <form onSubmit={e=>void act(e,async()=>{await request('/student/me',{method:'PATCH',headers:{'If-Match':String(profile.rowVersion)},body:JSON.stringify({displayName:name,nickname,timezone:zone})});await refreshAccount()},'Perfil actualizado.')}>
      <h2>Perfil</h2><label>Nombre visible<input required maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>Nickname<input required pattern="[a-zA-Z0-9_]{3,50}" value={nickname} onChange={e=>setNickname(e.target.value)}/></label>
      <label>Zona horaria IANA<input required value={zone} onChange={e=>setZone(e.target.value)}/></label><button disabled={busy}>Guardar perfil</button>
    </form>
    <form onSubmit={e=>void act(e,()=>request('/student/me/email',{method:'POST',body:JSON.stringify({email,currentPassword:password})}),'Revisa el nuevo correo para verificar el cambio. El correo actual se conserva hasta entonces.')}>
      <h2>Correo</h2><p>Actual: {profile.email}</p><label>Nuevo correo<input type="email" required value={email} onChange={e=>setEmail(e.target.value)}/></label>
      <label>Contraseña actual para cambiar correo<input type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label><button disabled={busy}>Solicitar cambio de correo</button>
    </form>
    <form onSubmit={e=>void act(e,async()=>{await request('/student/me/password',{method:'POST',body:JSON.stringify({currentPassword:password,newPassword:nextPassword})});window.location.assign('/')},'Contraseña cambiada. Vuelve a ingresar.')}>
      <h2>Contraseña</h2><label>Contraseña actual<input type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label>
      <label>Nueva contraseña<input type="password" autoComplete="new-password" minLength={12} maxLength={1024} required value={nextPassword} onChange={e=>setNextPassword(e.target.value)}/></label><button disabled={busy}>Cambiar contraseña y cerrar sesiones</button>
    </form>
    <form onSubmit={e=>void act(e,async()=>{await request('/student/me',{method:'DELETE',body:JSON.stringify({confirmation})});window.location.assign('/')},'Borrado solicitado.')}>
      <h2>Eliminar mi cuenta</h2><p>Se retirará el acceso y comenzará el borrado de tus datos privados. Los documentos compartidos se conservan. Debes transferir la coordinación de tus cursos antes de eliminarte.</p>
      <label>Escribe tu nickname para confirmar<input required value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label><button disabled={busy||confirmation!==profile.nickname}>Solicitar eliminación de cuenta</button>
    </form>
  </div>
}
