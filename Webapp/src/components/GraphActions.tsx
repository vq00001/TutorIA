import { useEffect, useState } from 'react'
import { request } from '../services/http'

interface Draft {id:string;label:string;description:string;row_version:number}
interface Route {stale:boolean;status:string;steps:Array<{concept_id:string;concept_label?:string;position:number;state:string;reason:string;estimated_minutes:number}>}
export function GraphActions({courseId,coordinator}:{courseId:string;coordinator:boolean}) {
  const [drafts,setDrafts]=useState<Draft[]>([])
  const [route,setRoute]=useState<Route|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const refresh=()=>{if(coordinator)void request<{data:Draft[]}>(`/courses/${courseId}/concepts/drafts`).then(r=>setDrafts(r.data)).catch(e=>setError(e.message))}
  useEffect(refresh,[courseId,coordinator])
  useEffect(()=>{
    let active=true
    const load=()=>request<{data:Route|null}>(`/courses/${courseId}/roadmaps/current`).then(r=>{if(active)setRoute(r.data)}).catch(()=>{})
    setRoute(null)
    void load()
    const timer=setInterval(()=>{if(document.visibilityState==='visible')void load()},5000)
    return()=>{active=false;clearInterval(timer)}
  },[courseId])
  const approve=async(d:Draft)=>{
    setBusy(true)
    try{await request(`/courses/${courseId}/concepts/${d.id}/approve`,{method:'POST',headers:{'If-Match':String(d.row_version)}});refresh()}
    catch(e){setError(e instanceof Error?e.message:'No se aprobó el concepto.')}
    finally{setBusy(false)}
  }
  const build=async()=>{
    setBusy(true);setError('')
    try{
      await request(`/courses/${courseId}/roadmaps`,{method:'POST',body:'{}'})
      setRoute((await request<{data:Route}>(`/courses/${courseId}/roadmaps/current`)).data)
    }catch(e){setError(e instanceof Error?e.message:'No se pudo crear la ruta.')}
    finally{setBusy(false)}
  }
  return <section><button className="primary-button" disabled={busy} onClick={()=>void build()}>Crear mi ruta de estudio</button>
    {error&&<p role="alert">{error}</p>}
    {route&&<div><h2>Mi ruta {route.stale?'· pendiente de actualizar':''}</h2>{route.status==='blocked'&&<p>La ruta tiene pasos bloqueados que requieren revisión o material publicado.</p>}<ol>{route.steps.map(s=><li key={s.concept_id}>Paso {s.position}: {s.concept_label ?? 'Concepto'} · {({pending:'pendiente',completed:'completado',blocked:'bloqueado',in_progress:'en curso'} as Record<string,string>)[s.state] ?? s.state} · {s.estimated_minutes} minutos {s.reason==='NO_SOURCE'?'· sin material publicado':s.reason==='RETIRED_CONCEPT'?'· concepto retirado':''}</li>)}</ol></div>}
    {coordinator&&<div><h2>Conceptos propuestos</h2><p>La extracción propone borradores; tu aprobación los incorpora al mapa.</p><button className="secondary-button" onClick={refresh}>Actualizar propuestas</button>{drafts.map(d=><article key={d.id}><h3>{d.label}</h3><p>{d.description}</p><button className="secondary-button" disabled={busy} onClick={()=>void approve(d)}>Aprobar concepto</button></article>)}</div>}
  </section>
}
