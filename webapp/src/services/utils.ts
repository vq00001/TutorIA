export async function responseError(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as { message?: string; detail?: string }
    return body.message ?? body.detail ?? `${fallback} (${response.status})`
  } catch {
    return `${fallback} (${response.status})`
  }
}
