import { Link } from 'react-router-dom'

import { PageHeading } from '../components/ui/PageHeading'
import { useAppData } from '../contexts/AppDataContext'

export function HomePage() {
  const { profile, summary, courses, status, loadError } = useAppData()

  if (status === 'loading') {
    return (
      <div className="page-loading">
        Cargando tu espacio de aprendizaje…
      </div>
    )
  }

  if (status === 'error' || !profile || !summary) {
    return (
      <div className="load-error">
        No se pudieron cargar los datos. {loadError}
      </div>
    )
  }

  const nextCourse =
    courses.find((course) => course.id === summary.nextCourseId) ??
    courses[0]

  const firstName = profile.displayName.split(/\s+/)[0]

  const today = new Intl.DateTimeFormat('es-CL', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(new Date())

  const maxHours = Math.max(...summary.studyHoursByDay, 0.1)

  return (
    <div className="home-page">
      {/* Welcome banner */}
      <section className="welcome-banner">
        <span className="eyebrow">
          {today.toLocaleUpperCase('es-CL')} ✳
        </span>

        <h1>
          Hola, {firstName} <span>✦</span>
        </h1>

        <p>¿Qué te gustaría aprender hoy?</p>

        {nextCourse && (
          <Link
            className="welcome-cta"
            to={`/cursos/crear`}

          >
            Nuevo Curso →
          </Link>
        )}

        <div className="welcome-orbit" aria-hidden="true">
          ∫
        </div>
      </section>

      {/* Study overview */}
      <section className="overview-strip">
        <div>
          <span>◷ Tiempo de estudio</span>

          <strong>
            {summary.studyHours.toLocaleString('es-CL')} h{' '}
            <small>{summary.studyHoursPeriod}</small>
          </strong>

          <div className="mini-bars">
            {summary.studyHoursByDay.map((hours, index) => (
              <i
                key={index}
                title={`${hours} horas`}
                style={{
                  height: `${Math.max(10, (hours / maxHours) * 100)}%`,
                }}
              />
            ))}
          </div>
        </div>

        <div>
          <span>✦ Racha actual</span>

          <strong>
            {summary.streakDays} <small>días seguidos</small>
          </strong>
        </div>

        <div>
          <span>✓ Conceptos dominados</span>

          <strong>
            {summary.masteredConcepts}{' '}
            <small>+{summary.masteredConceptsChange}</small>
          </strong>
        </div>
      </section>

      {/* Main content */}
      <div className="home-columns">
        <section>
          <PageHeading
            title="Mis cursos"
          />

          <div className="course-cards">
            {courses.map((course) => (
              <Link
                className={`course-card course-${course.color}`}
                to={`/cursos/${course.id}`}
                key={course.id}
              >
                <span className="course-subject">
                  {course.icon} {course.subject}
                </span>

                <h2>{course.name}</h2>

                <p>
                  Continúa con <strong>{course.nextTopic}</strong>
                </p>

                <div className="course-progress-meta">
                  <span>Tu progreso</span>
                  <strong>{course.progress}%</strong>
                </div>

                <div className="progress-track">
                  <i style={{ width: `${course.progress}%` }} />
                </div>

                <small>
                  {course.lastActivity} <span>↗</span>
                </small>
              </Link>
            ))}
          </div>

          {courses.length === 0 && (
            <div className="no-course-card">
              Aún no tienes cursos disponibles.
            </div>
          )}
        </section>

        {/* Sidebar */}
        <aside className="home-aside">
          <section className="continue-card">
            <span className="eyebrow">
              RETOMA DONDE QUEDASTE
            </span>

            <h2>Tu próxima sesión</h2>

            {nextCourse ? (
              <>
                <strong>{nextCourse.nextTopic}</strong>

                <p>
                  {nextCourse.name} · {summary.nextSessionMinutes} min aprox.
                </p>

                <p>
                  Tu ruta de hoy: {summary.roadmapStep} de{' '}
                  {summary.roadmapTotal} pasos
                </p>

                <div className="progress-track">
                  <i
                    style={{
                      width: `${summary.roadmapTotal
                        ? (summary.roadmapStep / summary.roadmapTotal) * 100
                        : 0
                        }%`,
                    }}
                  />
                </div>

                <Link
                  className="primary-button"
                  to={`/cursos/${nextCourse.id}`}
                >
                  Continuar sesión →
                </Link>
              </>
            ) : (
              <p>Añade un curso para empezar.</p>
            )}
          </section>

          <blockquote>
            “El aprendizaje no es un destino, sino una forma de viajar.”
            <small>Una pausa para inspirarte ✦</small>
          </blockquote>
        </aside>
      </div>
    </div>
  )
}
