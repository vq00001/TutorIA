import { CalendarDays, Clock3, RotateCcw, ArrowRight, CheckCircle2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ExamSummary } from '../../types/exams'

/** Tarjeta compacta compartida por los exámenes próximos, disponibles e históricos. */
export function ExamCard({ exam, courseId, onReschedule }: { exam: ExamSummary; courseId: string; onReschedule?: (exam: ExamSummary) => void }) {
  const completed = exam.status === 'completed'
  const available = exam.status === 'available'
  const date = new Date(exam.scheduledAt)
  const attemptPath=`/cursos/${courseId}/examen/${exam.id}${exam.attemptId?'?attemptId='+encodeURIComponent(exam.attemptId):''}`
  const dateLabel = available ? 'Disponible ahora' : date.toLocaleDateString('es-CL', { day: 'numeric', month: 'short', year: 'numeric' })

  return <article className="exam-card">
    <div className={`exam-card-icon ${completed ? 'is-complete' : available ? 'is-available' : ''}`}>{completed ? <CheckCircle2 size={19} /> : <CalendarDays size={19} />}</div>
    <div className="exam-card-content">
      <div className="exam-card-title-row"><div><span className="exam-type-label">{exam.type}</span><h3>{exam.title}</h3></div>{completed && exam.score !== undefined && <span className="exam-score">{exam.score}%</span>}</div>
      <div className="exam-meta"><span><CalendarDays size={14} />{dateLabel}</span><span><Clock3 size={14} />{exam.durationMinutes} min</span><span>{exam.questionCount} preguntas</span></div>
      <div className="exam-topics">{exam.topics.map((topic) => <span key={topic}>{topic}</span>)}</div>
    </div>
    <div className="exam-card-actions">
      {available && <Link className="primary-button" to={attemptPath}>{exam.attemptId?'Continuar':'Comenzar'} <ArrowRight size={15} /></Link>}
      {!completed && onReschedule && <button className="secondary-button" onClick={() => onReschedule(exam)}><RotateCcw size={14} /> Reprogramar</button>}
      {exam.attemptStatus==='needs_review'&&<span>Pendiente de revisión · sin puntaje final</span>}
      {completed && exam.attemptId && <Link className="secondary-button" to={attemptPath}>Ver revisión <ArrowRight size={14} /></Link>}
    </div>
  </article>
}
