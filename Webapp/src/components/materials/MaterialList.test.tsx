import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MaterialList } from './MaterialList'
import type { MaterialFile } from '../../types'

const material: MaterialFile = { id: 'material', courseId: 'course', name: 'Fuente.md', size: 1234, addedAt: new Date('2026-11-05T12:00:00Z'), versionId: 'version' }

describe('material publication states', () => {
  it('does not announce readiness or download while a run is queued', () => {
    render(<MaterialList materials={[{ ...material, status: 'queued' }]} onRemove={vi.fn()} />)
    expect(screen.getByText('Procesando…')).toBeVisible()
    expect(screen.queryByText('Listo')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Descargar' })).not.toBeInTheDocument()
  })
  it('links the exact published course/material/version', () => {
    render(<MaterialList materials={[{ ...material, status: 'ready' }]} onRemove={vi.fn()} />)
    expect(screen.getByRole('link', { name: 'Descargar' })).toHaveAttribute('href', '/api/v1/courses/course/materials/material/versions/version/content')
    expect(screen.getByText('MD')).toBeVisible()
  })
  it('explains a failed update without claiming it published', () => {
    render(<MaterialList materials={[{ ...material, status: 'failed', activeRunId: 'previous', errorCode: 'PROCESSING_FAILED' }]} onRemove={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('versión anterior sigue disponible')
    expect(screen.getByText('Procesamiento fallido')).toBeVisible()
    expect(screen.queryByText('Listo')).not.toBeInTheDocument()
  })
  it('uses the material id when requesting removal', () => {
    const remove = vi.fn()
    render(<MaterialList materials={[material]} onRemove={remove} />)
    fireEvent.click(screen.getByRole('button', { name: 'Quitar Fuente.md' }))
    expect(remove).toHaveBeenCalledWith('material')
  })
  it('has an explicit empty state', () => {
    render(<MaterialList materials={[]} onRemove={vi.fn()} />)
    expect(screen.getByText('Aún no hay materiales')).toBeVisible()
  })
})
