export type SourceValue<T = unknown> = { value: T | null; source: string | null }

export type CoverMetadata = {
  filename: string
  source: 'embedded' | 'external'
  provider: 'openlibrary' | 'googlebooks' | null
  source_id: string | null
  width: number
  height: number
  mime_type?: string
  fetched_at?: string
  source_url?: string
}

export type IsbnCandidate = {
  isbn13: string
  isbn10: string | null
  title: string
  authors: string[]
  publisher: string | null
  publication_year: number | null
  language: string | null
  format: string | null
  sources: string[]
  source_ids: string[]
  score: number
  work_score: number
  match_type: 'edition' | 'reference'
  subjects: string[]
  series: string[]
}

export type WorkMatch = {
  title: string
  authors: string[]
  language: string | null
  confidence: number
  sources: string[]
  source_ids: string[]
  reference_isbns: string[]
  suggested_genres: string[]
  work_series: string[]
  matched_at: string
}

export type IsbnSearchResult = {
  book_id: string
  cache_hit: boolean
  auto_applied: boolean
  auto_candidate: IsbnCandidate | null
  auto_reference: IsbnCandidate | null
  reference_applied: boolean
  candidates: IsbnCandidate[]
}

export type Book = {
  id: string
  title: string
  authors: string[]
  publication_year: number | null
  language: string | null
  language_label: string | null
  publisher: string | null
  isbn: string | null
  reference_isbn: string | null
  genres: string[]
  tags: Tag[]
  series: string | null
  format: string
  file_size: number
  original_filename: string
  imported_at: string
  has_cover: boolean
  cover_source: string | null
  cover_provider: string | null
  description?: string | null
  sha256?: string
  library_path?: string
  metadata?: Record<string, SourceValue>
  tag_sources?: Record<string, { source: string; provider: string; model: string; created_at: string; reason: string }>
  language_detection?: { status: string; language: string | null; created_at: string; assessments: { sample_id: number; language: string; status: string; reason: string }[] } | null
  cover?: CoverMetadata | null
  work_match?: WorkMatch | null
}

export type Tag = {
  id: number
  name: string
}

export type Author = {
  id: number
  name: string
  book_count: number
}

export type FilterOption = {
  value: string
  label: string
  book_count: number
}

export type FilterOptions = {
  tags: FilterOption[]
  authors: FilterOption[]
  languages: FilterOption[]
  publishers: FilterOption[]
  formats: FilterOption[]
  series: FilterOption[]
  years: FilterOption[]
}

export type ImportItem = {
  filename: string
  status: 'queued' | 'finished' | 'duplicate' | 'failed'
  event: string
  message: string | null
  book_id: string | null
}

export type ImportJob = {
  id: string
  status: string
  total: number
  completed: number
  items: ImportItem[]
}
