import type { ChartPayload } from './charts'

export * from './charts'
export * from './chat.ts'

export type ThemePreference = 'system' | 'latte' | 'mocha'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>


export const compatible_file_formats = '.txt,.md,.pdf'
export const supported_file_extensions = ['.txt', '.md', '.pdf'] as const

export function isSupportedFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return supported_file_extensions.some((extension) => name.endsWith(extension))
}

export interface DashboardSummary {
  studyHours: number
  studyHoursPeriod: string
  studyHoursByDay: number[]
  streakDays: number
  masteredConcepts: number
  masteredConceptsChange: number
  roadmapStep: number
  roadmapTotal: number
  nextSessionMinutes: number
  nextCourseId: string
}

export interface Course {
  id: string;
  myRole?: 'coordinator' | 'student';
  name: string;
  description?: string;
  objectives?: string;
  subject: string;
  progress: number;
  color: string;
  icon: string;
  nextTopic: string;
  lastActivity: string
}

export interface NewCourse {
  name: string;
  description: string;
  objectives: string;
}

export interface StudentProfile {
  id: string;
  displayName: string;
  role: string
}

export interface MaterialFile {
  id: string
  courseId: string
  name: string
  size: number
  addedAt: Date
  status?: 'queued' | 'processing' | 'ready' | 'failed'
}

export type MaterialDto = Omit<MaterialFile, 'addedAt'> & {
  addedAt: string
}

export interface JsonApiResponse<T> {
  data: T
}

export interface GraphologyNodeExport {
  key: string
  attributes?: Record<string, unknown>
}

export interface GraphologyEdgeExport {
  key?: string
  source: string
  target: string
  attributes?: Record<string, unknown>
}

export interface GraphologyExport {
  attributes?: Record<string, unknown>
  nodes: Array<
    GraphologyNodeExport | [string, Record<string, unknown>?]
  >
  edges: Array<
    GraphologyEdgeExport | [string, string, string, Record<string, unknown>?]
  >
}

