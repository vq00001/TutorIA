import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Bookmark, Check, Flag, Send, X } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAppData } from '../contexts/AppDataContext'
import { loadExamDefinition, submitExam } from '../data/exams'
import type { ExamDefinition } from '../types/exams'

const KnowledgeGraph = lazy(() => import('../components/graph/KnowledgeGraph').then((module) => ({ default: module.KnowledgeGraph })))
type AnswerMap = Record<string, number[] | string>

/** Vista para rendir un examen, guardar avance, marcar preguntas y entregar. */
export function TakeExamPage() {
  const { courseId = '', examId = '' } = useParams()
  const navigate = useNavigate()
  const { status, courses, graphsByCourse } = useAppData()
  const course = courses.find((item) => item.id === courseId)
  const [exam, setExam] = useState<ExamDefinition | null>(null)
  const [answers, setAnswers] = useState<AnswerMap>({})
  const [flagged, setFlagged] = useState<string[]>([])
  const [activeQuestion, setActiveQuestion] = useState(0)
  const [submitted, setSubmitted] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!courseId || !examId) return
    void loadExamDefinition(courseId, examId).then((definition) => {
      setExam(definition)
      // Restaura el borrador local hasta que exista persistencia de borradores en el servidor.
      const saved = localStorage.getItem(`exam-draft:${definition.id}`)
      if (saved) {
        try {
          const draft = JSON.parse(saved) as { answers?: AnswerMap; flagged?: string[] }
          setAnswers(draft.answers ?? {})
          setFlagged(draft.flagged ?? [])
        } catch {
          localStorage.removeItem(`exam-draft:${definition.id}`)
        }
      }
    })
  }, [courseId, examId])

  const questions = exam?.questions ?? []
  const current = questions[activeQuestion]
  const answeredCount = useMemo(() => questions.filter((question) => {
    const answer = answers[question.id]
    return Array.isArray(answer) ? answer.length > 0 : Boolean(answer?.trim())
  }).length, [answers, questions])
  const progress = questions.length ? Math.round(answeredCount / questions.length * 100) : 0

  if (status === 'loading' || !exam) return <div className="page-loading">Cargando examen…</div>
  if (!course) return <div className="load-error">Curso no encontrado. <Link to="/">Volver a mis cursos</Link></div>
  const graph = graphsByCourse[course.id]

  const setChoice = (questionId: string, option: number, multiple: boolean) => {
    if (submitted) return
    setAnswers((currentAnswers) => {
      const old = Array.isArray(currentAnswers[questionId]) ? currentAnswers[questionId] as number[] : []
      const next = multiple ? old.includes(option) ? old.filter((item) => item !== option) : [...old, option] : [option]
      return { ...currentAnswers, [questionId]: next }
    })
  }

  const saveAndExit = () => {
    // Placeholder local para conservar el progreso hasta que exista el endpoint de borradores.
    localStorage.setItem(`exam-draft:${exam.id}`, JSON.stringify({ answers, flagged, savedAt: new Date().toISOString() }))
    navigate(`/cursos/${course.id}/examenes`)
  }

  const handleSubmit = async () => {
    if (submitted) return
    setSaving(true)
    try {
      await submitExam(exam.id, answers)
      setSubmitted(true)
      localStorage.removeItem(`exam-draft:${exam.id}`)
    } finally { setSaving(false) }
  }

  const isAnswered = (questionId: string) => {
    const answer = answers[questionId]
    return Array.isArray(answer) ? answer.length > 0 : Boolean(answer?.trim())
  }
  const isCorrect = (questionId: string) => {
    const question = questions.find((item) => item.id === questionId)
    if (!question?.correctAnswers) return undefined
    const answer = answers[questionId]
    const selected = Array.isArray(answer) ? [...answer].sort() : []
    const expected = [...question.correctAnswers].sort()
    return selected.length === expected.length && selected.every((value, index) => value === expected[index])
  }

  return <section className="take-exam-page">
    <header className="take-exam-header"><div className="take-exam-title"><Link to={`/cursos/${course.id}/examenes`} aria-label="Volver a exámenes"><ArrowLeft size={18} /></Link><div><div className="page-breadcrumb"><Link to={`/cursos/${course.id}/examenes`}>{course.name} / Exámenes</Link></div><h1>{exam.title}</h1></div></div><button className="secondary-button" onClick={saveAndExit}><Bookmark size={15} /> Guardar y salir</button></header>
    <div className="take-exam-layout">
      <main className="question-panel">
        <div className="question-progress"><div><span>Progreso</span><strong>{answeredCount} de {questions.length} respondidas</strong></div><span>{progress}%</span><div className="progress-track"><i style={{ width: `${progress}%` }} /></div></div>
        {submitted && <div className="submission-banner"><Check size={17} /> Examen entregado. Revisa tus respuestas y explicaciones.</div>}
        {current && <article className="exam-question"><div className="question-kicker"><span>Pregunta {activeQuestion + 1} de {questions.length}</span><button className={flagged.includes(current.id) ? 'flag-button flagged' : 'flag-button'} onClick={() => setFlagged((old) => old.includes(current.id) ? old.filter((id) => id !== current.id) : [...old, current.id])} aria-pressed={flagged.includes(current.id)}><Flag size={15} /> {flagged.includes(current.id) ? 'Marcada' : 'Marcar pregunta'}</button></div><span className="question-topic">{current.topic}</span><h2>{current.prompt}</h2>
          {(current.type === 'alternatives' || current.type === 'multiple' || current.type === 'true-false') && <div className="answer-options">{current.options?.map((option, index) => {
            const selected = (answers[current.id] as number[] | undefined)?.includes(index) ?? false
            const correct = current.correctAnswers?.includes(index) ?? false
            return <button key={option} className={`answer-option ${selected ? 'selected' : ''} ${submitted && correct ? 'answer-correct' : ''} ${submitted && selected && !correct ? 'answer-incorrect' : ''}`} onClick={() => setChoice(current.id, index, current.type === 'multiple')} disabled={submitted} aria-pressed={selected}><span className="answer-marker">{submitted && correct ? <Check size={13} /> : current.type === 'multiple' ? selected ? '✓' : '' : String.fromCharCode(65 + index)}</span><span>{option}</span>{submitted && selected && !correct && <X size={15} />}</button>
          })}</div>}
          {current.type === 'development' && <label className="development-answer"><span>Tu respuesta</span><textarea value={(answers[current.id] as string) ?? ''} onChange={(event) => setAnswers((old) => ({ ...old, [current.id]: event.target.value }))} placeholder="Escribe tu respuesta…" disabled={submitted} rows={7} /></label>}
          {submitted && current.type !== 'development' && <div className={`answer-feedback ${isCorrect(current.id) ? 'feedback-correct' : 'feedback-incorrect'}`}><strong>{isCorrect(current.id) ? 'Respuesta correcta' : 'Respuesta incorrecta'}</strong>{current.explanation && <p>{current.explanation}</p>}</div>}
          {submitted && current.type === 'development' && <div className="answer-feedback"><strong>Respuesta de referencia</strong><p>{current.expectedAnswer}</p>{current.explanation && <p>{current.explanation}</p>}</div>}
        </article>}
        <div className="question-navigation"><button className="secondary-button" disabled={activeQuestion === 0} onClick={() => setActiveQuestion((index) => index - 1)}>Anterior</button><span>{activeQuestion + 1} / {questions.length}</span><button className="primary-button" disabled={activeQuestion >= questions.length - 1} onClick={() => setActiveQuestion((index) => index + 1)}>Siguiente</button></div>
      </main>
      <aside className="take-exam-aside"><section className="question-overview"><div className="overview-heading"><div><h2>Preguntas</h2><p>Selecciona un número para navegar</p></div><span>{questions.length}</span></div><div className="question-number-grid">{questions.map((question, index) => <button key={question.id} onClick={() => setActiveQuestion(index)} className={`question-number ${activeQuestion === index ? 'current' : ''} ${isAnswered(question.id) ? 'answered' : ''} ${flagged.includes(question.id) ? 'is-flagged' : ''} ${submitted && isCorrect(question.id) === true ? 'review-correct' : ''} ${submitted && isCorrect(question.id) === false ? 'review-incorrect' : ''}`} aria-label={`Pregunta ${index + 1}${isAnswered(question.id) ? ', respondida' : ', sin responder'}${flagged.includes(question.id) ? ', marcada' : ''}`}>{flagged.includes(question.id) && <Flag size={11} fill="currentColor" />}{index + 1}</button>)}</div><div className="overview-legend"><span><i className="legend-answered" />Respondida</span><span><i />Saltada</span><span><Flag size={11} />Marcada</span></div></section>
        <section className="take-graph-card"><div className="exam-aside-heading"><div><h2>Ruta de aprendizaje</h2><p>{course.name}</p></div><Link to={`/cursos/${course.id}/grafo`}>↗</Link></div>{graph?.nodes.length ? <Suspense fallback={<div className="graph-loading">Cargando grafo…</div>}><KnowledgeGraph data={graph} /></Suspense> : <div className="exam-graph-empty">El grafo aparecerá cuando haya conceptos disponibles.</div>}</section>
        <button className="submit-exam-button" onClick={() => void handleSubmit()} disabled={saving || submitted}>{submitted ? <><Check size={16} /> Examen entregado</> : saving ? 'Entregando…' : <><Send size={16} /> Entregar examen</>}</button>
      </aside>
    </div>
  </section>
}
