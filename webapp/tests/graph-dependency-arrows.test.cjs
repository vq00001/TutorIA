const assert = require('node:assert/strict')
const { readFileSync, existsSync, statSync } = require('node:fs')
const path = require('node:path')
const { test } = require('node:test')
const { runInNewContext } = require('node:vm')
const ts = require('typescript')
const Graph = require('graphology')

// Sigma consulta estas constantes al importar sus clases de programas.
// No se crea un contexto WebGL ni se ejecuta código de dibujo.
const webGLNames = ['WebGLRenderingContext', 'WebGL2RenderingContext']
const previousWebGL = new Map(webGLNames.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
let sigmaSettings
let sigmaRendering
try {
  for (const name of webGLNames) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value: {
        FLOAT: 0x1406, UNSIGNED_BYTE: 0x1401, UNSIGNED_SHORT: 0x1403,
        TRIANGLES: 0x0004, LINES: 0x0001, POINTS: 0x0000,
      },
    })
  }
  sigmaSettings = require('sigma/settings')
  sigmaRendering = require('sigma/rendering')
} finally {
  for (const name of webGLNames) {
    const descriptor = previousWebGL.get(name)
    if (descriptor) Object.defineProperty(globalThis, name, descriptor)
    else delete globalThis[name]
  }
}

const sourceRoot = path.resolve(__dirname, '../src')
const componentPath = path.join(sourceRoot, 'components/graph/KnowledgeGraph.tsx')

function graphData() {
  return {
    nodes: [
      { key: 'limites', attributes: { label: 'Límites' } },
      { key: 'derivadas', attributes: { label: 'Derivadas' } },
    ],
    // Convención del backend: source es el prerrequisito, target el dependiente.
    edges: [{ key: 'prerrequisito', source: 'limites', target: 'derivadas' }],
  }
}

function mountGraph(data, context) {
  const container = {}
  const effects = []
  const renderers = []
  let error = ''
  let cleanup
  class SigmaRecorder {
    constructor(graph, target, settings) {
      this.graph = graph
      this.container = target
      this.settings = sigmaSettings.resolveSettings(settings)
      sigmaSettings.validateSettings(this.settings)
      this.killCalls = 0
      renderers.push(this)
    }
    kill() { this.killCalls++ }
  }
  const element = (type, props) => ({ type, props })
  const mocks = {
    react: {
      useRef: () => ({ current: container }),
      useState: () => [error, (value) => { error = value }],
      useEffect: (effect) => effects.push(effect),
    },
    'react/jsx-runtime': { jsx: element, jsxs: element },
    graphology: Graph,
    sigma: SigmaRecorder,
    'sigma/rendering': sigmaRendering,
    'sigma/settings': sigmaSettings,
  }
  const modules = new Map()
  function loadModule(filename) {
    if (modules.has(filename)) return modules.get(filename).exports
    const compiled = ts.transpileModule(readFileSync(filename, 'utf8'), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
      fileName: filename,
    })
    const module = { exports: {} }
    modules.set(filename, module)
    runInNewContext(compiled.outputText, {
      module, exports: module.exports, Error,
      document: { documentElement: {} },
      getComputedStyle: () => ({ getPropertyValue: () => '#4c4f69' }),
      require(specifier) {
        if (specifier in mocks) return mocks[specifier]
        assert.ok(specifier.startsWith('.'), 'Importación no prevista por el test')
        const base = path.resolve(path.dirname(filename), specifier)
        const resolved = [base, base + '.ts', base + '.tsx', path.join(base, 'index.ts')]
          .find((candidate) => existsSync(candidate) && statSync(candidate).isFile())
        assert.ok(resolved, 'Módulo local no encontrado: ' + specifier)
        return loadModule(resolved)
      },
    }, { filename })
    return module.exports
  }

  const { KnowledgeGraph } = loadModule(componentPath)
  KnowledgeGraph({ data })
  assert.equal(effects.length, 1)
  cleanup = effects[0]()
  function unmount() {
    const dispose = cleanup
    cleanup = undefined
    dispose?.()
  }
  context.after(unmount)
  assert.equal(error, '', 'El componente debe configurar el grafo sin errores')
  assert.equal(renderers.length, 1)
  const renderer = renderers[0]
  assert.equal(renderer.container, container)
  return { graph: renderer.graph, settings: renderer.settings, renderer, unmount }
}

function assertArrowPrograms(graph, settings) {
  assert.ok(graph.size > 0, 'El caso debe contener dependencias para evaluar')
  graph.forEachEdge((key, attributes) => {
    // Sigma aplica edgeReducer antes de completar el tipo con defaultEdgeType.
    const display = settings.edgeReducer ? settings.edgeReducer(key, { ...attributes }) : attributes
    const type = display.type || settings.defaultEdgeType
    assert.equal(
      settings.edgeProgramClasses[type],
      sigmaRendering.EdgeArrowProgram,
      'La dependencia ' + key + ' debe usar el programa de flecha hacia target; tipo efectivo: ' + type,
    )
  })
}

test('conserva los nodos y la dirección prerrequisito a dependiente', (context) => {
  const { graph } = mountGraph(graphData(), context)
  assert.equal(graph.order, 2)
  assert.equal(graph.size, 1)
  assert.equal(graph.type, 'directed')
  assert.equal(graph.isDirected('prerrequisito'), true)
  assert.equal(graph.source('prerrequisito'), 'limites')
  assert.equal(graph.target('prerrequisito'), 'derivadas')
  assert.equal(graph.getNodeAttribute('limites', 'label'), 'Límites')
})

test('conserva coordenadas, colores y grosor importados', (context) => {
  const data = graphData()
  data.nodes[0].attributes = { label: 'Límites', x: 10, y: 20, size: 8, color: '#123456' }
  data.edges[0].attributes = { size: 3, color: '#654321' }
  const { graph } = mountGraph(data, context)
  assert.equal(graph.getNodeAttribute('limites', 'x'), 10)
  assert.equal(graph.getNodeAttribute('limites', 'y'), 20)
  assert.equal(graph.getNodeAttribute('limites', 'size'), 8)
  assert.equal(graph.getNodeAttribute('limites', 'color'), '#123456')
  assert.equal(graph.getEdgeAttribute('prerrequisito', 'size'), 3)
  assert.equal(graph.getEdgeAttribute('prerrequisito', 'color'), '#654321')
})

test('admite tuplas sin invertir sus extremos', (context) => {
  const { graph } = mountGraph({
    nodes: [['limites', { label: 'Límites' }], ['derivadas', { label: 'Derivadas' }]],
    edges: [['relacion-tupla', 'limites', 'derivadas']],
  }, context)
  assert.equal(graph.source('relacion-tupla'), 'limites')
  assert.equal(graph.target('relacion-tupla'), 'derivadas')
})

test('omite relaciones con nodos ausentes y conserva las válidas', (context) => {
  const data = graphData()
  data.edges.push({ key: 'obsoleta', source: 'limites', target: 'ausente' })
  const { graph } = mountGraph(data, context)
  assert.equal(graph.size, 1)
  assert.equal(graph.hasEdge('prerrequisito'), true)
})

test('la configuración no modifica los datos de entrada', (context) => {
  const data = graphData()
  const original = JSON.stringify(data)
  const { graph } = mountGraph(data, context)
  for (const node of graph.nodes()) {
    assert.ok(Number.isFinite(graph.getNodeAttribute(node, 'x')))
    assert.ok(Number.isFinite(graph.getNodeAttribute(node, 'y')))
  }
  assert.equal(JSON.stringify(data), original)
})

test('libera el renderizador al desmontar', (context) => {
  const { renderer, unmount } = mountGraph(graphData(), context)
  assert.equal(renderer.killCalls, 0)
  unmount()
  assert.equal(renderer.killCalls, 1)
})

test('permite configurar un grafo sin dependencias', (context) => {
  const { graph } = mountGraph({ nodes: [{ key: 'aislado' }], edges: [] }, context)
  assert.equal(graph.order, 1)
  assert.equal(graph.size, 0)
})

const arrowCases = [
  ['objetos de la API', () => graphData()],
  ['tuplas', () => ({
    nodes: [['limites'], ['derivadas']],
    edges: [['relacion-tupla', 'limites', 'derivadas']],
  })],
  ['formatos mezclados', () => ({
    nodes: [{ key: 'limites' }, ['derivadas'], { key: 'integrales' }],
    edges: [
      { key: 'relacion-objeto', source: 'limites', target: 'derivadas' },
      ['relacion-tupla', 'derivadas', 'integrales'],
    ],
  })],
  ['relaciones importadas con tipo line', () => {
    const data = graphData()
    data.edges[0].attributes = { type: 'line', size: 3, color: '#654321' }
    return data
  }],
  ['cadena de dependencias', () => ({
    nodes: [{ key: 'limites' }, { key: 'derivadas' }, { key: 'integrales' }],
    edges: [
      { key: 'primera', source: 'limites', target: 'derivadas' },
      { key: 'segunda', source: 'derivadas', target: 'integrales' },
    ],
  })],
]

for (const [name, createData] of arrowCases) {
  test('muestra flechas de dependencia para ' + name, (context) => {
    const data = createData()
    const { graph, settings } = mountGraph(data, context)
    assertArrowPrograms(graph, settings)
    for (const edge of data.edges) {
      const key = Array.isArray(edge) ? edge[0] : edge.key
      const source = Array.isArray(edge) ? edge[1] : edge.source
      const target = Array.isArray(edge) ? edge[2] : edge.target
      assert.equal(graph.source(key), source)
      assert.equal(graph.target(key), target)
    }
  })
}
