import type { ExamDefinition, ExamQuestion, ExamSummary } from '../types/exams'

/** Ejemplos locales para recorrer la experiencia mientras se conecta el backend. */
const demoExams: ExamSummary[] = [
  { id: 'control-derivadas', courseId: 'calculo-i', title: 'Control: derivadas y aplicaciones', type: 'Control', topics: ['Derivadas', 'Regla de la cadena'], scheduledAt: '2026-11-10T14:00:00', durationMinutes: 45, questionCount: 12, status: 'upcoming' },
  { id: 'repaso-limites', courseId: 'calculo-i', title: 'Repaso de límites', type: 'Práctica sugerida', topics: ['Límites', 'Continuidad'], scheduledAt: '2026-11-04T00:00:00', durationMinutes: 20, questionCount: 8, status: 'available' },
  { id: 'certamen-1', courseId: 'calculo-i', title: 'Certamen 1: funciones y límites', type: 'Certamen', topics: ['Funciones', 'Límites'], scheduledAt: '2026-10-18T12:00:00', durationMinutes: 60, questionCount: 15, status: 'completed', score: 82 },
]

const demoQuestions: ExamQuestion[] = [
  { id: 'q1', type: 'alternatives', topic: 'Regla de la cadena', prompt: '¿Cuál es la derivada de $f(x)=(3x+2)^4$?', options: ['$4(3x+2)^3$', '$12(3x+2)^3$', '$12(3x+2)^4$', '$4(3x+2)^3 + 3$'], correctAnswers: [1], explanation: 'Se deriva la función exterior y se multiplica por la derivada de la interior: $4(3x+2)^3 · 3$.' },
  { id: 'q2', type: 'multiple', topic: 'Derivadas', prompt: 'Selecciona todas las reglas que permiten derivar productos o cocientes de funciones.', options: ['Regla del producto', 'Regla del cociente', 'Regla de la suma', 'Regla de L’Hôpital'], correctAnswers: [0, 1], explanation: 'Las reglas del producto y del cociente derivan directamente esas operaciones.' },
  { id: 'q3', type: 'true-false', topic: 'Continuidad', prompt: 'Si una función es derivable en un punto, entonces también es continua en ese punto.', options: ['Verdadero', 'Falso'], correctAnswers: [0], explanation: 'La derivabilidad implica continuidad, aunque la continuidad por sí sola no implica derivabilidad.' },
  { id: 'q4', type: 'development', topic: 'Aplicaciones de la derivada', prompt: 'Explica cómo usarías la primera derivada para localizar y clasificar los extremos locales de una función.', expectedAnswer: 'Se buscan los puntos críticos donde f′(x)=0 o no existe y se analiza el cambio de signo de f′ alrededor de ellos.', explanation: 'Una respuesta completa identifica los puntos críticos y explica el criterio de cambio de signo.' },
]

/**
 * Placeholder de integración: reemplazar el conjunto local por GET /courses/:id/exams.
 * Se conserva como función asíncrona para mantener estable el contrato de la UI.
 */
export async function loadCourseExams(courseId: string): Promise<ExamSummary[]> {
  return demoExams.filter((exam) => exam.courseId === courseId)
}

/** Placeholder de integración: obtener temas disponibles desde el curso y sus materiales. */
export async function loadCourseTopics(courseId: string): Promise<string[]> {
  if (courseId === 'calculo-i') return ['Funciones', 'Límites', 'Continuidad', 'Derivadas', 'Regla de la cadena']
  return []
}

/** Placeholder de integración: reemplazar por POST /courses/:id/exams/generate. */
export async function generatePersonalExam(courseId: string, input: {
  type: string
  topics: string[]
  difficulty: string
  questionCount: number
  formats: string[]
}): Promise<ExamSummary> {
  const id = `personal-${Date.now()}`
  return {
    id,
    courseId,
    title: `Práctica personal: ${input.topics[0] ?? 'repaso'}`,
    type: input.type,
    topics: input.topics,
    scheduledAt: new Date().toISOString(),
    durationMinutes: Math.max(10, input.questionCount * 3),
    questionCount: input.questionCount,
    status: 'available',
  }
}

/** Placeholder de integración: actualizar la fecha con PATCH /exams/:examId/schedule. */
export async function rescheduleExam(examId: string, scheduledAt: string): Promise<{ examId: string; scheduledAt: string }> {
  return { examId, scheduledAt }
}

/** Placeholder de integración: reemplazar por GET /courses/:id/exams/:examId. */
export async function loadExamDefinition(courseId: string, examId: string): Promise<ExamDefinition> {
  const summary = (await loadCourseExams(courseId)).find((exam) => exam.id === examId)
  return {
    id: examId,
    courseId,
    title: summary?.title ?? 'Práctica personal de Cálculo I',
    durationMinutes: summary?.durationMinutes ?? 20,
    questions: demoQuestions,
  }
}

/** Placeholder de integración: guardar respuestas y entregar el examen al backend. */
export async function submitExam(_examId: string, _answers: Record<string, number[] | string>) {
  return { submittedAt: new Date().toISOString(), received: true }
}
