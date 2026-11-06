import { useState } from 'react'
import type { FormEvent } from 'react'
import { request } from '../../services/http'

export function ManualReview({ attemptId, answerId, maximum, onReviewed }: {
  attemptId: string; answerId: string; maximum: number; onReviewed: () => Promise<void>
}) {
  const [points, setPoints] = useState('')
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await request(`/attempts/${attemptId}/answers/${answerId}/review`, {
        method: 'POST', body: JSON.stringify({ points, feedback }),
      })
      await onReviewed()
    } catch (error) { setError(error instanceof Error ? error.message : 'No se guardó la revisión.') }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="answer-feedback">
    <p>La corrección automática necesita revisión. Puedes registrar una autoevaluación; se mostrará como tal y no acreditará dominio del concepto.</p>
    <label className="exam-field">Puntaje de autoevaluación (máximo {maximum})<input type="number" min="0" max={maximum} step="0.00001" required value={points} onChange={e => setPoints(e.target.value)} /></label>
    <label className="exam-field">Comentario de autoevaluación<textarea value={feedback} maxLength={10000} onChange={e => setFeedback(e.target.value)} /></label>
    <button className="secondary-button" type="submit" disabled={busy}>{busy ? 'Guardando…' : 'Guardar autoevaluación'}</button>
    {error && <p role="alert">{error}</p>}
  </form>
}
