import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { ManualReview } from './ManualReview'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('../../services/http', () => ({ request }))
beforeEach(() => { request.mockReset() })

it('labels self-review as non-evidence and saves the answer UUID', async () => {
  request.mockResolvedValue({ data: { method: 'manual_self' } })
  const reviewed = vi.fn().mockResolvedValue(undefined)
  render(<ManualReview attemptId="attempt" answerId="answer-uuid" maximum={2} onReviewed={reviewed} />)
  expect(screen.getByText(/no acreditará dominio/)).toBeVisible()
  const points = screen.getByLabelText('Puntaje de autoevaluación (máximo 2)')
  expect(points).toHaveAttribute('max', '2')
  fireEvent.change(points, { target: { value: '1.25' } })
  fireEvent.change(screen.getByLabelText('Comentario de autoevaluación'), { target: { value: 'Revisé la rúbrica.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Guardar autoevaluación' }))
  await waitFor(() => expect(reviewed).toHaveBeenCalledTimes(1))
  expect(request).toHaveBeenCalledWith('/attempts/attempt/answers/answer-uuid/review', { method: 'POST', body: JSON.stringify({ points: '1.25', feedback: 'Revisé la rúbrica.' }) })
})

it('does not announce a saved review when the server rejects it', async () => {
  request.mockRejectedValue(new Error('La práctica fue retirada.'))
  const reviewed = vi.fn()
  render(<ManualReview attemptId="attempt" answerId="answer" maximum={1} onReviewed={reviewed} />)
  fireEvent.change(screen.getByLabelText('Puntaje de autoevaluación (máximo 1)'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Guardar autoevaluación' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('La práctica fue retirada.')
  expect(reviewed).not.toHaveBeenCalled()
})
