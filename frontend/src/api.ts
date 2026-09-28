import type { Author, Book, CoverMetadata, FilterOptions, ImportJob, IsbnCandidate, IsbnSearchResult } from './types'

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail?.message || body?.detail || body?.error?.message || `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function getBooks(params: URLSearchParams): Promise<Book[]> {
  const endpoint = params.get('q')
    ? `/api/search?q=${encodeURIComponent(params.get('q')!)}`
    : `/api/books?${params.toString()}`
  return (await json<{ items: Book[] }>(await fetch(endpoint))).items
}

export async function getBook(id: string): Promise<Book> {
  return json<Book>(await fetch(`/api/books/${id}`))
}

export async function addTag(id: string, name: string): Promise<Book> {
  return json<Book>(await fetch(`/api/books/${id}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  }))
}

export async function removeTag(id: string, tagId: number): Promise<Book> {
  return json<Book>(await fetch(`/api/books/${id}/tags/${tagId}`, { method: 'DELETE' }))
}

export async function generateTags(id: string): Promise<{ book: Book; added: number; cached: boolean }> {
  return json(await fetch(`/api/books/${id}/ai/tags`, { method: 'POST' }))
}

export async function detectLanguage(id: string): Promise<{ book: Book; status: 'detected' | 'unclear' | 'protected' | 'insufficient_text'; applied: boolean; cached: boolean }> {
  return json(await fetch(`/api/books/${id}/ai/language`, { method: 'POST' }))
}

export async function getAuthors(): Promise<Author[]> {
  return (await json<{ items: Author[] }>(await fetch('/api/authors'))).items
}

export async function getFilterOptions(): Promise<FilterOptions> {
  return json<FilterOptions>(await fetch('/api/filter-options'))
}

export async function refreshCover(id: string): Promise<{ found: boolean; replaced: boolean; cover: CoverMetadata | null }> {
  return json(await fetch(`/api/books/${id}/cover/refresh`, { method: 'POST' }))
}

export async function getIsbnCandidates(id: string): Promise<IsbnCandidate[]> {
  return (await json<{ candidates: IsbnCandidate[] }>(await fetch(`/api/books/${id}/isbn/candidates`))).candidates
}

export async function searchIsbn(id: string, refresh = false): Promise<IsbnSearchResult> {
  return json<IsbnSearchResult>(await fetch(`/api/books/${id}/isbn/search?refresh=${refresh}`, { method: 'POST' }))
}

export async function applyIsbn(id: string, isbn: string): Promise<void> {
  await json(await fetch(`/api/books/${id}/isbn/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ isbn }),
  }))
}

export async function applyIsbnReference(id: string, isbn: string): Promise<void> {
  await json(await fetch(`/api/books/${id}/isbn/reference`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ isbn }),
  }))
}

export async function uploadBooks(files: File[]): Promise<ImportJob> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  return json<ImportJob>(await fetch('/api/import', { method: 'POST', body: form }))
}

export async function getImport(id: string): Promise<ImportJob> {
  return json<ImportJob>(await fetch(`/api/imports/${id}`))
}

export async function clearArchive(): Promise<{ deleted_books: number }> {
  return json<{ deleted_books: number }>(await fetch('/api/archive?confirmation=L%C3%96SCHEN', { method: 'DELETE' }))
}
