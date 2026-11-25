const assert = require('node:assert/strict')
const { readFileSync, existsSync, statSync } = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { test } = require('node:test')
const { runInNewContext } = require('node:vm')
const ts = require('typescript')

const sourceRoot = path.resolve(__dirname, '../src')
const EXISTING_COURSE = '00000000-0000-4000-8000-000000000001'
const NEW_COURSE = '00000000-0000-4000-8000-000000000101'
const flush = () => new Promise((resolve) => setImmediate(resolve))

// Estado y efectos mínimos para ejecutar los callbacks originales de React.
// La prueba no renderiza DOM ni sustituye la lógica de creación de la página.
function hooks() {
  const states = []
  const effects = []
  let stateIndex = 0
  let effectIndex = 0
  return {
    begin() { stateIndex = 0; effectIndex = 0 },
    useState(initial) {
      const index = stateIndex++
      if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial
      return [states[index], (value) => {
        states[index] = typeof value === 'function' ? value(states[index]) : value
      }]
    },
    useEffect(callback) {
      const index = effectIndex++
      if (!(index in effects)) {
        effects[index] = true
        callback()
      }
    },
  }
}

function find(element, predicate) {
  if (!element || typeof element !== 'object') return undefined
  if (predicate(element)) return element
  for (const child of [element.props?.children].flat(Infinity)) {
    const match = find(child, predicate)
    if (match) return match
  }
}

function serverFixture(outcome) {
  const existing = {
    id: EXISTING_COURSE, name: 'Curso anterior', description: 'Conservar',
    objectives: 'Conservar', myRole: 'coordinator',
  }
  const existingMaterial = {
    id: 'documento-anterior', courseId: EXISTING_COURSE, name: 'anterior.txt',
    size: 10, addedAt: '2026-11-25T12:00:00Z', status: 'ready',
  }
  const courses = new Map([[EXISTING_COURSE, existing]])
  const materials = new Map([[EXISTING_COURSE, [existingMaterial]]])
  const calls = []
  let created = 0
  const api = {
    getStudentProfile: async () => ({ data: { id: 'usuario-test', displayName: 'Test', role: 'member' } }),
    getDashboardSummary: async () => ({ data: {} }),
    getCourses: async () => ({ data: [...courses.values()] }),
    getCourseMaterials: async (id) => ({
      data: (materials.get(id) ?? []).map((material) => ({ ...material })),
    }),
    getCourseMessages: async () => ({ data: [] }),
    getCourseGraph: async () => ({ data: { nodes: [], edges: [] } }),
    async createCourse(input) {
      calls.push('createCourse')
      if (outcome === 'course_rejected') throw new Error('Creación rechazada')
      const id = '00000000-0000-4000-8000-' + String(101 + created++).padStart(12, '0')
      const course = { ...input, id, myRole: 'coordinator' }
      courses.set(id, course)
      materials.set(id, [])
      return { data: course }
    },
    async createCourseMaterials(id, files) {
      calls.push('createCourseMaterials')
      assert.ok(courses.has(id), 'La carga debe pertenecer a un curso existente')
      if (outcome === 'upload_rejected') throw new Error('Carga rechazada por la API')
      const documents = files.map((file, index) => ({
        id: id + '-document-' + index,
        courseId: id, name: file.name, size: file.size,
        addedAt: '2026-11-25T12:00:00Z',
        status: outcome === 'processing_failed' || (outcome === 'mixed' && index === 1)
          ? 'failed' : outcome === 'queued' ? 'queued' : 'ready',
      }))
      materials.set(id, documents)
      return { data: documents.map((document) => ({ ...document })) }
    },
    async deleteCourseMaterial(courseId, materialId) {
      materials.set(courseId, (materials.get(courseId) ?? []).filter((item) => item.id !== materialId))
      return { data: { deleted: true } }
    },
  }
  return {
    api, courses, materials, calls,
    existing: { ...existing },
    existingMaterial: { ...existingMaterial },
    completeProcessing(status) {
      for (const [id, documents] of materials) {
        if (id !== EXISTING_COURSE) {
          materials.set(id, documents.map((document) => ({ ...document, status })))
        }
      }
    },
  }
}

async function fixture(outcome = 'ready') {
  const server = serverFixture(outcome)
  const providerHooks = hooks()
  const pageHooks = hooks()
  const navigations = []
  let activeHooks
  const react = {
    createContext(initial) {
      const context = { current: initial }
      context.Provider = (props) => { context.current = props.value; return props.children }
      return context
    },
    useContext: (context) => context.current,
    useState: (initial) => activeHooks.useState(initial),
    useEffect: (callback) => activeHooks.useEffect(callback),
    useCallback: (callback) => callback,
    useMemo: (factory) => factory(),
  }
  const element = (type, props) => ({ type, props: props ?? {} })
  const MaterialDropzone = () => null
  const MaterialList = () => null
  const modules = new Map()
  const mocks = {
    react,
    'react/jsx-runtime': { jsx: element, jsxs: element, Fragment: Symbol('Fragment') },
    'react-router-dom': { Link: () => null, useNavigate: () => (to) => navigations.push(to) },
  }
  function loadModule(filename) {
    if (modules.has(filename)) return modules.get(filename).exports
    const compiled = ts.transpileModule(readFileSync(filename, 'utf8'), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
        jsx: ts.JsxEmit.ReactJSX,
      },
      fileName: filename,
    })
    const module = { exports: {} }
    modules.set(filename, module)
    runInNewContext(compiled.outputText, {
      module, exports: module.exports, crypto, Date, Error, File, Headers, URLSearchParams,
      setTimeout, clearTimeout, setInterval, clearInterval,
      fetch: () => assert.fail('El test intentó realizar HTTP sin simular'),
      require(specifier) {
        if (specifier in mocks) return mocks[specifier]
        assert.ok(specifier.startsWith('.'), 'Importación no prevista por el test')
        const base = path.resolve(path.dirname(filename), specifier)
        const resolved = [base, base + '.ts', base + '.tsx', path.join(base, 'index.ts')]
          .find((candidate) => existsSync(candidate) && statSync(candidate).isFile())
        assert.ok(resolved, 'Módulo local no encontrado: ' + specifier)
        if (resolved.endsWith('/components/materials/MaterialDropzone.tsx')) return { MaterialDropzone }
        if (resolved.endsWith('/components/materials/MaterialList.tsx')) return { MaterialList }
        if (resolved.endsWith('/components/ui/PageHeading.tsx')) return { PageHeading: () => null }
        return loadModule(resolved)
      },
    }, { filename })
    return module.exports
  }

  const { AppDataProvider, useAppData } = loadModule(path.join(sourceRoot, 'contexts/AppDataContext.tsx'))
  const { CreateCoursePage } = loadModule(path.join(sourceRoot, 'pages/CreateCoursePage.tsx'))
  function render() {
    activeHooks = providerHooks
    providerHooks.begin()
    const provider = AppDataProvider({ api: server.api, children: null })
    provider.type(provider.props)
    activeHooks = pageHooks
    pageHooks.begin()
    return CreateCoursePage()
  }

  render()
  await flush()
  render()

  return {
    server, navigations,
    setName(name) {
      find(render(), (node) => node.type === 'input' && node.props.required)
        .props.onChange({ target: { value: name } })
    },
    selectFiles(files) {
      find(render(), (node) => node.type === MaterialDropzone).props.onFiles(files)
    },
    submit() {
      return find(render(), (node) => node.type === 'form').props.onSubmit({ preventDefault() {} })
    },
    view() {
      const tree = render()
      const state = useAppData()
      const notice = find(tree, (node) => node.props.role === 'status')
      const button = find(tree, (node) => node.type === 'button' && node.props.type === 'submit')
      return { courses: state.courses, notice: notice?.props.children ?? '', busy: button.props.disabled }
    },
  }
}

const file = (name = 'guia.txt') => new File(['Texto sintético de prueba.'], name, { type: 'text/plain' })

function assertNoNewCourse(flow) {
  // Verifica tanto el estado confirmado por la API simulada como el contexto.
  assert.deepEqual([...flow.server.courses.keys()], [EXISTING_COURSE])
  assert.deepEqual(Array.from(flow.view().courses, (course) => course.id), [EXISTING_COURSE])
}

test('un nombre vacío no crea un curso ni carga archivos', async () => {
  const flow = await fixture()
  flow.setName('   ')
  flow.selectFiles([file()])
  await flow.submit()

  assertNoNewCourse(flow)
  assert.equal(flow.server.calls.length, 0)
  assert.equal(flow.navigations.length, 0)
  assert.ok(flow.view().notice)
})

test('permite crear un curso sin materiales', async () => {
  const flow = await fixture()
  flow.setName('Curso sin materiales')
  await flow.submit()

  assert.deepEqual([...flow.server.courses.keys()], [EXISTING_COURSE, NEW_COURSE])
  assert.deepEqual(Array.from(flow.view().courses, (course) => course.id), [NEW_COURSE, EXISTING_COURSE])
  assert.deepEqual(flow.server.calls, ['createCourse'])
  assert.deepEqual(flow.navigations, ['/cursos/' + NEW_COURSE])
  assert.equal(flow.view().busy, false)
})

test('crea el curso cuando todos los materiales están listos', async () => {
  const flow = await fixture()
  flow.setName('Curso con material válido')
  flow.selectFiles([file()])
  await flow.submit()

  assert.deepEqual([...flow.server.courses.keys()], [EXISTING_COURSE, NEW_COURSE])
  assert.equal(flow.server.materials.get(NEW_COURSE)[0].status, 'ready')
  assert.deepEqual(flow.navigations, ['/cursos/' + NEW_COURSE])
  assert.equal(flow.view().busy, false)
})

test('un error al crear el curso no inicia la carga', async () => {
  const flow = await fixture('course_rejected')
  flow.setName('Curso rechazado')
  flow.selectFiles([file()])
  await flow.submit()

  assertNoNewCourse(flow)
  assert.deepEqual(flow.server.calls, ['createCourse'])
  assert.equal(flow.navigations.length, 0)
  assert.ok(flow.view().notice)
  assert.equal(flow.view().busy, false)
})

test('un fallo de carga conserva intacto el curso anterior y sus documentos', async () => {
  const flow = await fixture('upload_rejected')
  flow.setName('Curso fallido')
  flow.selectFiles([file()])
  await flow.submit()

  assert.deepEqual(flow.server.courses.get(EXISTING_COURSE), flow.server.existing)
  assert.deepEqual(flow.server.materials.get(EXISTING_COURSE), [flow.server.existingMaterial])
})

for (const outcome of ['upload_rejected', 'processing_failed', 'mixed']) {
  test('no conserva un curso nuevo si falla el archivo con resultado ' + outcome, async () => {
    const flow = await fixture(outcome)
    flow.setName('Curso fallido')
    flow.selectFiles(outcome === 'mixed' ? [file('valido.txt'), file('fallido.txt')] : [file()])
    await flow.submit()

    assertNoNewCourse(flow)
    assert.equal(flow.navigations.length, 0)
    assert.ok(flow.view().notice)
    assert.equal(flow.view().busy, false)
  })
}

test('espera a que termine el procesamiento antes de navegar', { timeout: 5000 }, async () => {
  const flow = await fixture('queued')
  flow.setName('Curso pendiente')
  flow.selectFiles([file()])
  const submission = flow.submit()
  await flush()
  try {
    assert.equal(flow.navigations.length, 0)
    assert.equal(flow.view().busy, true)
  } finally {
    flow.server.completeProcessing('ready')
    await submission
  }
})

test('un fallo posterior a la aceptación de la carga no deja el curso nuevo', { timeout: 5000 }, async () => {
  const flow = await fixture('queued')
  flow.setName('Curso con fallo posterior')
  flow.selectFiles([file()])
  const submission = flow.submit()
  await flush()
  flow.server.completeProcessing('failed')
  await submission

  assertNoNewCourse(flow)
  assert.equal(flow.navigations.length, 0)
  assert.ok(flow.view().notice)
})

test('reintentar una carga rechazada no acumula cursos nuevos', async () => {
  const flow = await fixture('upload_rejected')
  flow.setName('Curso con reintento')
  flow.selectFiles([file()])
  await flow.submit()
  await flow.submit()

  assertNoNewCourse(flow)
  assert.equal(flow.navigations.length, 0)
})
