import { lazy, Suspense, useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ChatComposer } from '../components/chat/ChatComposer'
import { MessageRenderer } from '../components/chat/MessageRenderer'
import { useAppData } from '../contexts/AppDataContext'

const KnowledgeGraph = lazy(() =>
  import('../components/graph/KnowledgeGraph').then((module) => ({
    default: module.KnowledgeGraph,
  })),
)

export function ChatPage() {
  const { courseId } = useParams()
  const data = useAppData()

  const course = data.courses.find((item) => item.id === courseId)

  const messages = course
    ? (data.messagesByCourse[course.id] ?? [])
    : []

  const materials = course
    ? (data.materialsByCourse[course.id] ?? [])
    : []

  const graph = course
    ? (data.graphsByCourse[course.id] ?? { nodes: [], edges: [] })
    : { nodes: [], edges: [] }

  const scrollRef = useRef<HTMLDivElement>(null)

  const userInitials =
    data.profile?.displayName
      .split(/\s+/)
      .map((part) => part[0])
      .slice(0, 2)
      .join('') ?? '·'

  // Automatically scroll to the latest message.
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages])

  // Handle loading and error states.
  if (data.status === 'loading') {
    return <div className="page-loading">Cargando el curso…</div>
  }

  if (data.status === 'error') {
    return (
      <div className="load-error">
        No se pudo cargar el curso. {data.loadError}
      </div>
    )
  }

  if (!course) {
    return (
      <div className="load-error">
        No encontramos ese curso. <Link to="/">Volver a mis cursos</Link>
      </div>
    )
  }

  return (
    <div className="chat-page">
      {/* Course heading */}
      <div className="chat-page-heading">

        <div className="chat-course-title">
          <span className={`course-emblem ${course.color}-emblem`}>
            {course.icon}
          </span>

          <div>
            <h1><Link to="/" style={{ color: 'var(--yellow)' }}>Mis cursos</Link> / {course.name}</h1>
          </div>
          <div className="chat-session-bar">
            <span className="live-dot" />
            Sesión de aprendizaje · {course.name}
          </div>

        </div>
      </div>

      <div className="chat-layout">
        {/* Main chat panel */}
        <section className="chat-main-panel">

          <div className="messages-scroll" ref={scrollRef}>
            <div className="session-date">Sesión actual</div>

            {messages.map((message) => {
              if (message.role === 'assistant') {
                return (
                  <article className="assistant-message" key={message.id}>
                    <span className="assistant-avatar">✳</span>

                    <div className="assistant-body">
                      <div className="message-author">
                        TutorIA
                        <small>
                          {message.awaitingResponse
                            ? 'Preparado para responder'
                            : 'Tutor de aprendizaje'}
                        </small>
                      </div>

                      <MessageRenderer message={message} />
                    </div>
                  </article>
                )
              }

              return (
                <article className="user-message" key={message.id}>
                  <div className="user-bubble">
                    <MessageRenderer message={message} />
                  </div>

                  <span className="avatar message-avatar">
                    {userInitials}
                  </span>
                </article>
              )
            })}

            {messages.length === 0 && (
              <p className="context-empty">
                Escribe una pregunta para comenzar.
              </p>
            )}
          </div>

          {/* Message composer */}
          <div className="chat-compose-area">
            <ChatComposer courseId={course.id} />

            <small className="privacy-note">
              ✦ TutorIA puede cometer errores. Contrasta información importante
              con tus materiales.
            </small>
          </div>
        </section>

        {/* Course context panel */}
        <aside className="chat-context-panel">
          {/* Knowledge graph */}
          <div className="context-heading">
            <div>
              <h2>Ruta de Aprendizaje</h2>
            </div>

            <Link
              to={`/cursos/${course.id}/grafo`}
              aria-label="Abrir mapa completo"
            >
              ↗
            </Link>
          </div>

          {graph.nodes.length > 0 ? (
            <Suspense
              fallback={<div className="graph-loading">Cargando mapa…</div>}
            >
              <KnowledgeGraph data={graph} />
            </Suspense>
          ) : (
            <p className="context-empty">
              Este curso aún no tiene un mapa.
            </p>
          )}

          <div className="context-divider" />

          {/* Course materials */}
          <div className="context-subheading">
            <strong>Materiales del curso</strong>

            <Link to={`/materiales?courseId=${course.id}`}>
              Ver todos →
            </Link>
          </div>

          {materials.slice(0, 3).map((file) => (
            <div className="context-file" key={file.id}>
              <span className="file-icon">TXT</span>
              {file.name}
            </div>
          ))}

          {materials.length === 0 && (
            <p className="context-empty">
              Todavía no hay materiales.
            </p>
          )}

          <Link
            className="add-material-link"
            to={`/materiales?courseId=${course.id}`}
          >
            ＋ Añadir material
          </Link>
        </aside>
      </div>
    </div>
  )
}
