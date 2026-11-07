export type ThemePreference = 'system' | 'latte' | 'mocha'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>


// Tipos de archivos soportados por el sistema
export const compatible_file_formats = ".txt,text/plain,.md,text/markdown"

export interface Course {
  rowVersion?: number;
  myRole?: 'coordinator' | 'student';
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
  nickname?: string;
  email?: string;
  timezone?: string;
  rowVersion?: number;
  status?: string;
  id: string;
  displayName: string;
  role: string
}

export interface DashboardSummary {
  studyHours: number; studyHoursPeriod: string; studyHoursByDay: number[]; streakDays: number; masteredConcepts: number
  masteredConceptsChange: number; roadmapStep: number; roadmapTotal: number; nextSessionMinutes: number; nextCourseId: string
}
export interface MaterialFile { id: string; courseId: string; name: string; size: number; addedAt: Date; status?:string; errorCode?:string|null; activeRunId?:string|null; versionId?:string; rowVersion?:number }
export type MaterialDto = Omit<MaterialFile, 'addedAt'> & { addedAt: string }
export interface ChartPoint { label: string;[series: string]: string | number }
export interface ChartSeries { key: string; label: string; color: string }
export interface ChartPayload { title: string; description?: string; data: ChartPoint[]; series: ChartSeries[]; xAxisLabel?: string; yAxisLabel?: string }
export type MessagePart = { type: 'markdown'; content: string } | { type: 'chart'; chart: ChartPayload }
export interface SourceDocument { id:string; name?:string; documentName?:string|null; documentId?:string|null; contentUrl?:string|null }
export interface ChatMessage { id: string; role: 'user' | 'assistant'; parts: MessagePart[]; createdAt: Date; awaitingResponse?: boolean; usesDocuments?:boolean; usedDocuments?:SourceDocument[]; citations?: Array<{id:string;label:string;status:string;documentName?:string|null;contentUrl?:string|null}> }
export type ChatMessageDto = Omit<ChatMessage, 'createdAt'> & { createdAt: string }
export interface JsonApiResponse<T> { data: T }
export interface GraphologyNodeExport { key: string; attributes?: Record<string, unknown> }
export interface GraphologyEdgeExport { key?: string; source: string; target: string; attributes?: Record<string, unknown> }
export interface GraphologyExport {
  attributes?: Record<string, unknown>
  nodes: Array<GraphologyNodeExport | [string, Record<string, unknown>?]>
  edges: Array<GraphologyEdgeExport | [string, string, string, Record<string, unknown>?]>
}
