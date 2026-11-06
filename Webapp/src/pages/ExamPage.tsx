import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, BookOpenCheck } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { ExamBuilder } from '../components/exams/ExamBuilder'
import { ExamCard } from '../components/exams/ExamCard'
import { useAppData } from '../contexts/AppDataContext'
import { loadCourseExams, loadCourseTopics } from '../data/exams'
import type { ExamSummary } from '../types/exams'

const KnowledgeGraph = lazy(() => import('../components/graph/KnowledgeGraph').then((module) => ({ default: module.KnowledgeGraph })))
type ExamTab = 'upcoming' | 'history'

/** Lista de exámenes del curso y panel lateral con creación personal y grafo. */
export function ExamPage() {
  const { courseId = '' } = useParams()
  const { status, courses, graphsByCourse, loadError } = useAppData()
  const course = courses.find((item) => item.id === courseId)
  const [tab, setTab] = useState<ExamTab>('upcoming')
  const [exams, setExams] = useState<ExamSummary[]>([])
  const [topics, setTopics] = useState<string[]>([])
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (!courseId) return
    // La UI consume funciones asíncronas; al conectar el backend se conserva este contrato.
    void Promise.all([loadCourseExams(courseId), loadCourseTopics(courseId)]).then(([loadedExams, loadedTopics]) => {
      setExams(loadedExams)
      setTopics(loadedTopics)
    }).catch(error=>setNotice(error instanceof Error?error.message:'No se pudieron cargar las prácticas.'))
  }, [courseId])

  const shownExams = useMemo(() => exams.filter((exam) => tab === 'history' ? exam.status === 'completed' : exam.status !== 'completed'), [exams, tab])
  if (status === 'loading') return <div className="page-loading">Cargando exámenes…</div>
  if (status === 'error') return <div className="load-error">No se pudo cargar el curso. {loadError}</div>
  if (!course) return <div className="load-error">Curso no encontrado. <Link to="/">Volver a mis cursos</Link></div>
  const graph = graphsByCourse[course.id]

  const handleGenerate = (exam: ExamSummary) => {
    setExams((current) => [exam, ...current])
    setNotice('Tu examen personal está listo y ya aparece en Próximos y sugeridos.')
    setTab('upcoming')
  }

  return <section className="exam-page">
    <div className="page-breadcrumb"><Link to={`/cursos/${course.id}`}><ArrowLeft size={13} /> {course.name}</Link><span>/</span> Exámenes</div>
    <header className="exam-page-heading"><div><span className="eyebrow">PRACTICA Y MIDE TU AVANCE</span><h1>Exámenes</h1><p>Revisa tus evaluaciones y prepara una práctica a tu medida.</p></div><span className={`course-emblem ${course.color}-emblem`}>{course.icon}</span></header>
    <div className="exam-page-layout">
      <main className="exam-list-column">
        <div className="exam-tabs" role="tablist" aria-label="Exámenes del curso">
          <button role="tab" aria-selected={tab === 'upcoming'} className={tab === 'upcoming' ? 'active' : ''} onClick={() => setTab('upcoming')}>Próximos y sugeridos <span>{exams.filter((exam) => exam.status !== 'completed').length}</span></button>
          <button role="tab" aria-selected={tab === 'history'} className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>Históricos <span>{exams.filter((exam) => exam.status === 'completed').length}</span></button>
        </div>
        {notice && <div className="exam-notice" role="status"><BookOpenCheck size={16} />{notice}<button onClick={() => setNotice('')} aria-label="Cerrar aviso">×</button></div>}
        {tab === 'upcoming' && <p className="exam-section-intro">Evaluaciones programadas y prácticas recomendadas según tu avance en {course.name}.</p>}
        {tab === 'history' && <p className="exam-section-intro">Consulta tus exámenes rendidos y sus resultados.</p>}
        <div className="exam-card-list">{shownExams.map((exam) => <ExamCard key={exam.id} exam={exam} courseId={course.id} />)}</div>
        {shownExams.length === 0 && <div className="exam-empty"><BookOpenCheck size={25} /><strong>{tab === 'history' ? 'Aún no hay exámenes rendidos' : 'No tienes exámenes próximos'}</strong><span>Genera una práctica personal para empezar a estudiar.</span></div>}
      </main>
      <aside className="exam-aside">
        <ExamBuilder courseId={course.id} topics={topics} onGenerate={handleGenerate} />
        <section className="exam-graph-card"><div className="exam-aside-heading"><div className="builder-icon graph-icon">⌘</div><div><h2>Ruta de aprendizaje</h2><p>Conceptos de {course.name}</p></div><Link to={`/cursos/${course.id}/grafo`} aria-label="Abrir grafo completo">↗</Link></div>
          {graph?.nodes.length ? <Suspense fallback={<div className="graph-loading">Cargando mapa…</div>}><KnowledgeGraph data={graph} /></Suspense> : <div className="exam-graph-empty">El grafo aparecerá cuando haya conceptos disponibles.</div>}
          <Link className="exam-graph-link" to={`/cursos/${course.id}/grafo`}>Explorar mapa completo →</Link>
        </section>
      </aside>
    </div>
  </section>
}
