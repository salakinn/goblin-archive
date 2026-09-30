import type { AiSettings, AIUsageSummary, Author, Book, BookMetadataUpdate, BookPage, CoverMetadata, FilterOptions, ImportJob, ImportPreview, IsbnCandidate, IsbnSearchResult, TranslationGlossary, TranslationJob, TranslationSegments, UpdateStatus } from './types'

let csrfToken = ''
export function setCsrfToken(token: string) { csrfToken = token }

async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', csrfToken)
  let response = await fetch(input, { ...init, headers })
  if (response.status === 403 && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const body = await response.clone().json().catch(() => null)
    if (body?.detail?.message === 'CSRF-Prüfung fehlgeschlagen.') {
      const status = await fetch('/api/auth/status', { cache: 'no-store' }).then(result => result.json()).catch(() => null)
      if (status?.authenticated && status.csrf_token && status.csrf_token !== headers.get('X-CSRF-Token')) {
        setCsrfToken(status.csrf_token)
        headers.set('X-CSRF-Token', status.csrf_token)
        response = await fetch(input, { ...init, headers })
      }
    }
  }
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
export async function updateBookMetadata(id: string, values: BookMetadataUpdate): Promise<Book> {
  return json<Book>(await apiFetch(`/api/books/${id}/metadata`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) }))
}

export async function getAiSettings(): Promise<AiSettings> {
  return json(await apiFetch('/api/settings/ai'))
}

export type BackupJob = { id: string; kind: 'backup' | 'restore'; status: string; phase: string; created_at: string; processed_bytes: number; total_bytes?: number; size?: number; book_count?: number; current_book_count?: number; created_backup_at?: string; format?: number; error?: string; previous?: string; previous_bytes?: number }
export async function listBackups(): Promise<BackupJob[]> { return (await json<{ items: BackupJob[] }>(await apiFetch('/api/backups'))).items }
export async function createBackup(): Promise<BackupJob> { return json(await apiFetch('/api/backups', { method: 'POST' })) }
export async function uploadBackup(file: File): Promise<BackupJob> {
  const form = new FormData(); form.append('file', file)
  return json(await apiFetch('/api/backups/restore/upload', { method: 'POST', body: form }))
}
export async function restoreBackup(id: string): Promise<BackupJob> { return json(await apiFetch(`/api/backups/${id}/restore`, { method: 'POST' })) }
export async function deleteBackup(id: string): Promise<void> { await json(await apiFetch(`/api/backups/${id}`, { method: 'DELETE' })) }
export async function deletePreviousBackup(id: string): Promise<void> { await json(await apiFetch(`/api/backups/${id}/previous`, { method: 'DELETE' })) }

export async function saveAiSettings(settings: Partial<AiSettings> & { api_key?: string | null }): Promise<AiSettings> {
  return json(await apiFetch('/api/settings/ai', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) }))
}

export type AiConnectionInput = Pick<AiSettings, 'provider' | 'base_url'> & { api_key?: string; ai_tagging_model?: string }
export type AiModelPrices = Record<string, { input_usd_per_million: number; output_usd_per_million: number }>
export type AiModelCatalog = { models: string[]; prices: AiModelPrices }

export async function listAiModels(input: AiConnectionInput): Promise<AiModelCatalog> {
  return json<AiModelCatalog>(await apiFetch('/api/settings/ai/models', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  }))
}

export async function testAiSettings(input?: AiConnectionInput): Promise<{ ok: boolean; model: string }> {
  return json(await apiFetch('/api/settings/ai/test', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input || {}),
  }))
}
export async function getAiUsage(): Promise<AIUsageSummary> { return json(await apiFetch('/api/ai/usage')) }
export async function listGlossaries(): Promise<TranslationGlossary[]> { return (await json<{ items: TranslationGlossary[] }>(await apiFetch('/api/glossaries'))).items }

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

export async function generateTags(id: string): Promise<{ book: Book; added: number; removed?: number; cached: boolean; message?: string }> {
  return json(await apiFetch(`/api/books/${id}/ai/tags`, { method: 'POST' }))
}

export async function normalizeTitle(id: string): Promise<{ book: Book; changed: boolean; cached: boolean; message?: string; original_title?: string; normalized_title?: string }> {
  return json(await apiFetch(`/api/books/${id}/ai/title`, { method: 'POST' }))
}

export async function normalizeAuthors(id: string): Promise<{ book: Book; changed: boolean; message?: string; original_authors?: string[]; normalized_authors?: string[] }> {
  return json(await apiFetch(`/api/books/${id}/authors/normalize`, { method: 'POST' }))
}

export async function detectLanguage(id: string): Promise<{ book: Book; status: 'detected' | 'unclear' | 'protected' | 'insufficient_text'; applied: boolean; cached: boolean }> {
  return json(await apiFetch(`/api/books/${id}/ai/language`, { method: 'POST' }))
}

export async function listTranslations(bookId: string): Promise<TranslationJob[]> {
  return (await json<{ items: TranslationJob[] }>(await apiFetch(`/api/books/${bookId}/translations`))).items
}

export async function createTranslation(bookId: string, target_language: string, profile: string, budget_usd: number | null, glossary_id?: number | null): Promise<TranslationJob> {
  return json(await apiFetch(`/api/books/${bookId}/translations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_language, profile, budget_usd, glossary_id }) }))
}

export async function getTranslation(id: string): Promise<TranslationJob> {
  return json(await apiFetch(`/api/translations/${id}`))
}
export async function getTranslationSegments(id: string, offset = 0): Promise<TranslationSegments> {
  return json(await apiFetch(`/api/translations/${id}/segments?offset=${offset}&limit=50`))
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
export async function repairTranslationSegment(id: string, segmentId: string): Promise<TranslationJob> {
  return json(await apiFetch(`/api/translations/${id}/segments/${encodeURIComponent(segmentId)}/repair`, { method: 'POST' }))
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

export async function getImportLimits(): Promise<{ max_files: number; max_bytes: number }> {
  return json(await apiFetch('/api/import/limits'))
}

export async function uploadPreviews(files: File[]): Promise<ImportPreview[]> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  return (await json<{ items: ImportPreview[] }>(await apiFetch('/api/import/previews', { method: 'POST', body: form }))).items
}
export async function listPreviews(): Promise<ImportPreview[]> {
  return (await json<{ items: ImportPreview[] }>(await apiFetch('/api/import/previews'))).items
}
export async function editPreview(id: string, revision: number, changes: Record<string, unknown>, coverChoice?: string): Promise<ImportPreview> {
  return json(await apiFetch(`/api/import/previews/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, changes, cover_choice: coverChoice }) }))
}
export async function previewAction(id: string, action: 'confirm' | 'discard' | 'enrich' | 'reanalyze' | 'cover/search' | 'skip' | 'resume'): Promise<ImportPreview> {
  return json(await apiFetch(`/api/import/previews/${id}/${action}`, { method: 'POST' }))
}
export type PreviewTitleSuggestion = { revision: number; original_title: string; suggested_title: string; token: string; provider: string; model: string }
export async function suggestPreviewTitle(id: string, revision: number, original_title: string): Promise<PreviewTitleSuggestion> {
  return json(await apiFetch(`/api/import/previews/${id}/ai/title/suggest`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, original_title, suggested_title: original_title }) }))
}
export async function acceptPreviewTitle(id: string, suggestion: PreviewTitleSuggestion): Promise<ImportPreview> {
  return json(await apiFetch(`/api/import/previews/${id}/ai/title/accept`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(suggestion) }))
}

export async function decidePreviewDuplicate(id: string, revision: number, action: 'keep_both' | 'use_existing', bookId?: string): Promise<ImportPreview> {
  return json(await apiFetch(`/api/import/previews/${id}/duplicate-decision`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision, action, book_id: bookId }),
  }))
}

export type MetadataSourceRow = { field: string; value: unknown; source: string | null; recorded_at: string }
export async function getMetadataSources(id: string): Promise<MetadataSourceRow[]> {
  return (await json<{ items: MetadataSourceRow[] }>(await apiFetch(`/api/books/${id}/metadata-sources`))).items
}

export async function getImport(id: string): Promise<ImportJob> {
  return json<ImportJob>(await apiFetch(`/api/imports/${id}`))
}
export async function listImports(): Promise<ImportJob[]> {
  return (await json<{ items: ImportJob[] }>(await apiFetch('/api/imports'))).items
}
export async function retryImportStep(importId: string, itemId: string): Promise<ImportJob> {
  return json<ImportJob>(await apiFetch(`/api/imports/${importId}/items/${itemId}/retry`, { method: 'POST' }))
}

export async function clearArchive(): Promise<{ deleted_books: number }> {
  return json<{ deleted_books: number }>(await apiFetch('/api/archive?confirmation=L%C3%96SCHEN', { method: 'DELETE' }))
}
