import type {
  ChatMessageDto,
  Course,
  DashboardSummary,
  GraphologyExport,
  JsonApiResponse,
  MaterialDto,
  NewCourse,
  StudentProfile,
} from '../types'
import { authorizedFetch } from './authApi'
import { responseError } from './utils'

const API_BASE_URL = '/api/v1'

/** Adapter boundary between the UI DTOs and the backend API contract. */
export interface TutorIADataApi {
  getStudentProfile(): Promise<JsonApiResponse<StudentProfile>>
  getDashboardSummary(): Promise<JsonApiResponse<DashboardSummary>>
  getCourses(): Promise<JsonApiResponse<Course[]>>
  createCourse(course: NewCourse): Promise<JsonApiResponse<Course>>
  getCourseMaterials(courseId: string): Promise<JsonApiResponse<MaterialDto[]>>
  getCourseMessages(courseId: string): Promise<JsonApiResponse<ChatMessageDto[]>>
  getCourseGraph(courseId: string): Promise<JsonApiResponse<GraphologyExport>>
  createCourseMaterials(courseId: string, files: Array<{ name: string; size: number }>): Promise<JsonApiResponse<MaterialDto[]>>
  deleteCourseMaterial(courseId: string, materialId: string): Promise<JsonApiResponse<{ deleted: boolean }>>
}

interface ApiUser {
  id: string
  displayName: string
  role?: string
}

interface ApiDocument {
  id: string
  courseId: string
  name: string
  size: number
  uploadedAt: string
}

interface ApiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
}

interface ApiConversation { id: string }

interface ApiDashboardSummary {
  studyHours: number
  studyHoursPeriod: string
  studyHoursByDay: number[]
}

interface UploadResult { documentId: string | null }

function courseDto(course: Course): Course {
  return {
    ...course,
    subject: course.subject || 'General',
    progress: Number(course.progress ?? 0),
    color: course.color || 'mauve',
    icon: course.icon || 'book',
    nextTopic: course.nextTopic || 'Por descubrir',
    lastActivity: course.lastActivity || 'Sin actividad',
  }
}

async function request<T>(
  path: string,
  fallback: string,
  init?: RequestInit
): Promise<T> {
  const response = await authorizedFetch(`${API_BASE_URL}${path}`, init)
  if (!response.ok) throw new Error(await responseError(response, fallback))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function asMaterial(document: ApiDocument): MaterialDto {
  return {
    id: document.id,
    courseId: document.courseId,
    name: document.name,
    size: document.size,
    addedAt: document.uploadedAt,
  }
}

function asChatMessage(message: ApiMessage): ChatMessageDto {
  return {
    id: message.id,
    role: message.role,
    createdAt: message.createdAt,
    parts: [{ type: 'markdown', content: message.content }],
  }
}

export const databaseApi: TutorIADataApi = {
  async getStudentProfile() {
    const response = await request<JsonApiResponse<ApiUser>>(
      '/users/me', 'No se pudo obtener la información del usuario.',
    )
    return { data: { id: response.data.id, displayName: response.data.displayName, role: response.data.role ?? 'member' } }
  },

  async getDashboardSummary() {
    const response = await request<JsonApiResponse<ApiDashboardSummary>>(
      '/dashboard/summary', 'No se pudo obtener el resumen del dashboard.',
    )
    return { data: {
      ...response.data,
      streakDays: 0,
      masteredConcepts: 0,
      masteredConceptsChange: 0,
      roadmapStep: 0,
      roadmapTotal: 0,
      nextSessionMinutes: 0,
      nextCourseId: '',
    } }
  },

  async getCourses() {
    const response = await request<JsonApiResponse<Course[]>>(
      '/courses?page=1&pageSize=100', 'No se pudieron obtener los cursos.',
    )
    return { data: response.data.map(courseDto) }
  },

  async createCourse(input) {
    const response = await request<JsonApiResponse<Course>>('/courses', 'No se pudo crear el curso.', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    })
    return { data: courseDto(response.data) }
  },

  async getCourseMaterials(courseId) {
    const response = await request<JsonApiResponse<ApiDocument[]>>(
      `/courses/${encodeURIComponent(courseId)}/documents`, 'No se pudieron obtener los materiales.',
    )
    return { data: response.data.map(asMaterial) }
  },

  async getCourseMessages(courseId) {
    const response = await request<JsonApiResponse<ApiConversation[]>>(
      `/courses/${encodeURIComponent(courseId)}/conversations`, 'No se pudieron obtener las conversaciones.',
    )
    const histories = await Promise.all(response.data.map((conversation) =>
      request<{ messages: ApiMessage[] }>(
        `/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(conversation.id)}`,
        'No se pudo obtener el historial del curso.',
      )
    ))
    return { data: histories.flatMap((history) => history.messages.map(asChatMessage)) }
  },

  async getCourseGraph(courseId) {
    const response = await request<JsonApiResponse<GraphologyExport>>(
      `/courses/${encodeURIComponent(courseId)}/knowledge-graph`, 'No se pudo obtener el mapa de conceptos.',
    )
    return { data: { nodes: response.data.nodes, edges: response.data.edges, attributes: response.data.attributes } }
  },

  async createCourseMaterials(courseId, files) {
    if (files.length === 0) return { data: [] }

    const form = new FormData()

    files.forEach((file) => {
      if (!(file instanceof File)) throw new Error('Para subir materiales se necesita el archivo original.')
      form.append('files', file)
    })

    const uploaded = await request<JsonApiResponse<UploadResult[]>>(
      `/courses/${encodeURIComponent(courseId)}/documents`,
      'No se pudieron subir los materiales.',
      { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: form },
    )
    const listed = await request<JsonApiResponse<ApiDocument[]>>(
      `/courses/${encodeURIComponent(courseId)}/documents`, 'No se pudieron obtener los materiales.',
    )
    const uploadedIds = new Set(uploaded.data.map((item) => item.documentId).filter(Boolean))
    return { data: listed.data.filter((item) => uploadedIds.has(item.id)).map(asMaterial) }
  },

  async deleteCourseMaterial(courseId, materialId) {
    await request<void>(
      `/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(materialId)}`,
      'No se pudo eliminar el material.', { method: 'DELETE' },
    )
    return { data: { deleted: true } }
  },
}
