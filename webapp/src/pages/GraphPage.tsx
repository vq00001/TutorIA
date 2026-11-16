import { lazy, Suspense } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAppData } from '../contexts/AppDataContext'
import { GraphProposalPanel } from '../components/graph/GraphProposalPanel'

const KnowledgeGraph = lazy(() => import('../components/graph/KnowledgeGraph').then((m) => ({ default: m.KnowledgeGraph })))
export function GraphPage() {
  const { courseId } = useParams()
  const { status, courses, graphsByCourse, loadError } = useAppData()
  const course = courses.find((item) => item.id === courseId)
  if (status === 'loading') return <div className="page-loading">Cargando el mapa del curso…</div>
  if (status === 'error') return <div className="load-error">No se pudo cargar el mapa. {loadError}</div>
  if (!course) return <div className="load-error">Curso no encontrado. <Link to="/">Volver a mis cursos</Link></div>
  const graph = graphsByCourse[course.id]
  return <section className="graph-page"><div className="page-breadcrumb"><Link to={`/cursos/${course.id}`}>{course.name}</Link> / Mapa de conceptos</div><div className="context-heading"><div><span className="eyebrow">TU MAPA DE APRENDIZAJE</span><h1>{course.name}: conceptos conectados</h1></div><Link to={`/cursos/${course.id}`}>Volver al tutor →</Link></div><p className="context-description">Explora cómo se relacionan los conceptos y qué conocimientos preparan los siguientes temas.</p>{graph?.nodes.length ? <Suspense fallback={<div className="graph-loading">Cargando mapa…</div>}><KnowledgeGraph data={graph} className="graph-page-canvas" /></Suspense> : <div className="no-course-card">Este curso aún no tiene un mapa de conceptos.</div>}{course.myRole === 'coordinator' && !graph?.nodes.length && <GraphProposalPanel courseId={course.id} />}</section>
}
