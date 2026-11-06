import { useLocation } from 'react-router-dom'
import { request } from '../services/http'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import {
  databaseApi,
  type TutorIADataApi,
} from '../services/databaseApi'

import type {
  ChatMessage,
  ChatMessageDto,
  Course,
  DashboardSummary,
  GraphologyExport,
  MaterialDto,
  MaterialFile,
  NewCourse,
  StudentProfile,
} from '../types'

// Types

type LoadStatus = 'loading' | 'ready' | 'error'

interface AppDataContextValue {
  status: LoadStatus
  profile: StudentProfile | null
  summary: DashboardSummary | null
  courses: Course[]

  materialsByCourse: Record<string, MaterialFile[]>
  messagesByCourse: Record<string, ChatMessage[]>
  graphsByCourse: Record<string, GraphologyExport>

  loadError: string | null

  sendPrompt: (courseId: string, content: string) => void
  addCourse: (course: NewCourse) => Promise<Course>
  addMaterials: (courseId: string, files: File[]) => Promise<MaterialFile[]>
  removeMaterial: (courseId: string, materialId: string) => Promise<void>
}

// Context

const AppDataContext = createContext<AppDataContextValue | null>(null)

// Adapters

const adaptMaterial = (dto: MaterialDto): MaterialFile => ({
  ...dto,
  addedAt: new Date(dto.addedAt),
})

const adaptMessage = (dto: ChatMessageDto): ChatMessage => ({
  ...dto,
  createdAt: new Date(dto.createdAt),
})

// Provider

/**
 * Loads typed JSON records once, then exposes page-ready maps keyed by course id.
 */
export function AppDataProvider({
  children,
  api = databaseApi,
}: {
  children: ReactNode
  api?: TutorIADataApi
}) {
  // State

  const [status, setStatus] = useState<LoadStatus>('loading')
  const [profile, setProfile] = useState<StudentProfile | null>(null)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [courses, setCourses] = useState<Course[]>([])

  const [materialsByCourse, setMaterialsByCourse] = useState<
    Record<string, MaterialFile[]>
  >({})

  const [messagesByCourse, setMessagesByCourse] = useState<
    Record<string, ChatMessage[]>
  >({})

  const [graphsByCourse, setGraphsByCourse] = useState<
    Record<string, GraphologyExport>
  >({})

  const [loadError, setLoadError] = useState<string | null>(null)

  // Initial data loading

  useEffect(() => {
    let active = true

    async function load() {
      try {
        const [
          profileResponse,
          summaryResponse,
          coursesResponse,
        ] = await Promise.all([
          api.getStudentProfile(),
          api.getDashboardSummary(),
          api.getCourses(),
        ])

        if (!active) return

        setProfile(profileResponse.data)
        setSummary(summaryResponse.data)
        setCourses(coursesResponse.data)

        setStatus('ready')
      } catch (error) {
        if (!active) return

        setLoadError(
          error instanceof Error
            ? error.message
            : 'No se pudieron cargar los datos.'
        )

        setStatus('error')
      }
    }

    void load()

    return () => {
      active = false
    }
  }, [api])

  const location = useLocation()
  const routeId = /^\/(?:cursos|curso)\/([^/]+)/.exec(location.pathname)?.[1]
  const selectedId = location.pathname === '/materiales' ? new URLSearchParams(location.search).get('courseId') ?? courses[0]?.id : routeId
  useEffect(() => {
    if (status !== 'ready' || !selectedId || !courses.some(c => c.id === selectedId)) return
    let active = true
    const refresh = async () => {
      try {
        const [materials, messages, graph] = await Promise.all([api.getCourseMaterials(selectedId), api.getCourseMessages(selectedId), api.getCourseGraph(selectedId)])
        if (!active) return
        setMaterialsByCourse(current => ({ ...current, [selectedId]: materials.data.map(adaptMaterial) }))
        setMessagesByCourse(current => ({ ...current, [selectedId]: messages.data.map(adaptMessage) }))
        setGraphsByCourse(current => ({ ...current, [selectedId]: graph.data }))
      } catch(e) { if(active) setLoadError(e instanceof Error ? e.message : 'No se pudo actualizar el curso.') }
    }
    void refresh()
    const timer = setInterval(() => { if(document.visibilityState === 'visible') void refresh() }, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [api, status, selectedId, courses])

  useEffect(() => {
    if(status !== 'ready' || !selectedId || !courses.some(c => c.id === selectedId)) return
    let active = true
    let sessionId = ''
    let interaction = Date.now()
    const interact = () => { interaction = Date.now() }
    const beat = async () => {
      if(!active || document.visibilityState !== 'visible' || Date.now() - interaction >= 120000) return
      try {
        if(!sessionId) sessionId = (await request<{data:{id:string}}>('/study-sessions', {method:'POST',body:JSON.stringify({courseId:selectedId})})).data.id
        const result = await request<{data:{status:string}}>(`/study-sessions/${sessionId}/heartbeat`, {method:'POST',body:'{}'})
        if(result.data.status === 'closed') sessionId = ''
      } catch { sessionId = '' }
    }
    for(const event of ['pointerdown', 'keydown', 'scroll']) window.addEventListener(event,interact)
    void beat()
    const timer = setInterval(() => void beat(),30000)
    return () => { active = false; clearInterval(timer); for(const event of ['pointerdown','keydown','scroll']) window.removeEventListener(event,interact) }
  }, [selectedId, status, courses])

  // Chat messages

  const sendPrompt = useCallback(
    (courseId: string, content: string) => {
      const now = new Date()

      setMessagesByCourse((current) => ({
        ...current,
        [courseId]: [
          ...(current[courseId] ?? []),
          {
            id: crypto.randomUUID(),
            role: 'user',
            createdAt: now,
            parts: [
              {
                type: 'markdown',
                content,
              },
            ],
          },
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            createdAt: new Date(now.getTime() + 1000),
            awaitingResponse: true,
            parts: [],
          },
        ],
      }))
      void api.sendMessage(courseId, content).then(response => {
        setMessagesByCourse(current => ({ ...current, [courseId]: response.data.map(adaptMessage) }))
      }).catch(error => {
        setLoadError(error instanceof Error ? error.message : 'No se pudo enviar el mensaje.')
        void api.getCourseMessages(courseId).then(response => setMessagesByCourse(current => ({ ...current, [courseId]: response.data.map(adaptMessage) })))
      })
    },
    [api]
  )

  // Materials management

  const addMaterials = useCallback(
    async (courseId: string, files: File[]) => {
      const response = await api.createCourseMaterials(
        courseId,
        files
      )

      const created = response.data.map(adaptMaterial)

      setMaterialsByCourse((current) => ({
        ...current,
        [courseId]: [
          ...created,
        ],
      }))

      return created
    },
    [api]
  )

  const removeMaterial = useCallback(
    async (courseId: string, materialId: string) => {
      const result = await api.deleteCourseMaterial(
        courseId,
        materialId
      )

      if (result.data.deleted) {
        setMaterialsByCourse((current) => ({
          ...current,
          [courseId]: (current[courseId] ?? []).filter(
            (item) => item.id !== materialId
          ),
        }))
      }
    },
    [api]
  )

  // Course creation
  const addCourse = useCallback(async (input: NewCourse) => {
    const response = await api.createCourse(input)
    const course = response.data
    setCourses((current) => [course, ...current])
    setMaterialsByCourse((current) => ({ ...current, [course.id]: [] }))
    setMessagesByCourse((current) => ({ ...current, [course.id]: [] }))
    setGraphsByCourse((current) => ({ ...current, [course.id]: { nodes: [], edges: [] } }))
    return course
  }, [api])

  // Context value

  const value = useMemo(
    () => ({
      status,
      profile,
      summary,
      courses,
      materialsByCourse,
      messagesByCourse,
      graphsByCourse,
      loadError,
      sendPrompt,
      addCourse,
      addMaterials,
      removeMaterial,
    }),
    [
      status,
      profile,
      summary,
      courses,
      materialsByCourse,
      messagesByCourse,
      graphsByCourse,
      loadError,
      sendPrompt,
      addCourse,
      addMaterials,
      removeMaterial,
    ]
  )

  // Provider render

  return (
    <AppDataContext.Provider value={value}>
      {children}
    </AppDataContext.Provider>
  )
}

// Custom hook

export function useAppData() {
  const value = useContext(AppDataContext)

  if (!value) {
    throw new Error(
      'useAppData debe usarse dentro de AppDataProvider'
    )
  }

  return value
}
