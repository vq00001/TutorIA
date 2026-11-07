/** Tipos de examen usados por el listado, el generador y la rendición. */
export type ExamQuestionType = 'alternatives' | 'multiple' | 'development' | 'true-false'
export type ExamStatus = 'upcoming' | 'available' | 'completed'

export interface ExamSummary {
  id: string
  courseId: string
  title: string
  type: string
  topics: string[]
  scheduledAt: string
  durationMinutes: number
  questionCount: number
  status: ExamStatus
  score?: number
}

export interface ExamQuestion {
  id: string
  type: ExamQuestionType
  topic: string
  prompt: string
  options?: string[]
  correctAnswers?: number[]
  expectedAnswer?: string
  explanation?: string
}

export interface ExamDefinition {
  id: string
  courseId: string
  title: string
  durationMinutes: number
  questions: ExamQuestion[]
}
