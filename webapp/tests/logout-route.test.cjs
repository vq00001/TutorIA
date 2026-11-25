const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const { test } = require('node:test')
const { runInNewContext } = require('node:vm')
const ts = require('typescript')

const services = path.resolve(__dirname, '../src/services')
const TOKEN_KEY = 'tutoria.accessToken'
const ORIGIN = 'http://tutoria.test'

// Reproduce las operaciones de navegación usadas por el navegador.
// No impone si el cierre debe usar location o history.
function browser(initialRoute, token) {
  const url = new URL(initialRoute, ORIGIN)
  const storage = new Map(token ? [[TOKEN_KEY, token]] : [])
  const localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  }
  function navigate(destination) {
    url.href = new URL(destination, url).href
  }
  const location = {
    get href() { return url.href },
    set href(value) { navigate(value) },
    get pathname() { return url.pathname },
    set pathname(value) { url.pathname = value },
    get search() { return url.search },
    get hash() { return url.hash },
    assign: navigate,
    replace: navigate,
  }
  const history = {
    pushState: (_state, _unused, destination) => {
      if (destination != null) navigate(destination)
    },
    replaceState: (_state, _unused, destination) => {
      if (destination != null) navigate(destination)
    },
  }
  const window = new EventTarget()
  Object.defineProperty(window, 'location', {
    get: () => location,
    set: navigate,
  })
  window.history = history
  window.localStorage = localStorage
  return { window, location, history, localStorage, url }
}

function fixture({ route = '/cursos/curso-anterior', outcome = 'ok', token = 'token-anterior' } = {}) {
  const environment = browser(route, token)
  const requests = []
  async function fetchMock(input, init) {
    requests.push({ input, init })
    if (input === '/api/v1/auth/logout') {
      if (outcome === 'network') throw new TypeError('Red no disponible')
      const status = { ok: 204, unavailable: 503, expired: 401 }[outcome]
      assert.ok(status, 'Resultado de cierre desconocido')
      return new Response(null, { status })
    }
    assert.equal(input, '/api/v1/auth/login', 'Petición fuera de los datos de prueba')
    return new Response(JSON.stringify({
      data: {
        accessToken: 'token-nuevo',
        tokenType: 'Bearer',
        user: { id: 'usuario-nuevo', email: 'nuevo@example.test', displayName: 'Usuario nuevo', status: 'active' },
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }

  const modules = new Map()
  function loadModule(filename) {
    if (modules.has(filename)) return modules.get(filename).exports
    const source = readFileSync(filename, 'utf8')
    const compiled = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
      fileName: filename,
    })
    const module = { exports: {} }
    modules.set(filename, module)
    runInNewContext(compiled.outputText, {
      module,
      exports: module.exports,
      ...environment,
      Headers,
      Event,
      URL,
      URLSearchParams,
      fetch: fetchMock,
      require(specifier) {
        assert.ok(specifier.startsWith('./'), 'Importación no prevista por el test')
        return loadModule(path.resolve(path.dirname(filename), specifier + '.ts'))
      },
    }, { filename })
    return module.exports
  }
  const api = loadModule(path.join(services, 'authApi.ts'))
  return { api, ...environment, requests }
}

// Controles existentes. El RED de navegación no debe confundirse con una
// regresión de limpieza de credenciales o de notificación a AuthGate.
for (const outcome of ['ok', 'unavailable', 'network', 'expired']) {
  test('limpia el token y notifica el cierre con resultado ' + outcome, async () => {
    const { api, requests } = fixture({ outcome })
    const observedTokens = []
    const unsubscribe = api.listenForAuthExpiration(() => {
      observedTokens.push(api.getAccessToken())
    })
    try {
      await api.logout()
    } finally {
      unsubscribe()
    }

    assert.equal(api.getAccessToken(), null)
    assert.ok(observedTokens.length >= 1)
    assert.ok(observedTokens.every((token) => token === null))
    assert.equal(requests.length, 1)
    assert.equal(requests[0].input, '/api/v1/auth/logout')
    assert.equal(requests[0].init.method, 'POST')
    assert.equal(requests[0].init.headers.get('Authorization'), 'Bearer token-anterior')
  })
}

test('cerrar sesión desde el inicio conserva la URL limpia', async () => {
  const { api, url } = fixture({ route: '/' })
  await api.logout()
  assert.equal(url.href, ORIGIN + '/')
})

// Contrato RED. El destino es la ruta de inicio ya definida por App.tsx.
for (const route of [
  '/cursos/curso-anterior',
  '/cursos/curso-anterior/grafo',
  '/cursos/curso-anterior/examenes',
  '/materiales?courseId=curso-anterior#archivo-anterior',
]) {
  test('cerrar sesión vuelve al inicio desde ' + route, async () => {
    const { api, url } = fixture({ route })
    await api.logout()
    assert.equal(url.href, ORIGIN + '/')
  })
}

for (const outcome of ['unavailable', 'network', 'expired']) {
  test('vuelve al inicio aunque el servidor responda con ' + outcome, async () => {
    const { api, url } = fixture({
      route: '/cursos/curso-anterior/grafo?tema=anterior#concepto-anterior',
      outcome,
    })
    await api.logout()
    assert.equal(url.href, ORIGIN + '/')
  })
}

test('vuelve al inicio aunque el token ya se haya eliminado', async () => {
  const { api, url, requests } = fixture({
    route: '/cursos/curso-anterior/grafo',
    token: null,
  })
  await api.logout()
  assert.equal(requests.length, 0)
  assert.equal(url.href, ORIGIN + '/')
})

test('una cuenta nueva no hereda la ruta de la sesión anterior', async () => {
  const { api, url } = fixture({
    route: '/cursos/curso-anterior/grafo?tema=anterior#concepto-anterior',
  })
  await api.logout()
  const user = await api.login('nuevo@example.test', 'password-sintetica')

  assert.equal(user.id, 'usuario-nuevo')
  assert.equal(api.getAccessToken(), 'token-nuevo')
  assert.equal(url.href, ORIGIN + '/')
})
