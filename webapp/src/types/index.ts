import type { ChartPayload } from './charts'

export * from './charts'

export type ThemePreference = 'system' | 'latte' | 'mocha'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>


// Tipos de archivos soportados por el sistema
export const compatible_file_formats = ".txt,text/plain,.md,text/markdown"

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
}

export type MaterialDto = Omit<MaterialFile, 'addedAt'> & {
  addedAt: string
}


export type MessagePart =
  | { type: 'markdown'; content: string }
  | { type: 'chart'; chart: ChartPayload }


export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  parts: MessagePart[]
  createdAt: Date
  awaitingResponse?: boolean
}

export type ChatMessageDto = Omit<ChatMessage, 'createdAt'> & {
  createdAt: string
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


