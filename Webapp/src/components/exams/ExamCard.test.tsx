import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it } from 'vitest'
import { ExamCard } from './ExamCard'
import type { ExamSummary } from '../../types/exams'

const base: ExamSummary = { id: 'exam', courseId: 'course', title: 'Práctica', type: 'practice', topics: [], scheduledAt: '2026-11-06T12:00:00Z', durationMinutes: 10, questionCount: 3, status: 'available' }
it('reviews the existing attempt instead of starting a new one', () => {
  render(<MemoryRouter><ExamCard courseId="course" exam={{ ...base, status: 'completed', attemptId: 'existing', attemptStatus: 'graded', score: 0 }} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: /Ver revisión/ })).toHaveAttribute('href', '/cursos/course/examen/exam?attemptId=existing')
  expect(screen.getByText('0%')).toBeVisible()
})
it('shows pending review without inventing a final score', () => {
  render(<MemoryRouter><ExamCard courseId="course" exam={{ ...base, status: 'completed', attemptId: 'pending', attemptStatus: 'needs_review' }} /></MemoryRouter>)
  expect(screen.getByText(/sin puntaje final/)).toBeVisible()
  expect(screen.queryByText('0%')).not.toBeInTheDocument()
})
it('continues a saved attempt using its server identity', () => {
  render(<MemoryRouter><ExamCard courseId="course" exam={{ ...base, attemptId: 'saved', attemptStatus: 'in_progress' }} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: /Continuar/ })).toHaveAttribute('href', '/cursos/course/examen/exam?attemptId=saved')
})
