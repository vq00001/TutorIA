import { demoDatabase } from '../data/demoDatabase'
import type { ChatMessageDto, Course, DashboardSummary, GraphologyExport, JsonApiResponse, MaterialDto, NewCourse, StudentProfile } from '../types'

/** Adapter boundary for demo data and the document-upload HTTP endpoint. */
export interface TutorIADataApi {
  getStudentProfile(): Promise<JsonApiResponse<StudentProfile>>
  getDashboardSummary(): Promise<JsonApiResponse<DashboardSummary>>
  getCourses(): Promise<JsonApiResponse<Course[]>>
  createCourse(course: NewCourse): Promise<JsonApiResponse<Course>>
  getCourseMaterials(courseId: string): Promise<JsonApiResponse<MaterialDto[]>>
  getCourseMessages(courseId: string): Promise<JsonApiResponse<ChatMessageDto[]>>
  getCourseGraph(courseId: string): Promise<JsonApiResponse<GraphologyExport>>
  createCourseMaterials(courseId: string, files: File[]): Promise<JsonApiResponse<MaterialDto[]>>
  deleteCourseMaterial(courseId: string, materialId: string): Promise<JsonApiResponse<{ deleted: boolean }>>
}

function jsonResponse<T>(data: T): JsonApiResponse<T> { return JSON.parse(JSON.stringify({ data })) as JsonApiResponse<T> }
const delay = () => new Promise<void>((resolve) => window.setTimeout(resolve, 80))

interface UploadedDocument {
  id: string
  courseId: string
  name: string
  size: number
  uploadedAt: string
}

interface DocumentListResponse {
  data: UploadedDocument[]
}

async function uploadCourseMaterials(
  courseId: string,
  files: File[],
): Promise<JsonApiResponse<MaterialDto[]>> {
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file, file.name))

  const response = await fetch(
    `/api/v1/courses/${encodeURIComponent(courseId)}/documents`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: formData,
    },
  )

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(
      `No se pudieron subir los archivos (HTTP ${response.status})${detail ? `: ${detail}` : '.'}`,
    )
  }

  const result = await response.json() as DocumentListResponse
  return jsonResponse(
    result.data.map((document) => ({
      id: document.id,
      courseId: document.courseId,
      name: document.name,
      size: document.size,
      addedAt: document.uploadedAt,
    })),
  )
}

/** Demo database: responses cross the same JSON DTO boundary as the future API. */
export const databaseApi: TutorIADataApi = {
  async getStudentProfile() { await delay(); return jsonResponse(demoDatabase.profile) },
  async getDashboardSummary() { await delay(); return jsonResponse(demoDatabase.summary) },
  async getCourses() { await delay(); return jsonResponse(demoDatabase.courses) },
  async createCourse(input) {
    await delay()
    const course: Course = {
      id: crypto.randomUUID(),
      name: input.name,
      description: input.description,
      objectives: input.objectives,
      subject: 'Nuevo curso',
      progress: 0,
      color: 'mauve',
      icon: '✦',
      nextTopic: input.objectives,
      lastActivity: 'Recién creado',
    }
    demoDatabase.courses.unshift(course)
    demoDatabase.materialsByCourse[course.id] = []
    demoDatabase.messagesByCourse[course.id] = []
    demoDatabase.graphsByCourse[course.id] = { nodes: [], edges: [] }
    return jsonResponse(course)
  },
  async getCourseMaterials(courseId) { await delay(); return jsonResponse(demoDatabase.materialsByCourse[courseId] ?? []) },
  async getCourseMessages(courseId) { await delay(); return jsonResponse(demoDatabase.messagesByCourse[courseId] ?? []) },
  async getCourseGraph(courseId) { await delay(); return jsonResponse(demoDatabase.graphsByCourse[courseId] ?? { nodes: [], edges: [] }) },
  async createCourseMaterials(courseId, files) {
    return uploadCourseMaterials(courseId, files)
  },
  async deleteCourseMaterial(courseId, materialId) {
    await delay()
    const current = demoDatabase.materialsByCourse[courseId] ?? []
    const remaining = current.filter((item) => item.id !== materialId)
    demoDatabase.materialsByCourse[courseId] = remaining
    return jsonResponse({ deleted: remaining.length !== current.length })
  },
}
