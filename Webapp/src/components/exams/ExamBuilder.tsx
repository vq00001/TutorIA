import { useState } from 'react'
import type { FormEvent } from 'react'
import { Sparkles } from 'lucide-react'
import type { ExamSummary } from '../../types/exams'

const FORMATS = [
  ['alternatives', 'Alternativas'],
  ['multiple', 'Selección múltiple'],
  ['development', 'Desarrollo'],
  ['true-false', 'Verdadero y falso'],
]

/** Formulario modular para configurar y solicitar un examen personal. */
export function ExamBuilder({ courseId, topics, onGenerate }: { courseId: string; topics: string[]; onGenerate: (exam: ExamSummary) => void }) {
  const [type, setType] = useState('Práctica personal')
  const [selectedTopics, setSelectedTopics] = useState<string[]>([])
  const [questionCount, setQuestionCount] = useState(10)
  const [formats, setFormats] = useState<string[]>(['alternatives'])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const toggle = (value: string, current: string[], set: (values: string[]) => void) => set(current.includes(value) ? current.filter((item) => item !== value) : [...current, value])
  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { generatePersonalExam } = await import('../../data/exams')
      const exam = await generatePersonalExam(courseId, { type, topics: selectedTopics, questionCount, formats })
      onGenerate(exam)
    } catch(error) { setError(error instanceof Error ? error.message : 'No se pudo generar la práctica.') } finally { setBusy(false) }
  }

  return <form className="exam-builder" onSubmit={handleSubmit}>
    <div className="exam-aside-heading"><div className="builder-icon"><Sparkles size={16} /></div><div><h2>Crea un examen personal</h2><p>Practica a tu ritmo con TutorIA</p></div></div>
    <label className="exam-field">Tipo de examen<select value={type} onChange={(event) => setType(event.target.value)}><option>Práctica personal</option><option>Diagnóstico</option></select></label>
    <fieldset className="exam-topic-field"><legend>Temas <small>Selecciona uno o más</small></legend>{topics.length ? topics.map((topic) => <label className="exam-check" key={topic}><input type="checkbox" checked={selectedTopics.includes(topic)} onChange={() => toggle(topic, selectedTopics, setSelectedTopics)} />{topic}</label>) : <p className="context-empty">No hay temas para seleccionar.</p>}</fieldset>
    <label className="exam-field">Preguntas<input type="number" min="1" max="20" value={questionCount} onChange={(event) => setQuestionCount(Number(event.target.value))} /></label>
    <fieldset className="exam-format-field"><legend>Formato</legend><div>{FORMATS.map(([value, label]) => <label className="exam-check" key={value}><input type="checkbox" checked={formats.includes(value)} onChange={() => toggle(value, formats, setFormats)} />{label}</label>)}</div></fieldset>
    <button className="primary-button exam-generate" type="submit" disabled={busy || !selectedTopics.length || !formats.length}>{busy ? 'Preparando…' : <><Sparkles size={15} /> Generar examen</>}</button>
    {!selectedTopics.length && <small className="exam-hint">Elige al menos un tema para continuar.</small>}
    {error && <p role="alert">{error}</p>}
  </form>
}
