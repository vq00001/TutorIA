import { useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAppData } from '../contexts/AppDataContext'
import { MarkdownContent } from '../components/chat/MarkdownContent'
import { allPages, request } from '../services/http'
import type { SourceDocument } from '../types'

interface Artifact {id:string;title:string;content:{markdown:string};createdAt:string;usedDocuments:SourceDocument[]}
export function ArtifactsPage() {
  const {courseId=''}=useParams(),{courses}=useAppData()
  const [items,setItems]=useState<Artifact[]>([]),[kind,setKind]=useState('summary'),[topic,setTopic]=useState(''),[error,setError]=useState(''),[generation,setGeneration]=useState(''),[busy,setBusy]=useState(false)
  useEffect(()=>{let live=true;setItems([]);setGeneration('');void allPages<Artifact>(`/courses/${courseId}/artifacts`).then(r=>{if(live)setItems(r.data)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[courseId])
  useEffect(()=>{if(!generation)return;let live=true;const check=async()=>{try{const r=await request<{data:{status:string;error_code?:string}}>(`/generations/${generation}`);if(!live)return;if(r.data.status==='completed'){const list=await allPages<Artifact>(`/courses/${courseId}/artifacts`);if(live){setItems(list.data);setGeneration('')}}else if(['failed','cancelled','invalidated'].includes(r.data.status)){setError('No se pudo generar el material. Revisa las fuentes e inténtalo nuevamente.');setGeneration('')}}catch(e){if(live){setError(e instanceof Error?e.message:'No se pudo consultar.');setGeneration('')}}};void check();const timer=setInterval(()=>void check(),2000);return()=>{live=false;clearInterval(timer)}},[generation,courseId])
  async function create(e:FormEvent) {e.preventDefault();setBusy(true);setError('');try{setGeneration((await request<{data:{generationId:string}}>(`/courses/${courseId}/artifacts`,{method:'POST',body:JSON.stringify({kind,topic})})).data.generationId)}catch(e){setError(e instanceof Error?e.message:'No se pudo generar.')}finally{setBusy(false)}}
  return <div className="management-page"><Link to={`/cursos/${courseId}`}>← Volver al tutor</Link><h1>Materiales generados · {courses.find(c=>c.id===courseId)?.name}</h1><p>Biblioteca personal del curso. Los originales compartidos están en Materiales. El contenido generado por IA puede contener errores.</p>{error&&<p role="alert">{error}</p>}
    <form onSubmit={e=>void create(e)}><label>Tipo<select value={kind} onChange={e=>setKind(e.target.value)}><option value="summary">Resumen</option><option value="explanation">Explicación</option><option value="flashcards">Tarjetas de estudio</option></select></label><label>Tema<input required maxLength={200} value={topic} onChange={e=>setTopic(e.target.value)}/></label><button disabled={busy||!!generation}>{generation?'Generando material…':'Generar material'}</button></form>
    {!items.length&&!generation&&<p>Aún no has generado materiales en este curso.</p>}{items.map(i=><section key={i.id}><h2>{i.title}</h2><time>{new Date(i.createdAt).toLocaleString()}</time><MarkdownContent content={i.content.markdown}/><p>{i.usedDocuments.length?'Documentos utilizados:':'Sin documentos utilizados; revisa el aviso del tutor.'}</p>{i.usedDocuments.map(d=><p key={d.id}><a href={d.contentUrl??undefined}>{d.name}</a></p>)}</section>)}
  </div>
}
