import type { ChatMessageDto, Course, DashboardSummary, GraphologyExport, JsonApiResponse, MaterialDto, NewCourse, StudentProfile } from '../types'
import { allPages, request } from './http'

export interface TutorIADataApi {
  getStudentProfile(): Promise<JsonApiResponse<StudentProfile>>
  getDashboardSummary(): Promise<JsonApiResponse<DashboardSummary>>
  getCourses(): Promise<JsonApiResponse<Course[]>>
  createCourse(course: NewCourse): Promise<JsonApiResponse<Course>>
  getCourseMaterials(courseId: string): Promise<JsonApiResponse<MaterialDto[]>>
  getCourseMessages(courseId: string): Promise<JsonApiResponse<ChatMessageDto[]>>
  sendMessage(courseId: string, content: string): Promise<JsonApiResponse<ChatMessageDto[]>>
  getCourseGraph(courseId: string): Promise<JsonApiResponse<GraphologyExport>>
  createCourseMaterials(courseId: string, files: File[]): Promise<JsonApiResponse<MaterialDto[]>>
  deleteCourseMaterial(courseId: string, materialId: string): Promise<JsonApiResponse<{ deleted: boolean }>>
}
const conversations = new Map<string, string>()
async function conversation(courseId: string, create = false) {
  if (conversations.has(courseId)) return conversations.get(courseId)
  const { data } = await request<{ data: Array<{ id: string }> }>(`/courses/${courseId}/conversations`)
  let id = data[0]?.id
  if (!id && create) id = (await request<{data:{id:string}}>(`/courses/${courseId}/conversations`, { method: 'POST', body: JSON.stringify({ title: 'Tutor del curso' }) })).data.id
  if (id) conversations.set(courseId, id)
  return id
}

export const databaseApi: TutorIADataApi = {
  getStudentProfile: () => request('/student/me'),
  getDashboardSummary: () => request('/dashboard/summary'),
  getCourses: () => allPages('/courses'),
  createCourse: input => request('/courses', { method: 'POST', body: JSON.stringify(input) }),
  getCourseMaterials: id => allPages(`/courses/${id}/materials`),
  getCourseGraph: id => request(`/courses/${id}/knowledge-graph`),
  async getCourseMessages(id) {
    const tid = await conversation(id)
    if (!tid) return { data: [] }
    let after = 0
    const data: ChatMessageDto[] = []
    while (true) {
      const result = await request<{data: Array<ChatMessageDto & {sequenceNo:number}>}>(`/courses/${id}/conversations/${tid}/messages?after=${after}`)
      data.push(...result.data)
      if (result.data.length < 100) break
      after = result.data[result.data.length - 1].sequenceNo
    }
    return { data }
  },
  async sendMessage(id, content) {
    const tid = await conversation(id, true)
    const result = await request<{data:{generationId:string}}>(`/courses/${id}/conversations/${tid}/messages`, { method: 'POST', body: JSON.stringify({ content }) })
    for (let i = 0; i < 120; i++) {
      const status = await request<{data:{status:string;error_code?:string}}>(`/generations/${result.data.generationId}`)
      if (status.data.status === 'completed') return this.getCourseMessages(id)
      if (['failed', 'cancelled', 'invalidated'].includes(status.data.status)) throw new Error(status.data.error_code ?? 'La respuesta no pudo completarse.')
      await new Promise(resolve => setTimeout(resolve, 500))
    }
    throw new Error('La respuesta sigue procesándose. Vuelve a abrir el curso para consultar su estado.')
  },
  async createCourseMaterials(id, files) {
    const body = new FormData()
    for (const file of files) body.append('files', file, file.name)
    const result = await request<{data:{results:Array<{data?:unknown;error?:{code:string}}>}}>(`/courses/${id}/materials`, { method: 'POST', body })
    const errors = result.data.results.filter(r => r.error)
    if (errors.length) throw new Error(errors.map(r => r.error?.code).join(', '))
    return this.getCourseMaterials(id)
  },
  async deleteCourseMaterial(id, mid) {
    await request(`/courses/${id}/materials/${mid}`, { method: 'DELETE' })
    return { data: { deleted: true } }
  },
}
