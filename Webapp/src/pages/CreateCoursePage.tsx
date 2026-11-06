

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeading } from '../components/ui/PageHeading'
import { useAppData } from '../contexts/AppDataContext'

import { MaterialDropzone } from '../components/materials/MaterialDropzone'
import { describeUploadResults } from '../services/uploadResults'

export function CreateCoursePage() {
  const { addCourse, addMaterials } = useAppData()
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [objectives, setObjectives] = useState('')
  const [materials, setMaterials] = useState<File[]>([])
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [createdCourseId, setCreatedCourseId] = useState<string|null>(null)

  function handleFiles(files: File[]) {
    if (!files) return

    const accepted = Array.from(files).filter((file) =>
      /\.(txt|md|markdown)$/i.test(file.name) && file.size <= 30_000_000
    )

    if (accepted.length !== files.length) {
      setNotice('Solo se admiten TXT y Markdown UTF-8 de hasta 30 MB.')
    }
    if (materials.length + accepted.length > 10) {
      setNotice('Puedes adjuntar hasta 10 archivos al crear el curso.')
      return
    }

    setMaterials((previous) => [...previous, ...accepted])
  }

  function removeMaterial(index: number) {
    setMaterials((previous) =>
      previous.filter((_, i) => i !== index)
    )
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (loading || createdCourseId) return

    if (!name.trim() || !description.trim() || !objectives.trim()) {
      setNotice('Completa todos los campos obligatorios.')
      return
    }

    let createdId: string | null = null
    try {
      setLoading(true)
      setNotice('')

      const course = await addCourse({
        name: name.trim(),
        description: description.trim(),
        objectives: objectives.trim(),
      })
      createdId = course.id
      setCreatedCourseId(course.id)

      if (materials.length > 0) {
        const result = await addMaterials(course.id, materials)
        if (result.results.some(item=>item.error)) {
          setNotice(`El curso se creó. ${describeUploadResults(result.results)} Revisa los materiales para continuar.`)
          return
        }
      }

      navigate(`/cursos/${course.id}`)
    } catch {
      setNotice(createdId ? 'El curso se creó, pero no pudimos confirmar la carga de los archivos. Revisa los materiales antes de volver a subirlos.' : 'No se pudo crear el curso. Inténtalo nuevamente.')
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
                  <p>Define los datos principales de tu espacio de estudio.</p>
                </div>
                <span className="required-note">* Campos obligatorios</span>
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
                    placeholder="¿Qué conocimientos esperas adquirir? ¿Qué contenidos abarca el curso?"
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
              <Link className="course-cancel" to="/">Cancelar</Link>
              <button type="submit" disabled={loading || Boolean(createdCourseId)}>
                {loading ? 'Creando curso...' : createdCourseId ? 'Curso creado' : 'Crear curso'}
              </button>
              {createdCourseId && <><Link to={`/cursos/${createdCourseId}`}>Abrir curso</Link><Link to={`/materiales?courseId=${createdCourseId}`}>Revisar materiales</Link></>}
            </div>
          </main>

          <aside className="materials-aside">
            <section className="materials-tip create-course-tip">
              <span className="eyebrow">MATERIAL DE ESTUDIO</span>
              <h2>Comienza con una buena base</h2>
              <p>
                Agrega apuntes, guías y documentos para que tu tutor
                pueda utilizarlos como contexto durante las sesiones.
              </p>
              <MaterialDropzone onFiles={handleFiles} />
            </section>

            <section className="storage-card">
              <span>Materiales iniciales</span>
              <strong>{materials.length} archivos</strong>

              {materials.length === 0 ? (
                <small>
                  Todavía no has agregado documentos.
                </small>
              ) : (
                <ul className="initial-material-list">
                  {materials.map((file, index) => (
                    <li key={`${file.name}-${index}`}>
                      <span>{file.name}</span>
                      <button
                        type="button"
                        onClick={() => removeMaterial(index)}
                        aria-label={`Eliminar ${file.name}`}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </aside>
        </div>
      </form>
    </div>
  )
}
