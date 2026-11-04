import type { ChatMessageDto, Course, DashboardSummary, GraphologyExport, MaterialDto, StudentProfile } from '../types'

interface DemoDatabase {
  profile: StudentProfile; summary: DashboardSummary; courses: Course[]; materialsByCourse: Record<string, MaterialDto[]>
  messagesByCourse: Record<string, ChatMessageDto[]>; graphsByCourse: Record<string, GraphologyExport>
}

/** Datos de ejemplo que simulan los registros devueltos por una API JSON. */
export const demoDatabase: DemoDatabase = {
  profile: { id: 'student-001', displayName: 'Valentina Rojas', role: 'Estudiante' },
  summary: { studyHours: 4.5, studyHoursPeriod: 'esta semana', studyHoursByDay: [0.4, 0.8, 0.6, 1, 0.5, 0.7, 0.5], streakDays: 3, masteredConcepts: 12, masteredConceptsChange: 2, roadmapStep: 2, roadmapTotal: 4, nextSessionMinutes: 15, nextCourseId: 'calculo-i' },
  courses: [
    { id: 'calculo-i', name: 'Cálculo I', subject: 'Matemáticas', progress: 68, color: 'mauve', icon: '∫', nextTopic: 'Regla de la cadena', lastActivity: 'Hace 2 h' },
    { id: 'fisica', name: 'Física general', subject: 'Ciencias', progress: 42, color: 'blue', icon: '◉', nextTopic: 'Movimiento circular', lastActivity: 'Ayer' },
    { id: 'programacion', name: 'Programación', subject: 'Tecnología', progress: 31, color: 'green', icon: '{ }', nextTopic: 'Estructuras de datos', lastActivity: 'Hace 3 días' },
  ],
  materialsByCourse: {
    'calculo-i': [
      { id: 'm1', courseId: 'calculo-i', name: 'Apuntes de límites.txt', size: 248000, addedAt: '2026-10-21T12:00:00.000Z' },
      { id: 'm2', courseId: 'calculo-i', name: 'Guía de derivadas.txt', size: 156000, addedAt: '2026-10-24T12:00:00.000Z' },
    ],
    fisica: [{ id: 'm3', courseId: 'fisica', name: 'Resumen de cinemática.txt', size: 183000, addedAt: '2026-10-26T12:00:00.000Z' }],
    programacion: [],
  },
  messagesByCourse: {
    'calculo-i': [
      { id: 'welcome', role: 'assistant', createdAt: '2026-11-03T10:10:00.000Z', parts: [{ type: 'markdown', content: '¡Hola! Sigamos con **Cálculo I**. Ya revisé tus apuntes de límites y la guía de derivadas.\n\n¿Te gustaría repasar la regla de la cadena o prefieres que partamos con un ejercicio?' }] },
      { id: 'question', role: 'user', createdAt: '2026-11-03T10:12:00.000Z', parts: [{ type: 'markdown', content: '¿Me explicas cómo se relacionan la derivada y la regla de la cadena?' }] },
      { id: 'explanation', role: 'assistant', createdAt: '2026-11-03T10:12:30.000Z', parts: [
        { type: 'markdown', content: 'La **regla de la cadena** nos ayuda a derivar una función compuesta: una función dentro de otra.\n\nSi $y = f(g(x))$, derivamos la función exterior y multiplicamos por la derivada de la interior:\n\n$$\\frac{dy}{dx} = f\'(g(x)) \\cdot g\'(x)$$\n\nPor ejemplo, para $y = (3x + 2)^4$, la función exterior es $u^4$ y la interior es $3x+2$.' },
        { type: 'markdown', content: '```mermaid\nflowchart LR\n  A["3x + 2 · interior"] --> B["u⁴ · exterior"] --> C["derivar y multiplicar"]\n```' },
        { type: 'chart', chart: { title: 'Una función compuesta', description: 'y = (3x + 2)⁴', data: [{ label: '−2', fx: 16 }, { label: '−1', fx: 1 }, { label: '0', fx: 16 }, { label: '1', fx: 625 }, { label: '2', fx: 4096 }], series: [{ key: 'fx', label: 'f(x)', color: '#8839ef' }], xAxisLabel: 'x', yAxisLabel: 'f(x)' } },
        { type: 'markdown', content: '¿Quieres que resolvamos juntos un ejercicio paso a paso?' },
      ] },
    ],
    fisica: [{ id: 'fisica-welcome', role: 'assistant', createdAt: '2026-11-03T10:10:00.000Z', parts: [{ type: 'markdown', content: '¡Hola! Este es tu espacio para estudiar **Física general**. Puedes preguntarme sobre tus materiales o empezar con **Movimiento circular**.' }] }],
    programacion: [{ id: 'programacion-welcome', role: 'assistant', createdAt: '2026-11-03T10:10:00.000Z', parts: [{ type: 'markdown', content: '¡Hola! Este es tu espacio para estudiar **Programación**. Puedes revisar tus materiales o avanzar con **Estructuras de datos**.' }] }],
  },
  graphsByCourse: {
    'calculo-i': {
      nodes: [
        { key: 'funciones', attributes: { label: 'Funciones', x: .5, y: .82, size: 18, color: '#8839ef', kind: 'mastered' } },
        { key: 'limites', attributes: { label: 'Límites', x: .21, y: .55, size: 16, color: '#40a02b', kind: 'mastered' } },
        { key: 'continuidad', attributes: { label: 'Contin.', fullLabel: 'Continuidad', x: .77, y: .55, size: 14, color: '#df8e1d', kind: 'review' } },
        { key: 'derivadas', attributes: { label: 'Derivadas', x: .37, y: .2, size: 16, color: '#8839ef', kind: 'current' } },
        { key: 'cadena', attributes: { label: 'Cadena', fullLabel: 'Regla de la cadena', x: .72, y: .16, size: 12, color: '#7c7f93', kind: 'upcoming' } },
      ],
      edges: [
        { key: 'e1', source: 'funciones', target: 'limites', attributes: { color: '#ccd0da', size: 2 } },
        { key: 'e2', source: 'funciones', target: 'continuidad', attributes: { color: '#ccd0da', size: 2 } },
        { key: 'e3', source: 'limites', target: 'derivadas', attributes: { color: '#40a02b', size: 2.5 } },
        { key: 'e4', source: 'continuidad', target: 'derivadas', attributes: { color: '#ccd0da', size: 1.5 } },
        { key: 'e5', source: 'derivadas', target: 'cadena', attributes: { color: '#ccd0da', size: 2 } },
      ],
    },
    fisica: { nodes: [], edges: [] }, programacion: { nodes: [], edges: [] },
  },
}
