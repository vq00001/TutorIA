import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { MaterialDropzone } from '../components/materials/MaterialDropzone'
import { MaterialList } from '../components/materials/MaterialList'
import { PageHeading } from '../components/ui/PageHeading'
import { useAppData } from '../contexts/AppDataContext'
import { compatible_file_formats, isSupportedFile } from '../types'

export function MaterialsPage() {
  const {
    status,
    courses,
    materialsByCourse,
    addMaterials,
    removeMaterial,
  } = useAppData()

  const [params, setParams] = useSearchParams()
  const [notice, setNotice] = useState('')

  const requested = params.get('courseId')
  const course = courses.find((item) => item.id === requested) ?? courses[0]
  const materials = course ? materialsByCourse[course.id] ?? [] : []

  async function upload(files: File[]) {
    if (!course) {
      return setNotice(
        'Crea o selecciona un curso antes de subir material.',
      )
    }

    const accepted = files.filter(isSupportedFile)

    if (!accepted.length) {
      return setNotice(`Por ahora solo se admiten archivos ${compatible_file_formats}.`)
    }

    try {
      const result = await addMaterials(course.id, accepted)

      setNotice(
        `${result.length} archivo(s) añadido(s) a ${course.name}.`,
      )
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'No se pudieron añadir los archivos.')
    }
  }

  if (status === 'loading') {
    return (
      <div className="page-loading">
        Cargando cursos y materiales…
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="load-error">
        No se pudieron cargar los materiales.
      </div>
    )
  }

  return (
    <div className="materials-page">
      <div className="page-breadcrumb">
        <Link to="/">Inicio</Link> / Mis materiales
      </div>

      <PageHeading
        eyebrow="TU BIBLIOTECA DE ESTUDIO"
        title="Materiales por curso"
        description="Cada curso tiene su propia colección de apuntes."
        action={
          course && (
            <label className="course-picker-label">
              Curso
              <select
                value={course.id}
                onChange={(event) =>
                  setParams({ courseId: event.target.value })
                }
              >
                {courses.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
          )
        }
      />

      <div className="materials-layout">
        <main>
          {course ? (
            <MaterialDropzone
              onFiles={(files) => {
                void upload(files)
              }}
            />
          ) : (
            <div className="no-course-card">
              Aún no tienes cursos disponibles.
            </div>
          )}

          {notice && (
            <p className="upload-notice" role="status">
              ✓ {notice}
            </p>
          )}

          <section className="uploaded-section">
            <div className="section-title-row">
              <div>
                <h2>
                  Archivos de {course?.name ?? 'este curso'}{' '}
                  <span>{materials.length}</span>
                </h2>
                <p>Solo están disponibles en sesiones de este curso</p>
              </div>
            </div>

            <MaterialList
              materials={materials}
              onRemove={(id) => {
                if (course) {
                  void removeMaterial(course.id, id)
                }
              }}
            />
          </section>
        </main>

        <aside className="materials-aside">
          <section className="materials-tip">
            <span className="eyebrow">UN CONSEJO</span>

            <h2>
              Mejores apuntes,
              <br />
              mejores sesiones
            </h2>

            <p>
              Agrega guías, resúmenes y apuntes de clase. Tu tutor podrá usar
              este material como contexto para ayudarte.
            </p>

            {course && (
              <Link to={`/cursos/${course.id}`}>
                Ir a mi tutor →
              </Link>
            )}
          </section>

          <section className="storage-card">
            <span>Almacenamiento</span>
            <strong>{materials.length} archivos</strong>
            <small>Solo durante esta sesión de demo.</small>
          </section>
        </aside>
      </div>
    </div>
  )
}
