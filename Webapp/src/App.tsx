import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppDataProvider } from './contexts/AppDataContext'
import { AppShell } from './components/layout/AppShell'
import './styles/exams.css'

const HomePage = lazy(() => import('./pages/HomePage').then((m) => ({ default: m.HomePage })))
const ChatPage = lazy(() => import('./pages/ChatPage').then((m) => ({ default: m.ChatPage })))
const MaterialsPage = lazy(() => import('./pages/MaterialsPage').then((m) => ({ default: m.MaterialsPage })))
const CreateCoursePage = lazy(() => import('./pages/CreateCoursePage').then((m) => ({ default: m.CreateCoursePage })))
const GraphPage = lazy(() => import('./pages/GraphPage').then((m) => ({ default: m.GraphPage })))
const ExamPage = lazy(() => import('./pages/ExamPage').then((m) => ({ default: m.ExamPage })))
const TakeExamPage = lazy(() => import('./pages/TakeExamPage').then((m) => ({ default: m.TakeExamPage })))

export default function App() {
  return <AppDataProvider><AppShell><Suspense fallback={<div className="page-loading">Cargando módulo…</div>}><Routes>
    <Route path="/" element={<HomePage />} />
    <Route path="/cursos/:courseId" element={<ChatPage />} />
    <Route path="/cursos/crear" element={<CreateCoursePage />} />
    <Route path="/cursos/:courseId/grafo" element={<GraphPage />} />
    <Route path="/cursos/:courseId/examenes" element={<ExamPage />} />
    <Route path="/cursos/:courseId/examen/:examId" element={<TakeExamPage />} />
    {/* Compatibilidad con los enlaces antiguos al módulo de exámenes. */}
    <Route path="/cursos/:courseId/examen" element={<ExamPage />} />
    <Route path="/curso/:courseId" element={<ChatPage />} />
    <Route path="/materiales" element={<MaterialsPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense></AppShell></AppDataProvider>
}
