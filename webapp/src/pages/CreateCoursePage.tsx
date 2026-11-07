import { useState, type SubmitEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeading } from '../components/ui/PageHeading'
import { useAppData } from '../contexts/AppDataContext'

import { MaterialDropzone } from '../components/materials/MaterialDropzone'
import { compatible_file_formats } from '../types'
import type { Course, MaterialFile } from '../types'
import { MaterialList } from '../components/materials/MaterialList'

// Temporary representation of a selected file.
interface PendingMaterial {
  id: string
  name: string
  size: number
  addedAt: Date
  file: File
}

export function CreateCoursePage() {
  const { addCourse, addMaterials } = useAppData()
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [objectives, setObjectives] = useState('')

  // Files selected but not yet uploaded.
  const [holdedMaterials, setHoldedMaterials] = useState<
    PendingMaterial[]
  >([])

  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)

  // Add selected files to temporary state.
  function handleFiles(files: File[]) {
    const acceptedExtensions = compatible_file_formats
      .toLowerCase()
      .split(',')
      .filter((ext) => ext.trim().startsWith('.'))
      .map((ext) => ext.trim())

    const accepted = files.filter((file) =>
      acceptedExtensions.some((ext) =>
        file.name.toLowerCase().endsWith(ext)
      )
    )

    if (accepted.length !== files.length) {
      setNotice(
        `Algunos archivos no son compatibles. Formatos admitidos: ${compatible_file_formats}.`
      )
    } else {
      setNotice('')
    }

    const newMaterials: PendingMaterial[] = accepted.map((file) => ({
      id: crypto.randomUUID(),
      name: file.name,
      size: file.size,
      addedAt: new Date(),
      file,
    }))

    setHoldedMaterials((prev) => [...prev, ...newMaterials])
  }

  // Remove a selected file from temporary state.
  function removeMaterial(id: string) {
    setHoldedMaterials((prev) =>
      prev.filter((material) => material.id !== id)
    )
  }

  // Create course and upload its selected materials.
  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!name.trim() || !description.trim() || !objectives.trim()) {
      setNotice('Completa todos los campos obligatorios.')
      return
    }

    try {
      setLoading(true)
      setNotice('')

      const course = await addCourse({
        name: name.trim(),
        description: description.trim(),
        objectives: objectives.trim(),
      })

      if (holdedMaterials.length > 0) {
        const filesToUpload = holdedMaterials.map(
          (material) => material.file
        )

        await addMaterials(course.id, filesToUpload)
      }

      navigate(`/cursos/${course.id}`)
    } catch {
      setNotice('No se pudo crear el curso. Inténtalo nuevamente.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="materials-page create-course-page">
      <div className="page-breadcrumb">
        <Link to="/">Inicio</Link> / Crear curso
      </div>

      <PageHeading
        eyebrow="TU ESPACIO DE APRENDIZAJE"
        title="Crear un curso"
        description="Organiza tus contenidos y define lo que quieres aprender."
      />

      <form onSubmit={handleSubmit}>
        <div className="materials-layout">
          <main>
            <section className="uploaded-section">
              <div className="section-title-row">
                <div>
                  <h2>Información del curso</h2>
                  <p>
                    Define los datos principales de tu espacio de estudio.
                  </p>
                </div>

                <span className="required-note">
                  * Campos obligatorios
                </span>
              </div>

              <div className="course-form">
                <label className="course-field">
                  <span>Nombre del curso <b>*</b></span>
                  <input
                    type="text"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Ej. Estructuras de Datos"
                    required
                  />
                </label>

                <label className="course-field">
                  <span>Descripción</span>
                  <textarea
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="Describe brevemente de qué trata el curso..."
                    rows={4}
                  />
                </label>

                <label className="course-field">
                  <span>Objetivos y alcance</span>
                  <textarea
                    value={objectives}
                    onChange={(event) => setObjectives(event.target.value)}
                    placeholder="¿Qué conocimientos esperas adquirir?"
                    rows={5}
                  />
                </label>
              </div>
            </section>

            {notice && (
              <p className="upload-notice" role="status">
                {notice}
              </p>
            )}

            <div className="course-form-actions">
              <Link className="course-cancel" to="/">
                Cancelar
              </Link>

              <button type="submit" disabled={loading}>
                {loading ? 'Creando curso...' : 'Crear curso'}
              </button>
            </div>
          </main>

          <aside className="materials-aside">
            <section className="materials-tip create-course-tip">
              <span className="eyebrow">MATERIAL DE ESTUDIO</span>
              <br />
              <br />
              <div>
                <MaterialDropzone onFiles={handleFiles} />
                <br />
                <MaterialList
                  materials={holdedMaterials}
                  onRemove={removeMaterial}
                />
              </div>
            </section>
          </aside>
        </div>
      </form>
    </div>
  )
}
