
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
import { sendPrompt as requestPrompt, uploadPromptFiles, waitForAssistant } from '../services/llmApi'

import type {
  ChatMessage,
  ChatMessageDto,
  Course,
  DashboardSummary,
  GraphologyExport,
  MaterialDto,
  MaterialFile,
  MessageDto,
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

  sendPrompt: (courseId: string, content: string, files?: File[]) => Promise<void>

  addCourse: (course: NewCourse) => Promise<Course>
  addMaterials: (courseId: string, files: File[]) => Promise<MaterialFile[]>
  removeMaterial: (courseId: string, materialId: string) => Promise<void>
  refreshMaterials: (courseId: string) => Promise<void>
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

const adaptAssistantMessage = (dto: MessageDto): ChatMessage => ({
  id: dto.id,
  role: dto.role,
  createdAt: new Date(dto.createdAt),
  parts: [{ type: 'markdown', content: dto.content }],
  sourceDocuments: dto.sourceDocuments ?? [],
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
  const [conversationByCourse, setConversationByCourse] = useState<Record<string, string>>({})

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

        const loaded = await Promise.all(
          coursesResponse.data.map(async (course) => {
            const [materials, chat, graph] = await Promise.all([
              api.getCourseMaterials(course.id),
              api.getCourseMessages(course.id),
              api.getCourseGraph(course.id),
            ])

            return {
              id: course.id,
              materials: materials.data.map(adaptMaterial),
              messages: chat.data.map(adaptMessage),
              graph: graph.data,
            }
          })
        )

        if (!active) return

        setProfile(profileResponse.data)
        setSummary(summaryResponse.data)
        setCourses(coursesResponse.data)

        setMaterialsByCourse(
          Object.fromEntries(
            loaded.map((item) => [item.id, item.materials])
          )
        )

        setMessagesByCourse(
          Object.fromEntries(
            loaded.map((item) => [item.id, item.messages])
          )
        )

        setGraphsByCourse(
          Object.fromEntries(
            loaded.map((item) => [item.id, item.graph])
          )
        )

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

  // Chat messages

  const sendPrompt = useCallback(
    async (courseId: string, content: string, files: File[] = []) => {
      const now = new Date()
      const placeholderId = crypto.randomUUID()

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
            id: placeholderId,
            role: 'assistant',
            createdAt: new Date(now.getTime() + 1000),
            awaitingResponse: true,
            parts: [],
          },
        ],
      }))

      try {
        await uploadPromptFiles(courseId, files)
        const response = await requestPrompt(courseId, content, conversationByCourse[courseId])
        setConversationByCourse((current) => ({
          ...current,
          [courseId]: response.data.conversationId,
        }))
        const assistant = await waitForAssistant(
          courseId,
          response.data.conversationId,
          response.data.jobId,
          response.data.messageId,
        )

        setMessagesByCourse((current) => ({
          ...current,
          [courseId]: [
            ...(current[courseId] ?? []).filter((message) => message.id !== placeholderId),
            adaptAssistantMessage(assistant),
          ],
        }))
      } catch (error) {
        const errorMessage = error instanceof Error
          ? error.message
          : 'No se pudo obtener una respuesta del tutor.'

        setMessagesByCourse((current) => ({
          ...current,
          [courseId]: [
            ...(current[courseId] ?? []).filter((message) => message.id !== placeholderId),
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              createdAt: new Date(),
              isError: true,
              parts: [{ type: 'markdown', content: errorMessage }],
            },
          ],
        }))
      }
    },
    [conversationByCourse]
  )

  // Materials management

  const refreshMaterials = useCallback(async (courseId: string) => {
    const response = await api.getCourseMaterials(courseId)
    setMaterialsByCourse((current) => ({
      ...current,
      [courseId]: response.data.map(adaptMaterial),
    }))
  }, [api])

  const addMaterials = useCallback(
    async (courseId: string, files: File[]) => {
      const response = await api.createCourseMaterials(
        courseId,
        files,
      )

      const created = response.data.map(adaptMaterial)

      setMaterialsByCourse((current) => ({
        ...current,
        [courseId]: [
          ...created,
          ...(current[courseId] ?? []),
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
      refreshMaterials,
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
      refreshMaterials,
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
