import { useEffect, useState } from 'react'
import { request } from '../services/http'

interface Draft {id:string;label:string;description:string;row_version:number}
interface Route {stale:boolean;status:string;steps:Array<{concept_id:string;position:number;state:string;reason:string;estimated_minutes:number}>}
export function GraphActions({courseId,coordinator}:{courseId:string;coordinator:boolean}) {
  const [drafts,setDrafts]=useState<Draft[]>([])
  const [route,setRoute]=useState<Route|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const refresh=()=>{if(coordinator)void request<{data:Draft[]}>(`/courses/${courseId}/concepts/drafts`).then(r=>setDrafts(r.data)).catch(e=>setError(e.message))}
  useEffect(refresh,[courseId,coordinator])
  const approve=async(d:Draft)=>{
    setBusy(true)
    try{await request(`/courses/${courseId}/concepts/${d.id}/approve`,{method:'POST',headers:{'If-Match':String(d.row_version)}});refresh()}
    catch(e){setError(e instanceof Error?e.message:'No se aprobó el concepto.')}
    finally{setBusy(false)}
  }
  const build=async()=>{
    setBusy(true);setError('')
    try{
      const graph=await request<{data:{nodes:Array<{key:string}>}}>(`/courses/${courseId}/knowledge-graph`)
      const goal=await request<{data:{id:string}}>(`/courses/${courseId}/goals`,{method:'POST',body:JSON.stringify({title:'Dominar el curso',conceptIds:graph.data.nodes.map(n=>n.key)})})
      await request(`/goals/${goal.data.id}/roadmaps`,{method:'POST',body:'{}'})
      setRoute((await request<{data:Route}>(`/goals/${goal.data.id}/roadmaps/current`)).data)
    }catch(e){setError(e instanceof Error?e.message:'No se pudo crear la ruta.')}
    finally{setBusy(false)}
  }
  return <section><button className="primary-button" disabled={busy} onClick={()=>void build()}>Crear mi ruta de estudio</button>
    {error&&<p role="alert">{error}</p>}
    {route&&<div><h2>Mi ruta {route.stale?'· pendiente de actualizar':''}</h2><ol>{route.steps.map(s=><li key={s.concept_id}>Paso {s.position}: {s.state} · {s.estimated_minutes} minutos {s.reason==='NO_SOURCE'?'· sin material publicado':''}</li>)}</ol></div>}
    {coordinator&&<div><h2>Conceptos propuestos</h2><p>La extracción propone borradores; tu aprobación los incorpora al mapa.</p><button className="secondary-button" onClick={refresh}>Actualizar propuestas</button>{drafts.map(d=><article key={d.id}><h3>{d.label}</h3><p>{d.description}</p><button className="secondary-button" disabled={busy} onClick={()=>void approve(d)}>Aprobar concepto</button></article>)}</div>}
  </section>
}
