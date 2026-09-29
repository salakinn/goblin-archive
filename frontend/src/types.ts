export type SourceValue<T = unknown> = { value: T | null; source: string | null }

export type ImportPreview = {
  id: string
  group_id: string
  filename: string
  created_at: string
  expires_at: string
  status: 'queued' | 'analyzing' | 'ready' | 'cover_search' | 'archiving' | 'archived' | 'failed' | 'skipped' | 'discarded'
  metadata: Record<string, SourceValue> | null
  cover: CoverMetadata | null
  embedded_cover: CoverMetadata | null
  external_cover: CoverMetadata | null
  cover_selected: boolean
  providers: string[]
  format: string | null
  sha256: string | null
  duplicate_book_id: string | null
  duplicate_preview_id: string | null
  book_id: string | null
  error: string | null
  revision: number
  edited: boolean
}

export type AiSettings = {
  provider: 'openai' | 'custom'
  base_url: string
  key_configured: boolean
  ai_tagging_model: string
  ai_language_model: string
  ai_translation_model: string
  ai_translation_qa_model: string
  ai_translation_editor_model: string
  ai_translation_input_usd_per_million: number
  ai_translation_output_usd_per_million: number
  ai_translation_qa_input_usd_per_million: number | null
  ai_translation_qa_output_usd_per_million: number | null
  ai_translation_editor_input_usd_per_million: number | null
  ai_translation_editor_output_usd_per_million: number | null
  ai_tagging_input_usd_per_million: number
  ai_tagging_output_usd_per_million: number
  ai_language_input_usd_per_million: number
  ai_language_output_usd_per_million: number
  ai_daily_limit_usd: number
  ai_monthly_limit_usd: number
  ai_warning_percent: number
}

export type AIUsageSummary = { day_usd: number; month_usd: number; reserved_usd: number; warning: { day: boolean; month: boolean }; by_feature: Record<string, { requests: number; input_tokens: number; output_tokens: number; cost_usd: number; unpriced_requests: number }> }
export type TranslationGlossary = { id: number; name: string; source_language: string; target_language: string; version: number; glossary: { source: string; target: string }[]; style: string }

export type UpdateStatus = {
  enabled: boolean
  available: boolean
  install_ready: boolean
  install_mode: 'truenas' | 'systemd' | null
  current_version: string
  latest_version: string | null
}

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
  translation?: { source_book_id: string; target_language: string; job_id: string } | null
}

export type BookMetadataUpdate = Pick<Book, 'title' | 'publication_year' | 'language' | 'publisher' | 'isbn' | 'reference_isbn' | 'series' | 'description'> & { authors: string[] }

export type BookPage = { items: Book[]; total: number; limit: number; offset: number }

export type TranslationJob = {
  id: string
  status: string
  target_language: string
  profile: string
  budget_usd: number | null
  estimated_cost_usd: number | null
  cost_usd: number
  total_segments: number
  completed_segments: number
  glossary: { source: string; target: string }[]
  style: string
  preview: { source: string; translated: string }[]
  error: string | null
  output_book_id: string | null
}
export type TranslationSegment = { id: string; source: string; file: string; translated: string | null; draft: string | null }
export type TranslationSegments = { total: number; offset: number; items: TranslationSegment[] }

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
