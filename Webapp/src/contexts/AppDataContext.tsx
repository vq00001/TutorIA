import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { databaseApi, type TutorIADataApi } from '../services/databaseApi'
import type { ChatMessage, ChatMessageDto, Course, DashboardSummary, GraphologyExport, MaterialDto, MaterialFile, StudentProfile } from '../types'

type LoadStatus = 'loading' | 'ready' | 'error'
interface AppDataContextValue {
  status: LoadStatus; profile: StudentProfile | null; summary: DashboardSummary | null; courses: Course[]
  materialsByCourse: Record<string, MaterialFile[]>; messagesByCourse: Record<string, ChatMessage[]>
  graphsByCourse: Record<string, GraphologyExport>; loadError: string | null
  sendPrompt: (courseId: string, content: string) => void
  addMaterials: (courseId: string, files: File[]) => Promise<MaterialFile[]>
  removeMaterial: (courseId: string, materialId: string) => Promise<void>
}
const AppDataContext = createContext<AppDataContextValue | null>(null)
const adaptMaterial = (dto: MaterialDto): MaterialFile => ({ ...dto, addedAt: new Date(dto.addedAt) })
const adaptMessage = (dto: ChatMessageDto): ChatMessage => ({ ...dto, createdAt: new Date(dto.createdAt) })

/** Loads typed JSON records once, then exposes page-ready maps keyed by course id. */
export function AppDataProvider({ children, api = databaseApi }: { children: ReactNode; api?: TutorIADataApi }) {
  const [status, setStatus] = useState<LoadStatus>('loading')
  const [profile, setProfile] = useState<StudentProfile | null>(null)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [courses, setCourses] = useState<Course[]>([])
  const [materialsByCourse, setMaterialsByCourse] = useState<Record<string, MaterialFile[]>>({})
  const [messagesByCourse, setMessagesByCourse] = useState<Record<string, ChatMessage[]>>({})
  const [graphsByCourse, setGraphsByCourse] = useState<Record<string, GraphologyExport>>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    async function load() {
      try {
        const [profileResponse, summaryResponse, coursesResponse] = await Promise.all([api.getStudentProfile(), api.getDashboardSummary(), api.getCourses()])
        const loaded = await Promise.all(coursesResponse.data.map(async (course) => {
          const [m, chat, graph] = await Promise.all([api.getCourseMaterials(course.id), api.getCourseMessages(course.id), api.getCourseGraph(course.id)])
          return { id: course.id, materials: m.data.map(adaptMaterial), messages: chat.data.map(adaptMessage), graph: graph.data }
        }))
        if (!active) return
        setProfile(profileResponse.data); setSummary(summaryResponse.data); setCourses(coursesResponse.data)
        setMaterialsByCourse(Object.fromEntries(loaded.map((item) => [item.id, item.materials])))
        setMessagesByCourse(Object.fromEntries(loaded.map((item) => [item.id, item.messages])))
        setGraphsByCourse(Object.fromEntries(loaded.map((item) => [item.id, item.graph])))
        setStatus('ready')
      } catch (error) {
        if (!active) return
        setLoadError(error instanceof Error ? error.message : 'No se pudieron cargar los datos.'); setStatus('error')
      }
    }
    void load()
    return () => { active = false }
  }, [api])
  const sendPrompt = useCallback((courseId: string, content: string) => {
    const now = new Date()
    setMessagesByCourse((current) => ({ ...current, [courseId]: [...(current[courseId] ?? []),
      { id: crypto.randomUUID(), role: 'user', createdAt: now, parts: [{ type: 'markdown', content }] },
      { id: crypto.randomUUID(), role: 'assistant', createdAt: new Date(now.getTime() + 1000), awaitingResponse: true, parts: [] },
    ] }))
  }, [])
  const addMaterials = useCallback(async (courseId: string, files: File[]) => {
    const response = await api.createCourseMaterials(courseId, files.map(({ name, size }) => ({ name, size })))
    const created = response.data.map(adaptMaterial)
    setMaterialsByCourse((current) => ({ ...current, [courseId]: [...created, ...(current[courseId] ?? [])] }))
    return created
  }, [api])
  const removeMaterial = useCallback(async (courseId: string, materialId: string) => {
    const result = await api.deleteCourseMaterial(courseId, materialId)
    if (result.data.deleted) setMaterialsByCourse((current) => ({ ...current, [courseId]: (current[courseId] ?? []).filter((item) => item.id !== materialId) }))
  }, [api])
  const value = useMemo(() => ({ status, profile, summary, courses, materialsByCourse, messagesByCourse, graphsByCourse, loadError, sendPrompt, addMaterials, removeMaterial }), [status, profile, summary, courses, materialsByCourse, messagesByCourse, graphsByCourse, loadError, sendPrompt, addMaterials, removeMaterial])
  return <AppDataContext.Provider value={value}>{children}</AppDataContext.Provider>
}
export function useAppData() { const value = useContext(AppDataContext); if (!value) throw new Error('useAppData debe usarse dentro de AppDataProvider'); return value }
