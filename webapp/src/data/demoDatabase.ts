import type {
  ChatMessageDto,
  ChartPayload,
  Course,
  DashboardSummary,
  GraphologyExport,
  MaterialDto,
  StudentProfile,
} from '../types'

interface DemoDatabase {
  profile: StudentProfile
  summary: DashboardSummary
  courses: Course[]
  materialsByCourse: Record<string, MaterialDto[]>
  messagesByCourse: Record<string, ChatMessageDto[]>
  graphsByCourse: Record<string, GraphologyExport>
}

/** Datos de ejemplo que simulan los registros devueltos por una API JSON. */
export const demoDatabase: DemoDatabase = {
  profile: {
    id: 'student-001',
    displayName: 'Valentina Rojas',
    role: 'Estudiante',
  },

  summary: {
    studyHours: 4.5,
    studyHoursPeriod: 'esta semana',
    studyHoursByDay: [0.4, 0.8, 0.6, 1, 0.5, 0.7, 0.5],
    streakDays: 3,
    masteredConcepts: 12,
    masteredConceptsChange: 2,
    roadmapStep: 2,
    roadmapTotal: 4,
    nextSessionMinutes: 15,
    nextCourseId: 'calculo-i',
  },

  courses: [
    {
      id: 'calculo-i',
      name: 'Cálculo I',
      subject: 'Matemáticas',
      progress: 68,
      color: 'mauve',
      icon: '∫',
      nextTopic: 'Regla de la cadena',
      lastActivity: 'Hace 2 h',
    },
    {
      id: 'fisica',
      name: 'Física general',
      subject: 'Ciencias',
      progress: 42,
      color: 'blue',
      icon: '◉',
      nextTopic: 'Movimiento circular',
      lastActivity: 'Ayer',
    },
    {
      id: 'programacion',
      name: 'Programación',
      subject: 'Tecnología',
      progress: 31,
      color: 'green',
      icon: '{ }',
      nextTopic: 'Estructuras de datos',
      lastActivity: 'Hace 3 días',
    },
  ],

  materialsByCourse: {
    'calculo-i': [
      {
        id: 'm1',
        courseId: 'calculo-i',
        name: 'Apuntes de límites.txt',
        size: 248000,
        addedAt: '2026-10-21T12:00:00.000Z',
      },
      {
        id: 'm2',
        courseId: 'calculo-i',
        name: 'Guía de derivadas.txt',
        size: 156000,
        addedAt: '2026-10-24T12:00:00.000Z',
      },
    ],

    fisica: [
      {
        id: 'm3',
        courseId: 'fisica',
        name: 'Resumen de cinemática.txt',
        size: 183000,
        addedAt: '2026-10-26T12:00:00.000Z',
      },
    ],

    programacion: [],
  },

  messagesByCourse: {
    'calculo-i': [
      {
        id: 'welcome',
        role: 'assistant',
        createdAt: '2026-11-03T10:10:00.000Z',
        parts: [
          {
            type: 'markdown',
            content:
              '¡Hola! Sigamos con **Cálculo I**. Ya revisé tus apuntes de límites y la guía de derivadas.\n\n¿Te gustaría repasar la regla de la cadena o prefieres que partamos con un ejercicio?',
          },
        ],
      },
      {
        id: 'question',
        role: 'user',
        createdAt: '2026-11-03T10:12:00.000Z',
        parts: [
          {
            type: 'markdown',
            content:
              '¿Me explicas cómo se relacionan la derivada y la regla de la cadena?',
          },
        ],
      },
      {
        id: 'explanation',
        role: 'assistant',
        createdAt: '2026-11-03T10:12:30.000Z',
        parts: [
          {
            type: 'markdown',
            content:
              'La **regla de la cadena** nos ayuda a derivar una función compuesta: una función dentro de otra.\n\nSi $y = f(g(x))$, derivamos la función exterior y multiplicamos por la derivada de la interior:\n\n$$\\frac{dy}{dx} = f\'(g(x)) \\cdot g\'(x)$$\n\nPor ejemplo, para $y = (3x + 2)^4$, la función exterior es $u^4$ y la interior es $3x+2$.',
          },
          {
            type: 'markdown',
            content:
              '```mermaid\nflowchart LR\n  A["3x + 2 · interior"] --> B["u⁴ · exterior"] --> C["derivar y multiplicar"]\n```',
          },
          {
            type: 'chart',
            chart: {
              title: 'Una función compuesta',
              description: 'f(x) = (3x + 2)⁴',
              type: 'line',
              data: [
                { label: '-2', fx: 16 },
                { label: '-1', fx: 1 },
                { label: '0', fx: 16 },
                { label: '1', fx: 625 },
                { label: '2', fx: 4096 },
              ],
              series: [
                {
                  key: 'fx',
                  label: 'f(x)',
                  color: '#8839ef',
                },
              ],
              xAxisLabel: 'x',
              yAxisLabel: 'f(x)',
            },
          },
          {
            type: 'chart',
            chart: {
              title: 'Derivada de la función',
              description: "f'(x) = 12(3x + 2)³",
              type: 'bar',
              data: [
                { label: '-1', derivative: -12 },
                { label: '-0.5', derivative: -1.5 },
                { label: '0', derivative: 96 },
                { label: '0.5', derivative: 750 },
                { label: '1', derivative: 4116 },
              ],
              series: [
                {
                  key: 'derivative',
                  label: "f'(x)",
                  color: '#1e9e85',
                },
              ],
              xAxisLabel: 'x',
              yAxisLabel: "f'(x)",
            },
          },
          {
            type: 'chart',
            chart: {
              title: 'Función y derivada',
              description: 'Comparación entre f(x) y f\'(x)',
              type: 'area',
              data: [
                { label: '-2', fx: 16, derivative: -192 },
                { label: '-1', fx: 1, derivative: -12 },
                { label: '0', fx: 16, derivative: 96 },
                { label: '1', fx: 625, derivative: 1500 },
                { label: '2', fx: 4096, derivative: 3072 },
              ],
              series: [
                {
                  key: 'fx',
                  label: 'f(x)',
                  color: '#8839ef',
                },
                {
                  key: 'derivative',
                  label: "f'(x)",
                  color: '#e67e22',
                },
              ],
              xAxisLabel: 'x',
              yAxisLabel: 'Valor',
            },
          },
          ...[
            { title: 'Nube de puntos', type: 'scatter', data: [{ label: 'A', x: 1, y: 2 }, { label: 'B', x: 2, y: 4 }, { label: 'C', x: 3, y: 3 }], series: [{ key: 'y', label: 'f(x)', color: '#8839ef' }], xAxisLabel: 'x', yAxisLabel: 'f(x)' },
            { title: 'Métodos de estudio', type: 'composed', data: [{ label: 'A', ejercicios: 3, puntaje: 60 }, { label: 'B', ejercicios: 5, puntaje: 75 }, { label: 'C', ejercicios: 4, puntaje: 68 }], series: [{ key: 'ejercicios', label: 'Ejercicios', color: '#1e9e85', kind: 'bar' }, { key: 'puntaje', label: 'Puntaje', color: '#8839ef' }] },
            { title: 'Tiempo de estudio', type: 'pie', data: [{ label: 'Ejercicios', minutos: 45 }, { label: 'Lectura', minutos: 30 }, { label: 'Repaso', minutos: 25 }], nameKey: 'label', valueKey: 'minutos', colors: ['#8839ef', '#1e9e85', '#e67e22'] },
            { title: 'Habilidades de cálculo', type: 'radar', data: [{ label: 'Límites', dominio: 80, práctica: 65 }, { label: 'Derivadas', dominio: 70, práctica: 90 }, { label: 'Integrales', dominio: 55, práctica: 60 }], series: [{ key: 'dominio', label: 'Dominio', color: '#8839ef' }, { key: 'práctica', label: 'Práctica', color: '#1e9e85' }] },
            { title: 'Progreso del curso', type: 'radialBar', data: [{ label: 'Avance', progreso: 72 }], series: [{ key: 'progreso', label: 'Progreso (%)', color: '#8839ef' }] },
            { title: 'Etapas de resolución', type: 'funnel', data: [{ label: 'Propuestos', cantidad: 100 }, { label: 'Intentados', cantidad: 75 }, { label: 'Resueltos', cantidad: 52 }], nameKey: 'label', valueKey: 'cantidad', colors: ['#8839ef', '#1e9e85', '#e67e22'] },
            { title: 'Temas estudiados', type: 'treemap', data: [{ name: 'Límites', size: 30 }, { name: 'Derivadas', size: 45 }, { name: 'Integrales', size: 25 }, { name: 'Funciones', size: 35 }, { name: 'Ecuaciones', size: 20 }] },
            { title: 'Ruta de aprendizaje', type: 'sankey', data: { nodes: [{ name: 'Funciones' }, { name: 'Límites' }, { name: 'Derivadas' }, { name: 'Integrales' }], links: [{ source: 0, target: 1, value: 8 }, { source: 1, target: 2, value: 6 }, { source: 2, target: 3, value: 4 }] } },
            { title: 'Composición de contenidos', type: 'sunburst', data: { name: 'Matemáticas', value: 100, children: [{ name: 'Cálculo', value: 65, children: [{ name: 'Límites', value: 25 }, { name: 'Derivadas', value: 40 }] }, { name: 'Álgebra', value: 35, children: [{ name: 'Funciones', value: 20 }, { name: 'Ecuaciones', value: 15 }] }] } },
          ].map((chart) => ({ type: 'chart' as const, chart: { ...chart, description: 'Ejemplo de visualización para el chat' } as ChartPayload })),
          {
            type: 'markdown',
            content:
              '¿Quieres que resolvamos juntos un ejercicio paso a paso?',
          },
        ],
      },
    ],

    fisica: [
      {
        id: 'fisica-welcome',
        role: 'assistant',
        createdAt: '2026-11-03T10:10:00.000Z',
        parts: [
          {
            type: 'markdown',
            content:
              '¡Hola! Este es tu espacio para estudiar **Física general**. Puedes preguntarme sobre tus materiales o empezar con **Movimiento circular**.',
          },
        ],
      },
    ],

    programacion: [
      {
        id: 'programacion-welcome',
        role: 'assistant',
        createdAt: '2026-11-03T10:10:00.000Z',
        parts: [
          {
            type: 'markdown',
            content:
              '¡Hola! Este es tu espacio para estudiar **Programación**. Puedes revisar tus materiales o avanzar con **Estructuras de datos**.',
          },
        ],
      },
    ],
  },

  graphsByCourse: {
    'calculo-i': {
      nodes: [
        {
          key: 'funciones',
          attributes: {
            label: 'Funciones',
            x: 0.5,
            y: 0.82,
            size: 18,
            color: '#8839ef',
            kind: 'mastered',
          },
        },
        {
          key: 'limites',
          attributes: {
            label: 'Límites',
            x: 0.21,
            y: 0.55,
            size: 16,
            color: '#40a02b',
            kind: 'mastered',
          },
        },
        {
          key: 'continuidad',
          attributes: {
            label: 'Contin.',
            fullLabel: 'Continuidad',
            x: 0.77,
            y: 0.55,
            size: 14,
            color: '#df8e1d',
            kind: 'review',
          },
        },
        {
          key: 'derivadas',
          attributes: {
            label: 'Derivadas',
            x: 0.37,
            y: 0.2,
            size: 16,
            color: '#8839ef',
            kind: 'current',
          },
        },
        {
          key: 'cadena',
          attributes: {
            label: 'Cadena',
            fullLabel: 'Regla de la cadena',
            x: 0.72,
            y: 0.16,
            size: 12,
            color: '#7c7f93',
            kind: 'upcoming',
          },
        },
      ],

      edges: [
        {
          key: 'e1',
          source: 'funciones',
          target: 'limites',
          attributes: {
            color: '#ccd0da',
            size: 2,
          },
        },
        {
          key: 'e2',
          source: 'funciones',
          target: 'continuidad',
          attributes: {
            color: '#ccd0da',
            size: 2,
          },
        },
        {
          key: 'e3',
          source: 'limites',
          target: 'derivadas',
          attributes: {
            color: '#40a02b',
            size: 2.5,
          },
        },
        {
          key: 'e4',
          source: 'continuidad',
          target: 'derivadas',
          attributes: {
            color: '#ccd0da',
            size: 1.5,
          },
        },
        {
          key: 'e5',
          source: 'derivadas',
          target: 'cadena',
          attributes: {
            color: '#ccd0da',
            size: 2,
          },
        },
      ],
    },

    fisica: {
      nodes: [],
      edges: [],
    },

    programacion: {
      nodes: [],
      edges: [],
    },
  },
}
