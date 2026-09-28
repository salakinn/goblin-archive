import type { AiSettings, Author, Book, BookPage, CoverMetadata, FilterOptions, ImportJob, IsbnCandidate, IsbnSearchResult, TranslationJob, UpdateStatus } from './types'

let csrfToken = ''
export function setCsrfToken(token: string) { csrfToken = token }

async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(input, { ...init, headers })
  if (response.status === 401) window.dispatchEvent(new Event('goblin-unauthorized'))
  return response
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail?.message || body?.detail || body?.error?.message || `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function getBooks(params: URLSearchParams, signal?: AbortSignal): Promise<BookPage> {
  const endpoint = params.get('q')
    ? `/api/search?q=${encodeURIComponent(params.get('q')!)}`
    : `/api/books?${params.toString()}`
  const result = await json<{ items: Book[]; total: number; limit?: number; offset?: number }>(await apiFetch(endpoint, { signal }))
  return { items: result.items, total: result.total, limit: result.limit || result.items.length, offset: result.offset || 0 }
}

export async function getBook(id: string): Promise<Book> {
  return json<Book>(await apiFetch(`/api/books/${id}`))
}

export async function getAiSettings(): Promise<AiSettings> {
  return json(await apiFetch('/api/settings/ai'))
}

export async function saveAiSettings(settings: Partial<AiSettings> & { api_key?: string | null }): Promise<AiSettings> {
  return json(await apiFetch('/api/settings/ai', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) }))
}

export async function testAiSettings(): Promise<{ ok: boolean; model: string }> {
  return json(await apiFetch('/api/settings/ai/test', { method: 'POST' }))
}

export async function getUpdateStatus(): Promise<UpdateStatus> {
  return json(await apiFetch('/api/update', { cache: 'no-store' }))
}

export async function installUpdate(password: string): Promise<{ started: boolean; latest_version: string; install_mode: 'truenas' | 'systemd' }> {
  return json(await apiFetch('/api/update/install', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  }))
}

export async function addTag(id: string, name: string): Promise<Book> {
  return json<Book>(await apiFetch(`/api/books/${id}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  }))
}

export async function removeTag(id: string, tagId: number): Promise<Book> {
  return json<Book>(await apiFetch(`/api/books/${id}/tags/${tagId}`, { method: 'DELETE' }))
}

export async function generateTags(id: string): Promise<{ book: Book; added: number; cached: boolean }> {
  return json(await apiFetch(`/api/books/${id}/ai/tags`, { method: 'POST' }))
}

export async function detectLanguage(id: string): Promise<{ book: Book; status: 'detected' | 'unclear' | 'protected' | 'insufficient_text'; applied: boolean; cached: boolean }> {
  return json(await apiFetch(`/api/books/${id}/ai/language`, { method: 'POST' }))
}

export async function listTranslations(bookId: string): Promise<TranslationJob[]> {
  return (await json<{ items: TranslationJob[] }>(await apiFetch(`/api/books/${bookId}/translations`))).items
}

export async function createTranslation(bookId: string, target_language: string, profile: string, budget_usd: number | null): Promise<TranslationJob> {
  return json(await apiFetch(`/api/books/${bookId}/translations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_language, profile, budget_usd }) }))
}

export async function getTranslation(id: string): Promise<TranslationJob> {
  return json(await apiFetch(`/api/translations/${id}`))
}

export async function translationAction(id: string, action: 'start' | 'pause' | 'cancel'): Promise<TranslationJob> {
  return json(await apiFetch(`/api/translations/${id}/${action}`, { method: 'POST' }))
}

export async function saveTranslationGlossary(id: string, glossary: { source: string; target: string }[], style: string): Promise<TranslationJob> {
  return json(await apiFetch(`/api/translations/${id}/glossary`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ glossary, style }) }))
}

export async function saveTranslationBudget(id: string, budget_usd: number | null): Promise<TranslationJob> {
  return json(await apiFetch(`/api/translations/${id}/budget`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ budget_usd }) }))
}

export async function getAuthors(): Promise<Author[]> {
  return (await json<{ items: Author[] }>(await apiFetch('/api/authors'))).items
}

export async function getFilterOptions(): Promise<FilterOptions> {
  return json<FilterOptions>(await apiFetch('/api/filter-options'))
}

export async function refreshCover(id: string): Promise<{ found: boolean; replaced: boolean; cover: CoverMetadata | null }> {
  return json(await apiFetch(`/api/books/${id}/cover/refresh`, { method: 'POST' }))
}

export async function getIsbnCandidates(id: string): Promise<IsbnCandidate[]> {
  return (await json<{ candidates: IsbnCandidate[] }>(await apiFetch(`/api/books/${id}/isbn/candidates`))).candidates
}

export async function searchIsbn(id: string, refresh = false): Promise<IsbnSearchResult> {
  return json<IsbnSearchResult>(await apiFetch(`/api/books/${id}/isbn/search?refresh=${refresh}`, { method: 'POST' }))
}

export async function applyIsbn(id: string, isbn: string): Promise<void> {
  await json(await apiFetch(`/api/books/${id}/isbn/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ isbn }),
  }))
}

export async function applyIsbnReference(id: string, isbn: string): Promise<void> {
  await json(await apiFetch(`/api/books/${id}/isbn/reference`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ isbn }),
  }))
}

export async function uploadBooks(files: File[]): Promise<ImportJob> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  return json<ImportJob>(await apiFetch('/api/import', { method: 'POST', body: form }))
}

export async function getImport(id: string): Promise<ImportJob> {
  return json<ImportJob>(await apiFetch(`/api/imports/${id}`))
}

export async function clearArchive(): Promise<{ deleted_books: number }> {
  return json<{ deleted_books: number }>(await apiFetch('/api/archive?confirmation=L%C3%96SCHEN', { method: 'DELETE' }))
}
