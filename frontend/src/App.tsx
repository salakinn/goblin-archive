import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { addTag, applyIsbn, applyIsbnReference, clearArchive, getBook, getBooks, getFilterOptions, getImport, getIsbnCandidates, refreshCover, removeTag, searchIsbn, uploadBooks } from './api'
import { compatibleBookFiles, filesFromDrop } from './drop'
import type { Book, FilterOption, FilterOptions, ImportJob, ImportItem, IsbnCandidate } from './types'

const eventLabels: Record<string, string> = {
  queued: 'Wartet',
  'import.hashing': 'Prüft Fingerabdruck…',
  'import.metadata.embedded': 'Eingebettete Metadaten gefunden',
  'import.metadata.provider': 'Externe Metadaten ergänzt',
  'import.cover.embedded': 'Eingebettetes Cover gefunden',
  'import.cover.provider': 'Cover-Dienst wird verwendet',
  'import.cover.found': 'Cover gespeichert',
  'import.cover.missing': 'Kein Cover gefunden',
  'import.archiving': 'Wird archiviert…',
  'import.finished': 'Archiviert',
  'import.duplicate': 'Bereits im Archiv',
  'import.failed': 'Import fehlgeschlagen',
}

const emptyFilterOptions: FilterOptions = {
  tags: [], authors: [], languages: [], publishers: [], formats: [], series: [], years: [],
}

function FilterSelect({ label, allLabel, value, options, onChange, uppercase = false, showCount = true }: {
  label: string
  allLabel: string
  value: string
  options: FilterOption[]
  onChange: (value: string) => void
  uppercase?: boolean
  showCount?: boolean
}) {
  return <select aria-label={`${label} auswählen`} value={value} onChange={event => onChange(event.target.value)}>
    <option value="">{allLabel}</option>
    {options.map(option => <option key={option.value} value={option.value}>
      {uppercase ? option.label.toUpperCase() : option.label}{showCount ? ` (${option.book_count})` : ''}
    </option>)}
  </select>
}

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function DownloadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <path d="m7 10 5 5 5-5" />
    <path d="M12 15V3" />
  </svg>
}

function BookCard({ book, onClick, onTagClick, coverVersion }: { book: Book; onClick: () => void; onTagClick: (tag: string) => void; coverVersion?: number }) {
  return <article className="book-card">
    <div className="cover">
      <button className="cover-open" onClick={onClick} aria-label={`${book.title} öffnen`}>
        {book.has_cover
          ? <img src={`/api/books/${book.id}/cover${coverVersion ? `?v=${coverVersion}` : ''}`} alt="" loading="lazy" />
          : <div className="cover-fallback"><span>GA</span><small>{book.format}</small></div>}
      </button>
      <span className="format-badge">{book.format}</span>
      <a className="download-button" href={`/api/books/${book.id}/download`} download title="Buch herunterladen" aria-label={`${book.title} herunterladen`}><DownloadIcon /></a>
    </div>
    <div className="book-copy">
      <button className="book-summary" onClick={onClick}>
        <h3>{book.title}</h3>
        <p className="author">{book.authors.join(', ') || 'Unbekannter Autor'}</p>
        <p className="book-meta">{[book.publication_year, book.language_label].filter(Boolean).join(' · ') || 'Ohne Jahresangabe'}</p>
      </button>
      <div className="chips">{book.tags.slice(0, 3).map(tag => <button key={tag.id} onClick={() => onTagClick(tag.name)}>{tag.name}</button>)}</div>
    </div>
  </article>
}

function Detail({ book, tagOptions, close, filterByTag, refreshed }: { book: Book; tagOptions: string[]; close: () => void; filterByTag: (tag: string) => void; refreshed: (book: Book) => void }) {
  const [refreshing, setRefreshing] = useState(false)
  const [coverNotice, setCoverNotice] = useState('')
  const [coverVersion, setCoverVersion] = useState(0)
  const [isbnCandidates, setIsbnCandidates] = useState<IsbnCandidate[]>([])
  const [isbnSearching, setIsbnSearching] = useState(false)
  const [isbnApplying, setIsbnApplying] = useState('')
  const [isbnNotice, setIsbnNotice] = useState('')
  const [tagName, setTagName] = useState('')
  const [tagSaving, setTagSaving] = useState(false)
  const [tagNotice, setTagNotice] = useState('')
  const coverSource = book.cover_source === 'embedded' ? 'Embedded'
    : book.cover_provider === 'openlibrary' ? 'Open Library'
    : book.cover_provider === 'googlebooks' ? 'Google Books' : '—'
  async function searchCover() {
    try {
      setRefreshing(true)
      setCoverNotice('')
      const result = await refreshCover(book.id)
      if (result.found) {
        const updated = await getBook(book.id)
        refreshed(updated)
        setCoverVersion(Date.now())
        setCoverNotice('Cover wurde aktualisiert.')
      } else {
        setCoverNotice(book.has_cover ? 'Kein besserer Treffer – vorhandenes Cover bleibt erhalten.' : 'Kein Cover gefunden.')
      }
    } catch (err) {
      setCoverNotice(err instanceof Error ? err.message : 'Cover-Suche fehlgeschlagen.')
    } finally { setRefreshing(false) }
  }
  useEffect(() => {
    getIsbnCandidates(book.id).then(setIsbnCandidates).catch(() => setIsbnCandidates([]))
  }, [book.id])
  async function findIsbn(force = false) {
    try {
      setIsbnSearching(true)
      setIsbnNotice('')
      const result = await searchIsbn(book.id, force)
      setIsbnCandidates(result.candidates)
      if (result.auto_applied) {
        const updated = await getBook(book.id)
        refreshed(updated)
        setCoverVersion(Date.now())
        setIsbnNotice(`ISBN ${updated.isbn} wurde eindeutig erkannt und übernommen.`)
      } else if (result.reference_applied) {
        const updated = await getBook(book.id)
        refreshed(updated)
        setIsbnNotice(`Werk erkannt; ${updated.reference_isbn} wurde nur als Referenz gespeichert.`)
      } else if (!result.candidates.length) {
        setIsbnNotice('Keine plausible ISBN gefunden.')
      } else {
        setIsbnNotice('Bitte die passende Ausgabe auswählen.')
      }
    } catch (err) {
      setIsbnNotice(err instanceof Error ? err.message : 'ISBN-Suche fehlgeschlagen.')
    } finally { setIsbnSearching(false) }
  }
  async function chooseIsbn(candidate: IsbnCandidate) {
    try {
      setIsbnApplying(candidate.isbn13)
      setIsbnNotice('')
      await applyIsbn(book.id, candidate.isbn13)
      const updated = await getBook(book.id)
      refreshed(updated)
      setCoverVersion(Date.now())
      setIsbnNotice(`ISBN ${candidate.isbn13} wurde übernommen.`)
    } catch (err) {
      setIsbnNotice(err instanceof Error ? err.message : 'ISBN konnte nicht übernommen werden.')
    } finally { setIsbnApplying('') }
  }
  async function chooseReference(candidate: IsbnCandidate) {
    try {
      setIsbnApplying(`reference:${candidate.isbn13}`)
      setIsbnNotice('')
      await applyIsbnReference(book.id, candidate.isbn13)
      const updated = await getBook(book.id)
      refreshed(updated)
      setIsbnNotice(`ISBN ${candidate.isbn13} wurde als Werkreferenz gespeichert, nicht als ISBN dieser Datei.`)
    } catch (err) {
      setIsbnNotice(err instanceof Error ? err.message : 'Werkreferenz konnte nicht übernommen werden.')
    } finally { setIsbnApplying('') }
  }
  async function saveTag() {
    if (!tagName.trim()) return
    try {
      setTagSaving(true)
      setTagNotice('')
      refreshed(await addTag(book.id, tagName))
      setTagName('')
    } catch (err) {
      setTagNotice(err instanceof Error ? err.message : 'Tag konnte nicht gespeichert werden.')
    } finally { setTagSaving(false) }
  }
  async function deleteTag(tagId: number) {
    try {
      setTagSaving(true)
      setTagNotice('')
      refreshed(await removeTag(book.id, tagId))
    } catch (err) {
      setTagNotice(err instanceof Error ? err.message : 'Tag konnte nicht entfernt werden.')
    } finally { setTagSaving(false) }
  }
  const fields: [string, string, string | null | undefined][] = [
    ['Titel', book.title, book.metadata?.title?.source],
    ['Autor', book.authors.join(', ') || '—', book.metadata?.authors?.source],
    ['Jahr', book.publication_year?.toString() || '—', book.metadata?.publication_year?.source],
    ['Sprache', book.language_label || '—', book.metadata?.language?.source],
    ['Verlag', book.publisher || '—', book.metadata?.publisher?.source],
    ['Ausgaben-ISBN', book.isbn || '—', book.metadata?.isbn?.source],
    ['Referenz-ISBN', book.reference_isbn || '—', book.work_match ? `${book.work_match.confidence.toFixed(0)} % Werkmatch` : null],
    ['Reihe', book.series || '—', book.metadata?.series?.source],
  ]
  return <div className="detail-backdrop" onMouseDown={event => event.target === event.currentTarget && close()}>
    <article className="detail-panel">
      <button className="close" onClick={close} aria-label="Schließen">×</button>
      <div className="detail-hero">
        <div className="detail-cover">{book.has_cover ? <img src={`/api/books/${book.id}/cover?v=${coverVersion}`} alt={`Cover von ${book.title}`} /> : <span>GA</span>}</div>
        <div><p className="eyebrow">{book.format} · {formatBytes(book.file_size)}</p><h2>{book.title}</h2><p className="detail-author">{book.authors.join(', ') || 'Unbekannter Autor'}</p><p className="cover-source">Cover: {coverSource}</p><button className="refresh-cover" disabled={refreshing} onClick={searchCover}>{refreshing ? 'Cover wird gesucht…' : 'Cover neu suchen'}</button>{coverNotice && <small className="cover-notice">{coverNotice}</small>}</div>
      </div>
      {book.description && <p className="description">{book.description}</p>}
      <section className="tag-editor">
        <h3 className="section-title">Tags</h3>
        {book.tags.length > 0 && <div className="detail-tags">{book.tags.map(tag => <span key={tag.id}><button className="tag-filter" onClick={() => filterByTag(tag.name)}>{tag.name}</button><button className="tag-remove" disabled={tagSaving} onClick={() => deleteTag(tag.id)} aria-label={`${tag.name} entfernen`}>×</button></span>)}</div>}
        <form onSubmit={event => { event.preventDefault(); saveTag() }}>
          <input list="available-tags" value={tagName} maxLength={200} onChange={event => setTagName(event.target.value)} placeholder="Tag hinzufügen…" aria-label="Tag hinzufügen" />
          <datalist id="available-tags">{tagOptions.map(tag => <option key={tag} value={tag} />)}</datalist>
          <button disabled={tagSaving || !tagName.trim()}>{tagSaving ? 'Speichert…' : 'Hinzufügen'}</button>
        </form>
        {tagNotice && <small className="tag-notice">{tagNotice}</small>}
      </section>
      <h3 className="section-title">Metadaten</h3>
      <dl className="metadata-list">{fields.map(([name, value, source]) => <div key={name}><dt>{name}</dt><dd>{value}</dd><small>{source || '—'}</small></div>)}</dl>
      <section className="isbn-resolver">
        <div className="isbn-heading"><div><h3>Werk- und ISBN-Auflösung</h3><p>Eine Ausgaben-ISBN gehört exakt zur Datei. Eine Referenz-ISBN bezeichnet nur dasselbe Werk und dient der späteren Metadatenanreicherung.</p></div><div className="isbn-actions"><button disabled={isbnSearching || Boolean(isbnApplying)} onClick={() => findIsbn(false)}>{isbnSearching ? 'Suche läuft…' : 'Werk suchen'}</button>{(isbnCandidates.length > 0 || isbnNotice) && <button className="quiet" disabled={isbnSearching || Boolean(isbnApplying)} onClick={() => findIsbn(true)}>Neu abfragen</button>}</div></div>
        {isbnNotice && <p className="isbn-notice">{isbnNotice}</p>}
        {book.work_match && <div className="work-match"><strong>Erkanntes Werk: {book.work_match.title}</strong><span>{book.work_match.confidence.toFixed(0)} % · {book.work_match.sources.join(' + ')}</span>{book.work_match.suggested_genres.length > 0 && <p>Tag-Vorschläge: {book.work_match.suggested_genres.slice(0, 8).join(', ')}</p>}{book.work_match.work_series.length > 0 && <p>Werkreihe: {book.work_match.work_series.join(', ')}</p>}</div>}
        {isbnCandidates.length > 0 && <div className="isbn-candidates">{isbnCandidates.slice(0, 12).map(candidate => <article key={candidate.isbn13} className={candidate.isbn13 === book.isbn || candidate.isbn13 === book.reference_isbn ? 'selected' : ''}>
          <div><strong>{candidate.isbn13}</strong><small>{candidate.match_type === 'edition' ? 'Ausgabe plausibel' : 'Werkreferenz'}{candidate.isbn10 ? ` · ISBN-10: ${candidate.isbn10}` : ''}</small></div>
          <p>{candidate.title}</p>
          <small>{[candidate.authors.join(', '), candidate.publisher, candidate.publication_year].filter(Boolean).join(' · ')}</small>
          {candidate.subjects.length > 0 && <small className="candidate-subjects">{candidate.subjects.slice(0, 5).join(' · ')}</small>}
          <footer><span>{candidate.sources.join(' + ')} · Werk {candidate.work_score.toFixed(0)} % · Ausgabe {candidate.score.toFixed(0)} %</span><div className="candidate-actions"><button className="quiet" disabled={Boolean(isbnApplying) || candidate.isbn13 === book.reference_isbn} onClick={() => chooseReference(candidate)}>{candidate.isbn13 === book.reference_isbn ? 'Werkreferenz' : isbnApplying === `reference:${candidate.isbn13}` ? 'Speichert…' : 'Als Werk'}</button><button disabled={Boolean(isbnApplying) || candidate.isbn13 === book.isbn} onClick={() => chooseIsbn(candidate)}>{candidate.isbn13 === book.isbn ? 'Ausgaben-ISBN' : isbnApplying === candidate.isbn13 ? 'Wird übernommen…' : 'Als Ausgabe'}</button></div></footer>
        </article>)}</div>}
      </section>
      <div className="file-info"><span>Originaldatei</span><strong>{book.original_filename}</strong><span>Archivpfad</span><strong>{book.library_path}</strong></div>
    </article>
  </div>
}

function ImportPanel({ jobs, collapsed, toggle }: { jobs: ImportJob[]; collapsed: boolean; toggle: () => void }) {
  if (!jobs.length) return null
  const latest = jobs[jobs.length - 1]
  return <aside className={`import-panel ${collapsed ? 'collapsed' : ''}`}>
    <button className="import-head" onClick={toggle}><span><i /> Import</span><strong>{latest.completed} / {latest.total}</strong><b>{collapsed ? '⌃' : '⌄'}</b></button>
    {!collapsed && <div className="import-items">{jobs.flatMap(job => job.items).slice(-8).map((item, index) => <div className={`import-item ${item.status}`} key={`${item.filename}-${index}`}>
      <span className="state">{item.status === 'finished' ? '✓' : item.status === 'failed' ? '!' : item.status === 'duplicate' ? '↺' : '●'}</span>
      <div><strong>{item.filename}</strong><small>{item.message || eventLabels[item.event] || item.event}</small></div>
    </div>)}</div>}
  </aside>
}

export default function App() {
  const [books, setBooks] = useState<Book[]>([])
  const [filterOptions, setFilterOptions] = useState<FilterOptions>(emptyFilterOptions)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState({ tag: '', year_from: '', year_to: '', author: '', language: '', publisher: '', format: '', series: '' })
  const [dragging, setDragging] = useState(false)
  const [jobs, setJobs] = useState<ImportJob[]>([])
  const [collapsed, setCollapsed] = useState(false)
  const [selected, setSelected] = useState<Book | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [notice, setNotice] = useState('')
  const [coverVersions, setCoverVersions] = useState<Record<string, number>>({})
  const dragDepth = useRef(0)

  const params = useMemo(() => {
    const result = new URLSearchParams()
    if (query.trim()) result.set('q', query.trim())
    else Object.entries(filters).forEach(([key, value]) => value && result.set(key, value))
    return result
  }, [query, filters])

  const loadBooks = useCallback(async () => {
    try { setError(''); setBooks(await getBooks(params)) }
    catch (err) { setError(err instanceof Error ? err.message : 'Bibliothek konnte nicht geladen werden') }
    finally { setLoading(false) }
  }, [params])

  const loadFilterOptions = useCallback(async () => {
    try { setFilterOptions(await getFilterOptions()) }
    catch (err) { setError(err instanceof Error ? err.message : 'Filter konnten nicht geladen werden') }
  }, [])

  useEffect(() => { const timer = window.setTimeout(loadBooks, 180); return () => clearTimeout(timer) }, [loadBooks])
  useEffect(() => { loadFilterOptions() }, [loadFilterOptions])

  useEffect(() => {
    const source = new EventSource('/api/events')
    const names = Object.keys(eventLabels).filter(name => name.startsWith('import.'))
    const onEvent = async (rawEvent: Event) => {
      const event = rawEvent as MessageEvent
      const payload = JSON.parse(event.data)
      if (payload.import_id) {
        try {
          const job = await getImport(payload.import_id)
          setJobs(current => current.some(item => item.id === job.id) ? current.map(item => item.id === job.id ? job : item) : [...current, job])
        } catch { /* job may have vanished after a backend restart */ }
      }
      if (event.type === 'import.finished' || event.type === 'import.duplicate') {
        loadBooks()
        loadFilterOptions()
      }
    }
    names.forEach(name => source.addEventListener(name, onEvent))
    return () => source.close()
  }, [loadBooks, loadFilterOptions])

  useEffect(() => {
    const active = jobs.filter(job => job.status === 'queued' || job.status === 'running')
    if (!active.length) return
    const timer = window.setInterval(async () => {
      const updates = await Promise.all(active.map(job => getImport(job.id).catch(() => job)))
      let newlyFinished = false
      setJobs(current => current.map(job => {
        const update = updates.find(candidate => candidate.id === job.id)
        if (update && job.status !== 'finished' && update.status === 'finished') newlyFinished = true
        return update || job
      }))
      if (newlyFinished) {
        loadBooks()
        loadFilterOptions()
      }
    }, 1200)
    return () => clearInterval(timer)
  }, [jobs, loadBooks, loadFilterOptions])

  async function importFiles(files: File[]) {
    if (!files.length) { setError('Keine unterstützten E-Books gefunden.'); return }
    try {
      setError('')
      const job = await uploadBooks(files)
      setJobs(current => [...current, job])
      setCollapsed(false)
    } catch (err) { setError(err instanceof Error ? err.message : 'Upload fehlgeschlagen') }
  }

  async function importSelection(input: HTMLInputElement) {
    const files = compatibleBookFiles(input.files || [])
    input.value = ''
    await importFiles(files)
  }

  async function openBook(id: string) {
    try {
      const detail = await getBook(id)
      setSelected(detail)
      history.pushState({ book: id }, '', `/books/${id}`)
    } catch (err) { setError(err instanceof Error ? err.message : 'Buch konnte nicht geladen werden') }
  }

  function closeDetail() { setSelected(null); history.pushState({}, '', '/') }

  function filterByTag(tag: string) {
    setQuery('')
    setFilters(current => ({ ...current, tag }))
    if (selected) closeDetail()
  }

  async function deleteEverything() {
    const confirmed = window.confirm('Gesamtes Archiv unwiderruflich leeren? Alle Bücher und archivierten Dateien werden gelöscht.')
    if (!confirmed) return
    try {
      setClearing(true)
      setError('')
      const result = await clearArchive()
      setSelected(null)
      setJobs([])
      setSettingsOpen(false)
      setNotice(`${result.deleted_books} ${result.deleted_books === 1 ? 'Buch wurde' : 'Bücher wurden'} gelöscht.`)
      history.replaceState({}, '', '/')
      await Promise.all([loadBooks(), loadFilterOptions()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Archiv konnte nicht geleert werden')
    } finally {
      setClearing(false)
    }
  }

  useEffect(() => {
    const id = location.pathname.match(/^\/books\/(bk_[a-f0-9]+)$/)?.[1]
    if (id) getBook(id).then(setSelected).catch(() => history.replaceState({}, '', '/'))
    const pop = () => {
      const current = location.pathname.match(/^\/books\/(bk_[a-f0-9]+)$/)?.[1]
      current ? getBook(current).then(setSelected) : setSelected(null)
    }
    addEventListener('popstate', pop)
    return () => removeEventListener('popstate', pop)
  }, [])

  return <div className="app" onDragEnter={event => { event.preventDefault(); dragDepth.current++; setDragging(true) }} onDragLeave={event => { event.preventDefault(); if (--dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false) } }} onDragOver={event => event.preventDefault()} onDrop={async event => { event.preventDefault(); dragDepth.current = 0; setDragging(false); await importFiles(await filesFromDrop(event.dataTransfer)) }}>
    <header>
      <a className="brand" href="/"><span className="goblin">G</span><span><strong>Goblin</strong><small>ARCHIVAR</small></span></a>
      <div className="settings-wrap">
        <button className="settings" title="Einstellungen" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)}>⚙</button>
        {settingsOpen && <div className="settings-menu">
          <strong>Entwicklung</strong>
          <p>Entfernt alle importierten Bücher und Dateien aus dem lokalen Archiv.</p>
          <button className="danger-button" disabled={clearing} onClick={deleteEverything}>{clearing ? 'Archiv wird geleert…' : 'Gesamtes Archiv leeren'}</button>
        </div>}
      </div>
    </header>
    <main>
      <section className="intro">
        <label className="search library-search"><span>⌕</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Titel, Autor, ISBN durchsuchen…" /><kbd>⌘ K</kbd></label>
        <div className="import-actions">
          <label className="add-button"><b aria-hidden="true">＋</b><span>Bücher hinzufügen</span><input type="file" multiple accept=".epub,.pdf,.mobi,.azw3" onChange={event => importSelection(event.currentTarget)} /></label>
          <label className="folder-button"><b aria-hidden="true">▣</b><span>Ordner importieren</span><input ref={input => { if (input) { input.webkitdirectory = true; input.setAttribute('directory', '') } }} type="file" multiple accept=".epub,.pdf,.mobi,.azw3" onChange={event => importSelection(event.currentTarget)} /></label>
        </div>
      </section>
      <section className="filters">
        <span className="filter-label">FILTER</span>
        <FilterSelect label="Tag" allLabel="Alle Tags" value={filters.tag} options={filterOptions.tags} showCount={false} onChange={tag => setFilters({...filters, tag})} />
        <FilterSelect label="Autor" allLabel="Alle Autoren" value={filters.author} options={filterOptions.authors} onChange={author => setFilters({...filters, author})} />
        <FilterSelect label="Sprache" allLabel="Alle Sprachen" value={filters.language} options={filterOptions.languages} onChange={language => setFilters({...filters, language})} />
        <FilterSelect label="Verlag" allLabel="Alle Verlage" value={filters.publisher} options={filterOptions.publishers} onChange={publisher => setFilters({...filters, publisher})} />
        <FilterSelect label="Format" allLabel="Alle Formate" value={filters.format} options={filterOptions.formats} uppercase onChange={format => setFilters({...filters, format})} />
        <FilterSelect label="Reihe" allLabel="Alle Reihen" value={filters.series} options={filterOptions.series} onChange={series => setFilters({...filters, series})} />
        <FilterSelect label="Jahr von" allLabel="Jahr von" value={filters.year_from} options={[...filterOptions.years].reverse()} showCount={false} onChange={year_from => setFilters({...filters, year_from})} />
        <FilterSelect label="Jahr bis" allLabel="Jahr bis" value={filters.year_to} options={filterOptions.years} showCount={false} onChange={year_to => setFilters({...filters, year_to})} />
        {Object.values(filters).some(Boolean) && <button className="reset" onClick={() => setFilters({ tag: '', year_from: '', year_to: '', author: '', language: '', publisher: '', format: '', series: '' })}>Zurücksetzen</button>}
      </section>
      {error && <div className="error-banner">{error}<button onClick={() => setError('')}>×</button></div>}
      {notice && <div className="notice-banner">{notice}<button onClick={() => setNotice('')}>×</button></div>}
      {loading ? <div className="empty">Der Goblin blättert durch das Archiv…</div> : books.length ? <section className="book-grid">{books.map(book => <BookCard key={book.id} book={book} coverVersion={coverVersions[book.id]} onClick={() => openBook(book.id)} onTagClick={filterByTag} />)}</section> : <div className="empty"><span>🧌</span><h2>Das Archiv ist noch hungrig</h2><p>Ziehe EPUB-, PDF-, MOBI- oder AZW3-Dateien hierher.</p></div>}
    </main>
    {dragging && <div className="drop-overlay"><div><span>🧌</span><h2>Bücher dem Goblin verfüttern</h2><p>Dateien oder Ordner hier ablegen</p></div></div>}
    <ImportPanel jobs={jobs} collapsed={collapsed} toggle={() => setCollapsed(!collapsed)} />
    {selected && <Detail book={selected} tagOptions={filterOptions.tags.map(tag => tag.label)} close={closeDetail} filterByTag={filterByTag} refreshed={book => { setSelected(book); setBooks(current => current.map(item => item.id === book.id ? book : item)); setCoverVersions(current => ({ ...current, [book.id]: Date.now() })); loadBooks(); loadFilterOptions() }} />}
  </div>
}
