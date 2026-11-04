import { demoDatabase } from '../data/demoDatabase'
import type { ChatMessageDto, Course, DashboardSummary, GraphologyExport, JsonApiResponse, MaterialDto, NewCourse, StudentProfile } from '../types'

/** Adapter boundary: the in-memory implementation can be replaced by fetch/database services. */
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

function jsonResponse<T>(data: T): JsonApiResponse<T> { return JSON.parse(JSON.stringify({ data })) as JsonApiResponse<T> }
const delay = () => new Promise<void>((resolve) => window.setTimeout(resolve, 80))

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
    await delay()
    const created = files.filter((file) => file.name.toLowerCase().endsWith('.txt')).map((file): MaterialDto => ({ id: crypto.randomUUID(), courseId, name: file.name, size: file.size, addedAt: new Date().toISOString() }))
    const materials = demoDatabase.materialsByCourse[courseId] ?? (demoDatabase.materialsByCourse[courseId] = [])
    materials.unshift(...created)
    return jsonResponse(created)
  },
  async deleteCourseMaterial(courseId, materialId) {
    await delay()
    const current = demoDatabase.materialsByCourse[courseId] ?? []
    const remaining = current.filter((item) => item.id !== materialId)
    demoDatabase.materialsByCourse[courseId] = remaining
    return jsonResponse({ deleted: remaining.length !== current.length })
  },
}
