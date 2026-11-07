import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAppData } from '../contexts/AppDataContext'
import { request } from '../services/http'

interface Member {id:string;displayName:string;nickname:string;role:string;status:string;rowVersion:number}
export function CourseSettingsPage() {
  const {courseId=''}=useParams(), navigate=useNavigate()
  const {courses,profile,refreshAccount}=useAppData()
  const course=courses.find(c=>c.id===courseId)
  const [members,setMembers]=useState<Member[]>([]),[name,setName]=useState(''),[description,setDescription]=useState(''),[objectives,setObjectives]=useState('')
  const [email,setEmail]=useState(''),[confirmation,setConfirmation]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false)
  const load=async()=>setMembers((await request<{data:Member[]}>(`/courses/${courseId}/members`)).data)
  useEffect(()=>{let live=true;void request<{data:Member[]}>(`/courses/${courseId}/members`).then(r=>{if(live)setMembers(r.data)}).catch(e=>{if(live)setNotice(e.message)});return()=>{live=false}},[courseId])
  useEffect(()=>{if(course){setName(course.name);setDescription(course.description??'');setObjectives(course.objectives??'')}},[course])
  async function act(operation:()=>Promise<unknown>, success:string) {setBusy(true);setNotice('');try{await operation();setNotice(success)}catch(e){setNotice(e instanceof Error?e.message:'No se pudo completar.')}finally{setBusy(false)}}
  const submit=(e:FormEvent,operation:()=>Promise<unknown>,success:string)=>{e.preventDefault();void act(operation,success)}
  if(!course)return <p>Curso no disponible. <Link to="/">Mis cursos</Link></p>
  const coordinator=course.myRole==='coordinator'
  return <div className="management-page"><Link to={`/cursos/${courseId}`}>← Volver al tutor</Link><h1>Información de {course.name}</h1><p>{course.description}</p><p>Objetivos: {course.objectives}</p>
    {notice&&<p role="status">{notice}</p>}
    <section><h2>Miembros</h2><ul>{members.map(m=><li key={m.id}><strong>{m.displayName}</strong> (@{m.nickname}) · {m.role}
      {coordinator&&<button disabled={busy} onClick={()=>void act(async()=>{await request(`/courses/${courseId}/members/${m.id}`,{method:'PATCH',headers:{'If-Match':String(m.rowVersion)},body:JSON.stringify({role:m.role==='student'?'coordinator':'student',status:'active'})});await refreshAccount();await load()},'Rol actualizado.')}>{m.role==='student'?'Hacer coordinador':'Hacer estudiante'}{m.id===profile?.id?' (yo)':''}</button>}
    </li>)}</ul></section>
    {coordinator&&<><form onSubmit={e=>submit(e,async()=>{await request(`/courses/${courseId}`,{method:'PATCH',headers:{'If-Match':String(course.rowVersion)},body:JSON.stringify({name,description,objectives})});await refreshAccount()},'Curso actualizado.')}>
      <h2>Configurar curso</h2><label>Nombre<input required maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label><label>Descripción<textarea value={description} maxLength={10000} onChange={e=>setDescription(e.target.value)}/></label><label>Objetivos<textarea value={objectives} maxLength={10000} onChange={e=>setObjectives(e.target.value)}/></label><button disabled={busy}>Guardar curso</button>
    </form><form onSubmit={e=>submit(e,async()=>{await request(`/courses/${courseId}/invitations`,{method:'POST',body:JSON.stringify({email})});setEmail('')},'Si existe una cuenta activa con ese correo y aún no pertenece al curso, recibirá la invitación en su bandeja.')}>
      <h2>Invitar a una cuenta registrada</h2><label>Correo de la persona<input type="email" required value={email} onChange={e=>setEmail(e.target.value)}/></label><button disabled={busy}>Enviar invitación</button>
    </form></>}
    <section><h2>Salir del curso</h2><p>Perderás acceso al curso. Los documentos compartidos se conservan. El último coordinador debe transferir su rol antes de salir.</p><button disabled={busy} onClick={()=>{if(window.confirm('¿Salir de este curso?'))void act(async()=>{await request(`/courses/${courseId}/leave`,{method:'POST',body:'{}'});await refreshAccount();navigate('/')},'Has salido del curso.')}}>Salir del curso</button></section>
    {coordinator&&<form onSubmit={e=>submit(e,async()=>{await request(`/courses/${courseId}`,{method:'DELETE',body:JSON.stringify({confirmation})});await refreshAccount();navigate('/')},'Borrado solicitado.')}><h2>Eliminar curso</h2><p>Retira el acceso de todos los miembros e inicia el borrado del curso y sus contenidos.</p><label>Escribe el nombre del curso para confirmar<input required value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label><button disabled={busy||confirmation!==course.name}>Solicitar eliminación del curso</button></form>}
  </div>
}
