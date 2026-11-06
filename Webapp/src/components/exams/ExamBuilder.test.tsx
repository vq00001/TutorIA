import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ExamBuilder } from './ExamBuilder'

const generate = vi.hoisted(() => vi.fn())
vi.mock('../../data/exams', () => ({ generatePersonalExam: generate }))

it('offers only supported settings and sends the selected topic', async () => {
  const exam = { id: 'exam' }
  generate.mockResolvedValue(exam)
  const onGenerate = vi.fn()
  render(<ExamBuilder courseId="course" topics={['Células']} onGenerate={onGenerate} />)
  expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual(['Práctica personal', 'Diagnóstico'])
  expect(screen.queryByLabelText('Dificultad')).not.toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('Células'))
  fireEvent.click(screen.getByRole('button', { name: /Generar examen/ }))
  await waitFor(() => expect(onGenerate).toHaveBeenCalledWith(exam))
  expect(generate).toHaveBeenCalledWith('course', { type: 'Práctica personal', topics: ['Células'], questionCount: 10, formats: ['alternatives'] })
})
