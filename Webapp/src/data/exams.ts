import { request } from '../services/http'
import type { ExamSummary } from '../types/exams'

interface ExamRow { id: string; course_id: string; title: string; kind: string; status: string; created_at: string; duration_seconds: number; question_count: number; latest_attempt_id?:string|null; latest_attempt_status?:string|null; latest_score?:number|null }
function summary(e: ExamRow): ExamSummary {
  const closed = e.latest_attempt_status && ['graded','needs_review','invalidated'].includes(e.latest_attempt_status)
  return { id: e.id, courseId: e.course_id, title: e.title, type: e.kind, topics: [], scheduledAt: e.created_at, durationMinutes: e.duration_seconds / 60, questionCount: e.question_count, status: closed || e.status === 'invalidated' ? 'completed' : e.status === 'ready' ? 'available' : 'upcoming', attemptId:e.latest_attempt_id??undefined, attemptStatus:e.latest_attempt_status??undefined, score:e.latest_score===null||e.latest_score===undefined?undefined:Number(e.latest_score) }
}
export async function loadCourseExams(id: string) { return (await request<{data:ExamRow[]}>(`/courses/${id}/exams`)).data.map(summary) }
export async function loadCourseTopics(id: string) {
  const graph = await request<{data:{nodes:Array<{key:string;attributes:{label:string}}>}}>(`/courses/${id}/knowledge-graph`)
  return graph.data.nodes.map(n => n.attributes.label)
}
export async function generatePersonalExam(id: string, input: {type:string;topics:string[];questionCount:number;formats:string[]}) {
  const graph = await request<{data:{nodes:Array<{key:string;attributes:{label:string}}>}}>(`/courses/${id}/knowledge-graph`)
  const result = await request<{data:{id:string}}>(`/courses/${id}/exams`, {method:'POST',body:JSON.stringify({ title: `Práctica: ${input.topics[0] ?? 'repaso'}`, kind: input.type === 'Diagnóstico' ? 'diagnostic' : 'practice', conceptIds: graph.data.nodes.filter(n=>input.topics.includes(n.attributes.label)).map(n=>n.key), questionCount:input.questionCount, durationSeconds:Math.max(600,input.questionCount*180), questionKinds:input.formats.map(f=>f==='true-false'?'true_false':f) })})
  for (let i=0;i<240;i++) {
    const row=(await request<{data:ExamRow[]}>(`/courses/${id}/exams`)).data.find(e=>e.id===result.data.id)
    if (row?.status==='ready') return summary(row)
    if (row?.status==='failed'||row?.status==='invalidated') throw new Error('No se pudo generar la práctica con las fuentes actuales.')
    await new Promise(resolve=>setTimeout(resolve,500))
  }
  throw new Error('La práctica sigue procesándose. Vuelve a abrir la lista para consultar su estado.')
}
export async function rescheduleExam(_id:string,_at:string):Promise<void> { throw new Error('La reprogramación todavía no está disponible.') }
