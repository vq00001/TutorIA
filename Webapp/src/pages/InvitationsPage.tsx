import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAppData } from '../contexts/AppDataContext'
import { request } from '../services/http'

interface Invitation {id:string;courseId:string;courseName:string;status:string;expiresAt:string}
export function InvitationsPage() {
  const {refreshAccount}=useAppData()
  const [items,setItems]=useState<Invitation[]>([]),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[token,setToken]=useState('')
  const load=async()=>setItems((await request<{data:Invitation[]}>('/invitations')).data)
  useEffect(()=>{void load().catch(e=>setNotice(e.message))},[])
  async function respond(id:string,accept:boolean) {setBusy(true);try{await request(`/invitations/${id}/respond`,{method:'POST',body:JSON.stringify({accept})});await refreshAccount();await load();setNotice(accept?'Invitación aceptada. El curso está en Mis cursos.':'Invitación rechazada.')}catch(e){setNotice(e instanceof Error?e.message:'No se pudo responder.')}finally{setBusy(false)}}
  async function redeem(e:FormEvent) {e.preventDefault();setBusy(true);try{await request('/invites/accept',{method:'POST',body:JSON.stringify({token})});await refreshAccount();setToken('');setNotice('Invitación por código aceptada.')}catch(e){setNotice(e instanceof Error?e.message:'Código inválido.')}finally{setBusy(false)}}
  return <div className="management-page"><h1>Invitaciones</h1>{notice&&<p role="status">{notice}</p>}
    {!items.length&&<p>No tienes invitaciones vigentes.</p>}
    {items.map(i=><section key={i.id}><h2>{i.courseName}</h2><p>Estado: {i.status} · Vence: {new Date(i.expiresAt).toLocaleString()}</p>{i.status==='pending'?<><button disabled={busy} onClick={()=>void respond(i.id,true)}>Aceptar {i.courseName}</button><button disabled={busy} onClick={()=>void respond(i.id,false)}>Rechazar {i.courseName}</button></>:i.status==='accepted'&&<Link to={`/cursos/${i.courseId}`}>Abrir curso</Link>}</section>)}
    <form onSubmit={e=>void redeem(e)}><h2>Invitación por código</h2><label>Código de invitación<input required minLength={20} maxLength={128} value={token} onChange={e=>setToken(e.target.value)}/></label><button disabled={busy}>Aceptar código</button></form>
  </div>
}
