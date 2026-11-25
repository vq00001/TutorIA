const assert = require('node:assert/strict')
const { readFile } = require('node:fs/promises')
const path = require('node:path')
const { test } = require('node:test')
const { runInNewContext } = require('node:vm')
const ts = require('typescript')

const services = path.resolve(__dirname, '../src/services')

// Compila el TypeScript original y sustituye únicamente sus dependencias HTTP.
async function loadService(name, fetchMock) {
  const filename = path.join(services, name + '.ts')
  const source = await readFile(filename, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename,
  })
  const module = { exports: {} }
  runInNewContext(compiled.outputText, {
    module,
    exports: module.exports,
    Date,
    URLSearchParams,
    setTimeout,
    require(specifier) {
      if (specifier === './authApi') return { authorizedFetch: fetchMock }
      assert.equal(specifier, './utils', 'Importación no prevista por el test')
      return { responseError: () => assert.fail('La API simulada devolvió un error no previsto') }
    },
  }, { filename })
  return module.exports
}

function fixture({ conversationCount = 1, messageCount = 1, jobState = 'succeeded' } = {}) {
  const conversations = Array.from({ length: conversationCount }, (_, index) => ({
    id: 'conversation-' + index,
    updatedAt: '2026-11-25T12:00:00+00:00',
  }))
  const histories = new Map(conversations.map((conversation) => [conversation.id,
    Array.from({ length: messageCount }, (_, index) => ({
      id: conversation.id + '-message-' + (index + 1),
      sequence: index + 1,
      role: index === messageCount - 1 || index % 2 ? 'assistant' : 'user',
      content: 'Contenido íntegro del mensaje ' + (index + 1),
      createdAt: '2026-11-25T12:00:00+00:00',
      inReplyTo: index === messageCount - 1 ? 'question-latest' : null,
      sourceDocuments: [],
    })),
  ]))
  const base = '/api/v1/courses/course-test'

  async function fetchMock(input) {
    const url = new URL(input, 'http://fixture.invalid')
    let body
    if (url.pathname === base + '/jobs/job-test') {
      body = { data: { state: jobState, errorCode: jobState === 'failed' ? 'TEST_FAILED' : null } }
    } else if (url.pathname === base + '/conversations') {
      const beforeId = url.searchParams.get('beforeId')
      const start = beforeId ? conversations.findIndex((row) => row.id === beforeId) + 1 : 0
      if (beforeId) {
        assert.ok(start > 0, 'Cursor de conversación desconocido')
        assert.equal(url.searchParams.get('beforeUpdatedAt'), conversations[start - 1].updatedAt)
      }
      const limit = Number(url.searchParams.get('limit') ?? 20)
      assert.ok(Number.isInteger(limit) && limit > 0 && limit <= 100)
      const data = conversations.slice(start, start + limit)
      const last = data.at(-1)
      body = {
        data,
        nextCursor: start + limit < conversations.length
          ? { beforeId: last.id, beforeUpdatedAt: last.updatedAt } : null,
      }
    } else {
      const conversationId = url.pathname.slice((base + '/conversations/').length)
      assert.equal(url.pathname, base + '/conversations/' + conversationId)
      assert.ok(histories.has(conversationId), 'Petición fuera de los datos de prueba')
      const after = Number(url.searchParams.get('afterSequence') ?? 0)
      const limit = Number(url.searchParams.get('limit') ?? 50)
      assert.ok(Number.isInteger(after) && after >= 0)
      assert.ok(Number.isInteger(limit) && limit > 0 && limit <= 100)
      const remaining = histories.get(conversationId).filter((message) => message.sequence > after)
      const messages = remaining.slice(0, limit)
      body = {
        data: conversations.find((row) => row.id === conversationId),
        messages,
        nextAfterSequence: remaining.length > limit ? messages.at(-1).sequence : null,
      }
    }
    return new Response(JSON.stringify(body), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }
  return { fetchMock, histories }
}

for (const count of [0, 20, 21, 101]) {
  test('historial conserva los mensajes de ' + count + ' conversaciones', async () => {
    const data = fixture({ conversationCount: count })
    const { databaseApi } = await loadService('databaseApi', data.fetchMock)
    const result = await databaseApi.getCourseMessages('course-test')
    const expectedIds = [...data.histories.values()].flat().map((message) => message.id)
    assert.deepEqual(Array.from(result.data, (message) => message.id), expectedIds)
  })
}

for (const count of [0, 50, 51, 101]) {
  test('historial conserva los ' + count + ' mensajes de una conversación', async () => {
    const data = fixture({ messageCount: count })
    const { databaseApi } = await loadService('databaseApi', data.fetchMock)
    const result = await databaseApi.getCourseMessages('course-test')
    const expected = data.histories.get('conversation-0')
    assert.deepEqual(Array.from(result.data, (message) => message.id), expected.map((message) => message.id))
    assert.deepEqual(Array.from(result.data, (message) => message.parts[0].content),
      expected.map((message) => message.content))
  })
}

for (const count of [50, 51, 101]) {
  test('recupera la última respuesta con un historial de ' + count + ' mensajes', async () => {
    const data = fixture({ messageCount: count })
    const { waitForAssistant } = await loadService('llmApi', data.fetchMock)
    const response = await waitForAssistant('course-test', 'conversation-0', 'job-test', 'question-latest')
    const expected = data.histories.get('conversation-0').at(-1)
    assert.equal(response.id, expected.id)
    assert.equal(response.content, expected.content)
  })
}

test('preserva el contenido completo de un mensaje largo', async () => {
  const data = fixture()
  const content = '# Desarrollo\n\n' + 'Explicación completa. '.repeat(1000) + '\nFIN'
  data.histories.get('conversation-0')[0].content = content
  const { databaseApi } = await loadService('databaseApi', data.fetchMock)
  const result = await databaseApi.getCourseMessages('course-test')
  assert.equal(result.data[0].parts[0].content, content)
})

test('un trabajo fallido se comunica como error', async () => {
  const data = fixture({ jobState: 'failed' })
  const { waitForAssistant } = await loadService('llmApi', data.fetchMock)
  await assert.rejects(
    waitForAssistant('course-test', 'conversation-0', 'job-test', 'question-latest'),
    /El tutor no pudo generar una respuesta/,
  )
})
