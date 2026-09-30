/**
 * The single HTTP boundary between the React product and FastAPI.
 *
 * - Same-origin only: the bundle is served by FastAPI at /app, so requests go to /api/...
 *   and the HttpOnly session cookie travels automatically. No credentials ever live in JS.
 * - Unsafe methods carry the CSRF token (double-submit: the non-HttpOnly sp_csrf cookie is
 *   echoed as X-CSRF-Token; the server compares it with the session's token).
 * - The active scope (organization) is sent as X-Scope-Org; the server decides whether the
 *   signed-in user may act in it. The browser never enforces access — it only asks.
 * - Every failure becomes an ApiError with a readable message; 401 also broadcasts an event
 *   so the shell can send the user to sign in.
 */

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, message: string, detail: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const UNAUTHENTICATED_EVENT = 'platform:unauthenticated'

let activeScope: string | null = null

export function setActiveScope(orgId: string | null): void {
  activeScope = orgId
}

export function getActiveScope(): string | null {
  return activeScope
}

export function readCookie(name: string): string | null {
  const match = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export interface RequestOptions {
  method?: string
  json?: unknown
  form?: FormData
  signal?: AbortSignal
  scope?: string | null
}

function messageFrom(status: number, body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
        .join('; ')
    }
    if (detail && typeof detail === 'object' && 'error' in detail) {
      return String((detail as { error: unknown }).error)
    }
  }
  if (status === 401) return 'You need to sign in to continue.'
  if (status === 403) return 'You do not have permission to do this in the current scope.'
  if (status === 404) return 'Not found in the current scope.'
  if (status === 429) return 'Too many requests. Wait a moment and try again.'
  if (status >= 500) return 'The server could not complete the request.'
  return `Request failed (${status}).`
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? (opts.json !== undefined || opts.form ? 'POST' : 'GET')).toUpperCase()
  const headers: Record<string, string> = { Accept: 'application/json' }
  let body: BodyInit | undefined
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.json)
  } else if (opts.form) {
    body = opts.form
  }
  if (UNSAFE.has(method)) {
    const csrf = readCookie('sp_csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }
  const scope = opts.scope === undefined ? activeScope : opts.scope
  if (scope) headers['X-Scope-Org'] = scope

  let res: Response
  try {
    res = await fetch(path, { method, headers, body, credentials: 'same-origin', signal: opts.signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'The server could not be reached. Check that it is running.', err)
  }

  const type = res.headers.get('content-type') ?? ''
  const parsed: unknown = type.includes('application/json') ? await res.json().catch(() => null) : await res.text()
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
    throw new ApiError(res.status, messageFrom(res.status, parsed), parsed)
  }
  return parsed as T
}
