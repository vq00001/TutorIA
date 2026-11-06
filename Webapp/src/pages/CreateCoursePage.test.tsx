import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import { CreateCoursePage } from './CreateCoursePage'

const mocks = vi.hoisted(() => ({ addCourse: vi.fn(), addMaterials: vi.fn(), navigate: vi.fn() }))
vi.mock('../contexts/AppDataContext', () => ({ useAppData: () => mocks }))
vi.mock('react-router-dom', async importOriginal => ({
  ...await importOriginal<typeof import('react-router-dom')>(), useNavigate: () => mocks.navigate,
}))
beforeEach(() => {
  vi.clearAllMocks()
  mocks.addCourse.mockResolvedValue({ id: 'created-course' })
})

function submitCourse() {
  const { container } = render(<MemoryRouter><CreateCoursePage /></MemoryRouter>)
  fireEvent.change(screen.getByLabelText(/Nombre del curso/), { target: { value: 'Biología' } })
  fireEvent.change(screen.getByLabelText('Descripción'), { target: { value: 'Apuntes' } })
  fireEvent.change(screen.getByLabelText('Objetivos y alcance'), { target: { value: 'Aprender' } })
  fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [new File(['text'], 'apuntes.md')] } })
  fireEvent.click(screen.getByRole('button', { name: 'Crear curso' }))
}

it('keeps a created course accessible and names a rejected initial upload', async () => {
  mocks.addMaterials.mockResolvedValue({ materials: [], results: [{ name: 'apuntes.md', error: { code: 'DUPLICATE_DOCUMENT' } }] })
  submitCourse()
  expect(await screen.findByRole('status')).toHaveTextContent('apuntes.md: ya existe en este curso')
  expect(screen.getByRole('button', { name: 'Curso creado' })).toBeDisabled()
  expect(screen.getByRole('link', { name: 'Abrir curso' })).toHaveAttribute('href', '/cursos/created-course')
  expect(screen.getByRole('link', { name: 'Revisar materiales' })).toHaveAttribute('href', '/materiales?courseId=created-course')
  expect(mocks.addCourse).toHaveBeenCalledTimes(1)
  expect(mocks.navigate).not.toHaveBeenCalled()
})

it('does not offer to create a duplicate course after an uncertain upload', async () => {
  mocks.addMaterials.mockRejectedValue(new Error('network'))
  submitCourse()
  expect(await screen.findByRole('status')).toHaveTextContent('El curso se creó, pero no pudimos confirmar la carga')
  fireEvent.click(screen.getByRole('button', { name: 'Curso creado' }))
  expect(mocks.addCourse).toHaveBeenCalledTimes(1)
  expect(mocks.navigate).not.toHaveBeenCalled()
})
