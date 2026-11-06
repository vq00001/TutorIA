import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { request } from '../services/http'
import { ManualReview } from '../components/exams/ManualReview'

interface Question {
  id:string; kind:string; prompt:string; topic:string; max_points:number; options:Array<{id:string;text:string}>
  answer:{id:string;text_answer:string|null;optionIds:string[];flagged:boolean;row_version:number}|null
  grade:{points:number|null;status:string;feedback:string;grader_version?:string}|null
  review?:{expected_answer:string|null;explanation:string;option_ids:string[]}
}
interface Attempt { id:string;status:string;deadline_at:string;serverNow:string;score_percent:number|null;questions:Question[] }

export function TakeExamPage() {
  const {courseId='',examId=''}=useParams()
  const [search]=useSearchParams()
  const requestedAttempt=search.get('attemptId')
  const [attempt,setAttempt]=useState<Attempt|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [remaining,setRemaining]=useState(0)
  useEffect(()=>{
    let active=true
    const load=requestedAttempt?request<{data:Attempt}>(`/attempts/${requestedAttempt}`):request<{data:Attempt}>(`/courses/${courseId}/exams/${examId}/attempts`,{method:'POST',body:'{}'})
    void load
      .then(r=>{if(active)setAttempt(r.data)}).catch(e=>{if(active)setError(e.message)})
    return ()=>{active=false}
  },[courseId,examId,requestedAttempt])
  useEffect(()=>{
    if(!attempt)return
    const deadline=Date.now()+new Date(attempt.deadline_at).getTime()-new Date(attempt.serverNow).getTime()
    setRemaining(Math.max(0,Math.ceil((deadline-Date.now())/1000)))
    const timer=setInterval(()=>setRemaining(Math.max(0,Math.ceil((deadline-Date.now())/1000))),1000)
    return ()=>clearInterval(timer)
  },[attempt?.id,attempt?.serverNow])
  useEffect(()=>{
    if(!attempt||!['in_progress','submitted','grading'].includes(attempt.status))return
    const timer=setInterval(()=>{void request<{data:Attempt}>(`/attempts/${attempt.id}`).then(r=>setAttempt(r.data)).catch(e=>setError(e.message))},3000)
    return ()=>clearInterval(timer)
  },[attempt?.id,attempt?.status])
  const save=async(q:Question,textAnswer:string|null,optionIds:string[])=>{
    if(!attempt)return
    setBusy(true);setError('')
    try {
      await request(`/attempts/${attempt.id}/answers/${q.id}`,{method:'PUT',headers:{'If-Match':String(q.answer?.row_version??0)},body:JSON.stringify({textAnswer,optionIds,flagged:q.answer?.flagged??false})})
      setAttempt((await request<{data:Attempt}>(`/attempts/${attempt.id}`)).data)
    }catch(e){setError(e instanceof Error?e.message:'No se guardó la respuesta.')}
    finally{setBusy(false)}
  }
  const submit=async()=>{
    if(!attempt)return
    setBusy(true)
    try{await request(`/attempts/${attempt.id}/submit`,{method:'POST',body:'{}'});setAttempt((await request<{data:Attempt}>(`/attempts/${attempt.id}`)).data)}
    catch(e){setError(e instanceof Error?e.message:'No se entregó la práctica.')}
    finally{setBusy(false)}
  }
  if(!attempt)return <div className="page-loading">{error||'Cargando intento…'}</div>
  if(attempt.status==='invalidated')return <section className="take-exam-page"><Link to={`/cursos/${courseId}/examenes`}>← Volver a prácticas</Link><h1>Evaluación retirada</h1><p>Una fuente fue retirada del curso. Esta práctica ya no está disponible y sus resultados no aportan evidencia de dominio.</p></section>
  const closed=attempt.status!=='in_progress'||remaining<=0
  return <section className="take-exam-page"><Link to={`/cursos/${courseId}/examenes`}>← Volver a prácticas</Link><h1>Práctica personal</h1>
    <p>Estado: {attempt.status} · Tiempo restante: {Math.floor(remaining/60)}:{String(remaining%60).padStart(2,'0')}</p>
    {attempt.score_percent!==null&&<p>Puntaje: {Number(attempt.score_percent).toFixed(2)}%</p>}
    {attempt.status==='needs_review'&&<p>Hay respuestas pendientes de revisión. Aún no hay puntaje final.</p>}
    {error&&<p role="alert">{error}</p>}
    {attempt.questions.map((q,i)=><article className="exam-question" key={q.id}><span>{i+1}. {q.topic}</span><h2>{q.prompt}</h2>
      {q.kind==='development'?<textarea key={`${q.id}:${q.answer?.row_version??0}`} defaultValue={q.answer?.text_answer??''} disabled={closed||busy} rows={6} onBlur={e=>{if(!closed&&e.target.value!==(q.answer?.text_answer??''))void save(q,e.target.value,[])}}/>:
      <div className="answer-options">{q.options.map(o=><button key={o.id} disabled={closed||busy} className={`answer-option ${q.answer?.optionIds.includes(o.id)?'selected':''}`} onClick={()=>{
        const old=q.answer?.optionIds??[]
        void save(q,null,q.kind==='multiple'?(old.includes(o.id)?old.filter(id=>id!==o.id):[...old,o.id]):[o.id])
      }}>{o.text}</button>)}</div>}
      {q.grade&&<p>{q.grade.feedback} {q.grade.points!==null&&`(${q.grade.points} puntos)`}</p>}
      {q.grade?.grader_version==='manual_self'&&<p>Autoevaluación personal: este puntaje no aporta evidencia de dominio.</p>}
      {attempt.status==='needs_review'&&q.grade?.status==='needs_review'&&q.answer&&<ManualReview attemptId={attempt.id} answerId={q.answer.id} maximum={Number(q.max_points)} onReviewed={async()=>{setAttempt((await request<{data:Attempt}>(`/attempts/${attempt.id}`)).data)}}/>}
      {q.review&&<p>{q.review.explanation}</p>}
    </article>)}
    {!closed&&<button className="primary-button" disabled={busy} onClick={()=>void submit()}>Entregar práctica</button>}
  </section>
}
