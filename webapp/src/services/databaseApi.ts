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
  role: string
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
    return { data: { id: response.data.id, displayName: response.data.displayName, role: response.data.role } }
  },

  getDashboardSummary() {
    return request('/dashboard/summary', 'No se pudo obtener el resumen del dashboard.')
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
    const response = await request<JsonApiResponse<Array<{ messages: ApiMessage[] }>>>(
      `/courses/${encodeURIComponent(courseId)}/conversations`, 'No se pudieron obtener las conversaciones.',
    )
    return { data: response.data.flatMap((conversation) => conversation.messages.map(asChatMessage)) }
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

    const response = await request<JsonApiResponse<ApiDocument[]>>(
      `/courses/${encodeURIComponent(courseId)}/documents`,
      'No se pudieron subir los materiales.',
      { method: 'POST', body: form },
    )
    return { data: response.data.map(asMaterial) }
  },

  async deleteCourseMaterial(courseId, materialId) {
    await request<void>(
      `/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(materialId)}`,
      'No se pudo eliminar el material.', { method: 'DELETE' },
    )
    return { data: { deleted: true } }
  },
}
