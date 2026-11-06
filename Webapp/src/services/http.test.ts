import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

describe('HTTP request boundary', () => {
  beforeEach(() => vi.resetModules())
  afterEach(() => vi.unstubAllGlobals())

  it('uses cookie credentials and no CSRF request for a read', async () => {
    const fetch = vi.fn().mockResolvedValue(response({ data: [] }))
    vi.stubGlobal('fetch', fetch)
    const { request } = await import('./http')
    expect(await request('/courses')).toEqual({ data: [] })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/courses')
    expect(fetch.mock.calls[0][1].credentials).toBe('same-origin')
    expect(fetch.mock.calls[0][1].headers.has('X-CSRF-Token')).toBe(false)
  })

  it('fetches CSRF first and sends a new idempotency key for a mutation', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ data: { csrfToken: 'synthetic-csrf' } })).mockResolvedValueOnce(response({ data: { id: 'course' } }))
    vi.stubGlobal('fetch', fetch)
    const { request } = await import('./http')
    await request('/courses', { method: 'POST', body: JSON.stringify({ name: 'Synthetic' }) })
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/auth/csrf')
    const options = fetch.mock.calls[1][1]
    expect(options.headers.get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(options.headers.get('Idempotency-Key')).toMatch(/^[a-f0-9-]{36}$/)
    expect(options.headers.get('Content-Type')).toBe('application/json')
  })

  it('preserves a supplied idempotency key and leaves multipart boundaries to the browser', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ data: { csrfToken: 'csrf' } })).mockResolvedValueOnce(response({ data: {} }))
    vi.stubGlobal('fetch', fetch)
    const body = new FormData()
    body.append('files', new File(['# Fuente real'], 'tema.md', { type: 'text/markdown' }))
    const { request } = await import('./http')
    await request('/courses/course/materials', { method: 'POST', headers: { 'Idempotency-Key': 'retry-this-upload' }, body })
    const options = fetch.mock.calls[1][1]
    expect(options.headers.has('Content-Type')).toBe(false)
    expect(options.headers.get('Idempotency-Key')).toBe('retry-this-upload')
    expect((options.body as FormData).get('files')).toBeInstanceOf(File)
  })

  it('propagates rejected operations and does not treat an error as success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ error: { code: 'NOT_FOUND', message: 'No disponible' } }, 404)))
    const { request, ApiError } = await import('./http')
    try { await request('/courses/missing'); expect.fail('Expected rejection') }
    catch (error) { expect(error).toBeInstanceOf(ApiError); expect(error).toMatchObject({ code: 'NOT_FOUND', status: 404, message: 'No disponible' }) }
  })

  it('stops a write if the CSRF request failed', async () => {
    const fetch = vi.fn().mockResolvedValue(response({}, 503))
    vi.stubGlobal('fetch', fetch)
    const { request } = await import('./http')
    await expect(request('/courses', { method: 'POST', body: '{}' })).rejects.toThrow('sesión segura')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('handles logout with no response body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({ data: { csrfToken: 'csrf' } })).mockResolvedValueOnce(new Response(null, { status: 204 })))
    const { request } = await import('./http')
    await expect(request('/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
  })

  it('reads all stable pages and carries the returned cursor', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ data: [{ id: 'a' }], meta: { nextCursor: 'a/b' } })).mockResolvedValueOnce(response({ data: [{ id: 'b' }], meta: { nextCursor: null } }))
    vi.stubGlobal('fetch', fetch)
    const { allPages } = await import('./http')
    expect(await allPages('/courses')).toEqual({ data: [{ id: 'a' }, { id: 'b' }] })
    expect(fetch.mock.calls[1][0]).toBe('/api/v1/courses?cursor=a%2Fb')
  })
})
