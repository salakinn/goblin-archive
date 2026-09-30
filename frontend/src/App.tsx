import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { addTag, applyIsbn, applyIsbnReference, clearArchive, detectLanguage, generateTags, getBook, getBooks, getFilterOptions, getImport, getImportLimits, getIsbnCandidates, getMetadataSources, listImports, normalizeAuthors, normalizeTitle, refreshCover, removeTag, retryImportStep, searchIsbn, updateBookMetadata, uploadBooks, uploadPreviews } from './api'
import type { MetadataSourceRow } from './api'
import { compatibleBookFiles, filesFromDrop } from './drop'
import { TranslationPanel } from './TranslationPanel'
import { AiSettingsPanel } from './AiSettingsPanel'
import { BackupPanel } from './BackupPanel'
import { UpdatePrompt } from './UpdatePrompt'
import { PreviewPanel } from './PreviewPanel'
import type { Book, FilterOption, FilterOptions, ImportJob, IsbnCandidate } from './types'

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
  'import.archived': 'Archiviert · Nachbearbeitung wartet',
  'import.postprocess.queued': 'Nachbearbeitung wartet',
  'import.postprocess.isbn': 'ISBN wird geprüft…',
  'import.postprocess.authors': 'Autoren werden geprüft…',
  'import.postprocess.language': 'Sprache wird geprüft…',
  'import.postprocess.tags': 'Tags werden ergänzt…',
  'import.finished': 'Fertig',
  'import.duplicate': 'Bereits im Archiv',
  'import.discarded': 'Vorschau verworfen',
  'import.needs_review': 'Mögliches Duplikat: Vorschau prüfen',
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
  const [aiTagging, setAiTagging] = useState(false)
  const [tagNotice, setTagNotice] = useState('')
  const [languageDetecting, setLanguageDetecting] = useState(false)
  const [languageNotice, setLanguageNotice] = useState('')
  const [titleNormalizing, setTitleNormalizing] = useState(false)
  const [titleNotice, setTitleNotice] = useState('')
  const [authorNormalizing, setAuthorNormalizing] = useState(false)
  const [authorNotice, setAuthorNotice] = useState('')
  const [editingMetadata, setEditingMetadata] = useState(false)
  const [metadataSaving, setMetadataSaving] = useState(false)
  const [metadataNotice, setMetadataNotice] = useState('')
  const [sourceRows, setSourceRows] = useState<MetadataSourceRow[]>([])
  const [sourceOpen, setSourceOpen] = useState(false)
  const [sourceError, setSourceError] = useState('')
  const [metadataForm, setMetadataForm] = useState(() => ({ title: book.title, authors: book.authors.join(', '), publication_year: book.publication_year?.toString() || '', language: book.language || '', publisher: book.publisher || '', isbn: book.isbn || '', reference_isbn: book.reference_isbn || '', series: book.series || '', description: book.description || '' }))
  const coverSource = book.cover_source === 'embedded' ? 'Embedded'
    : book.cover_provider === 'openlibrary' ? 'Open Library'
    : book.cover_provider === 'googlebooks' ? 'Google Books' : '—'
  const nonLatinAuthor = book.authors.some(name => /[^\p{Script=Latin}\p{M}\s.,'-]/u.test(name))
  function beginMetadataEdit() {
    setMetadataForm({ title: book.title, authors: book.authors.join(', '), publication_year: book.publication_year?.toString() || '', language: book.language || '', publisher: book.publisher || '', isbn: book.isbn || '', reference_isbn: book.reference_isbn || '', series: book.series || '', description: book.description || '' })
    setMetadataNotice(''); setEditingMetadata(true)
  }
  async function saveMetadata(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      setMetadataSaving(true); setMetadataNotice('')
      const updated = await updateBookMetadata(book.id, { ...metadataForm, authors: metadataForm.authors.split(',').map(value => value.trim()).filter(Boolean), publication_year: metadataForm.publication_year ? Number(metadataForm.publication_year) : null, language: metadataForm.language || null, publisher: metadataForm.publisher || null, isbn: metadataForm.isbn || null, reference_isbn: metadataForm.reference_isbn || null, series: metadataForm.series || null, description: metadataForm.description || null })
      refreshed(updated); setEditingMetadata(false); setMetadataNotice('Metadaten gespeichert.')
    } catch (err) { setMetadataNotice(err instanceof Error ? err.message : 'Metadaten konnten nicht gespeichert werden.') }
    finally { setMetadataSaving(false) }
  }
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
  async function setTagsWithAI() {
    try {
      setTagSaving(true)
      setAiTagging(true)
      setTagNotice('')
      const result = await generateTags(book.id)
      refreshed(result.book)
      setTagNotice(result.message || (result.cached ? 'Diese Buchdaten wurden bereits analysiert. Kein weiterer KI-Aufruf.'
        : result.added || result.removed ? `${result.added} neue Tags ergänzt${result.removed ? `; ${result.removed} veraltete KI-Tags ersetzt` : ''}.` : 'Die KI hat keine neuen Tags vorgeschlagen.')
      )
    } catch (err) {
      setTagNotice(err instanceof Error ? err.message : 'KI-Tags konnten nicht gesetzt werden.')
    } finally { setTagSaving(false); setAiTagging(false) }
  }
  async function findLanguage() {
    try {
      setLanguageDetecting(true)
      setLanguageNotice('')
      const result = await detectLanguage(book.id)
      refreshed(result.book)
      const messages = {
        detected: result.cached ? `Bereits analysiert (${result.book.language_detection?.source === 'local' ? 'lokal' : 'per KI'}): ${result.book.language_detection?.language?.toUpperCase() || 'eindeutig'}. Aktuelle Sprachangabe bleibt erhalten.`
          : `${result.book.language_detection?.source === 'local' ? 'Lokal erkannt' : 'Per KI erkannt'}: ${result.book.language_label || result.book.language}. Direkt gespeichert.`,
        unclear: `Sprache unklar. Bisherige Angabe bleibt erhalten.${result.book.language_detection?.fallback_error ? ` ${result.book.language_detection.fallback_error}` : ''}`,
        protected: 'Die Sprachangabe wurde manuell bestätigt und bleibt erhalten.',
        insufficient_text: 'Keine drei ausreichend langen Textproben gefunden. Bei gescannten PDFs ist zunächst OCR nötig.',
      }
      setLanguageNotice(messages[result.status])
    } catch (err) {
      setLanguageNotice(err instanceof Error ? err.message : 'Sprache konnte nicht ermittelt werden.')
    } finally { setLanguageDetecting(false) }
  }
  async function normalizeTitleNow() {
    try {
      setTitleNormalizing(true)
      setTitleNotice('')
      const result = await normalizeTitle(book.id)
      refreshed(result.book)
      setTitleNotice(result.message || (result.changed
        ? `Titel bereinigt: ${result.normalized_title}`
        : result.cached ? 'Dieser Titel wurde bereits bereinigt.' : 'Der Titel ist bereits sauber.'))
    } catch (err) {
      setTitleNotice(err instanceof Error ? err.message : 'Titel konnte nicht bereinigt werden.')
    } finally { setTitleNormalizing(false) }
  }
  async function normalizeAuthorsNow() {
    try {
      setAuthorNormalizing(true)
      setAuthorNotice('')
      const result = await normalizeAuthors(book.id)
      refreshed(result.book)
      setAuthorNotice(result.message || (result.changed
        ? `Autor korrigiert: ${result.normalized_authors?.join(', ')}`
        : 'Keine sichere Korrektur gefunden.'))
    } catch (err) {
      setAuthorNotice(err instanceof Error ? err.message : 'Autor konnte nicht korrigiert werden.')
    } finally { setAuthorNormalizing(false) }
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
    ['Referenz-ISBN', book.reference_isbn || '—', book.metadata?.reference_isbn?.source || (book.work_match ? `${book.work_match.confidence.toFixed(0)} % Werkmatch` : null)],
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
      {book.translation && <p className="tag-notice">KI-Übersetzung von <a href={`/books/${book.translation.source_book_id}`}>Originalbuch öffnen</a></p>}
      <section className="tag-editor">
        <div className="detail-section-header"><h3 className="section-title">Tags</h3>
          <button type="button" className="detail-action" disabled={tagSaving || languageDetecting || titleNormalizing} onClick={setTagsWithAI}>{aiTagging ? 'KI analysiert…' : 'Tags per KI setzen'}</button></div>
        <p className="tag-notice">Übermittelt Buchmetadaten an den eingestellten KI-Anbieter und ergänzt passende Tags direkt.</p>
        {book.tags.length > 0 && <div className="detail-tags">{book.tags.map(tag => <span key={tag.id}><button className="tag-filter" onClick={() => filterByTag(tag.name)}>{tag.name}</button><button className="tag-remove" disabled={tagSaving} onClick={() => deleteTag(tag.id)} aria-label={`${tag.name} entfernen`}>×</button></span>)}</div>}
        <form onSubmit={event => { event.preventDefault(); saveTag() }}>
          <input list="available-tags" value={tagName} maxLength={200} onChange={event => setTagName(event.target.value)} placeholder="Tag hinzufügen…" aria-label="Tag hinzufügen" />
          <datalist id="available-tags">{tagOptions.map(tag => <option key={tag} value={tag} />)}</datalist>
          <button disabled={tagSaving || !tagName.trim()}>{tagSaving ? 'Speichert…' : 'Hinzufügen'}</button>
        </form>
        {tagNotice && <small className="tag-notice" role="status">{tagNotice}</small>}
      </section>
      <section className="metadata-section">
      <div className="detail-section-header"><h3 className="section-title">Metadaten</h3>
        {!editingMetadata && <button type="button" className="detail-action" onClick={beginMetadataEdit}>Metadaten bearbeiten</button>}</div>
      {editingMetadata && <form className="metadata-editor" onSubmit={saveMetadata}>
        <label>Titel<input required maxLength={500} value={metadataForm.title} onChange={event => setMetadataForm(current => ({ ...current, title: event.target.value }))} /></label>
        <label>Autoren (mit Komma trennen)<input maxLength={3000} value={metadataForm.authors} onChange={event => setMetadataForm(current => ({ ...current, authors: event.target.value }))} /></label>
        <div className="metadata-editor-grid"><label>Jahr<input type="number" min="0" max="9999" value={metadataForm.publication_year} onChange={event => setMetadataForm(current => ({ ...current, publication_year: event.target.value }))} /></label><label>Sprache<input maxLength={30} placeholder="de" value={metadataForm.language} onChange={event => setMetadataForm(current => ({ ...current, language: event.target.value }))} /></label><label>Verlag<input maxLength={300} value={metadataForm.publisher} onChange={event => setMetadataForm(current => ({ ...current, publisher: event.target.value }))} /></label></div>
        <div className="metadata-editor-grid"><label>Ausgaben-ISBN<input maxLength={20} value={metadataForm.isbn} onChange={event => setMetadataForm(current => ({ ...current, isbn: event.target.value }))} /></label><label>Referenz-ISBN<input maxLength={20} value={metadataForm.reference_isbn} onChange={event => setMetadataForm(current => ({ ...current, reference_isbn: event.target.value }))} /></label><label>Reihe<input maxLength={300} value={metadataForm.series} onChange={event => setMetadataForm(current => ({ ...current, series: event.target.value }))} /></label></div>
        <label>Beschreibung<textarea maxLength={100000} rows={5} value={metadataForm.description} onChange={event => setMetadataForm(current => ({ ...current, description: event.target.value }))} /></label>
        <div className="detail-form-actions"><button className="detail-action" disabled={metadataSaving}>{metadataSaving ? 'Speichert…' : 'Speichern'}</button><button type="button" className="detail-action quiet" onClick={() => setEditingMetadata(false)}>Abbrechen</button></div>
      </form>}
      {metadataNotice && <p className="tag-notice" role="status">{metadataNotice}</p>}
      <dl className="metadata-list">{fields.map(([name, value, source]) => <div key={name} className={name === 'Sprache' ? 'metadata-language-row' : name === 'Titel' ? 'metadata-title-row' : undefined}>
        <dt>{name}</dt><dd className={name === 'Sprache' ? 'metadata-language-value' : name === 'Titel' ? 'metadata-title-value' : undefined}>{value}
          {name === 'Titel' && <button type="button" className="detail-action" disabled={titleNormalizing || aiTagging || languageDetecting || isbnSearching || Boolean(isbnApplying)} onClick={normalizeTitleNow}>{titleNormalizing ? 'Titel wird bereinigt…' : 'Titel per KI bereinigen'}</button>}
          {name === 'Autor' && nonLatinAuthor && <button type="button" className="detail-action" disabled={authorNormalizing} onClick={normalizeAuthorsNow}>{authorNormalizing ? 'Autor wird abgeglichen…' : 'Autor mit Katalog abgleichen'}</button>}
          {name === 'Sprache' && <button type="button" className="detail-action" disabled={languageDetecting || aiTagging || titleNormalizing || isbnSearching || Boolean(isbnApplying)} onClick={findLanguage}>{languageDetecting ? 'Sprache wird geprüft…' : 'Sprache prüfen'}</button>}
        </dd><small>{source || '—'}</small>
        {name === 'Titel' && titleNotice && <div className="metadata-language-feedback"><p role="status">{titleNotice}</p></div>}
        {name === 'Autor' && authorNotice && <div className="metadata-language-feedback"><p role="status">{authorNotice}</p></div>}
        {name === 'Sprache' && (languageNotice || book.language_detection) && <div className="metadata-language-feedback">
          {languageNotice && <p role="status">{languageNotice}</p>}
          {book.language_detection && <details><summary>Letzte Sprachprüfung: {book.language_detection.status === 'detected' ? (book.language_detection.source === 'local' ? 'lokal erkannt' : 'per KI erkannt') : 'unklar'}</summary>{book.language_detection.assessments.map(sample => <p key={sample.sample_id}>Probe {sample.sample_id}: {sample.language === 'xx' ? 'unklar / mehrsprachig' : sample.language.toUpperCase()} — {sample.reason}</p>)}</details>}
        </div>}
      </div>)}</dl>
      <button type="button" className="detail-action" onClick={async () => { if (sourceOpen) { setSourceOpen(false); return } try { setSourceRows(await getMetadataSources(book.id)); setSourceError(''); setSourceOpen(true) } catch (err) { setSourceError(err instanceof Error ? err.message : 'Quellenverlauf konnte nicht geladen werden') } }}>Quellenverlauf {sourceOpen ? 'ausblenden' : 'anzeigen'}</button>
      {sourceError && <p className="metadata-language-feedback">{sourceError}</p>}
      {sourceOpen && <div className="metadata-source-history">{sourceRows.length ? sourceRows.map((row, index) => <p key={`${row.field}-${index}`}><strong>{row.field}</strong> · {row.source || 'Herkunft unbekannt'} · {String(Array.isArray(row.value) ? row.value.join(', ') : row.value ?? '—').slice(0, 160)}</p>) : <p>Für dieses Buch wurden noch keine Quellwerte erfasst.</p>}</div>}
      </section>
      <TranslationPanel book={book} finished={() => refreshed(book)} />
      <section className="isbn-resolver">
        <div className="isbn-heading"><div><h3>Werk- und ISBN-Auflösung</h3><p>Eine Ausgaben-ISBN gehört exakt zur Datei. Eine Referenz-ISBN bezeichnet nur dasselbe Werk und dient der späteren Metadatenanreicherung.</p></div><div className="isbn-actions"><button disabled={isbnSearching || Boolean(isbnApplying)} onClick={() => findIsbn(false)}>{isbnSearching ? 'Suche läuft…' : 'Werk suchen'}</button>{(isbnCandidates.length > 0 || isbnNotice) && <button className="quiet" disabled={isbnSearching || Boolean(isbnApplying)} onClick={() => findIsbn(true)}>Neu abfragen</button>}</div></div>
        {isbnNotice && <p className="isbn-notice">{isbnNotice}</p>}
        {book.work_match && <div className="work-match"><strong>Erkanntes Werk: {book.work_match.title}</strong><span>{book.work_match.confidence.toFixed(0)} % · {book.work_match.sources.join(' + ')}</span>{book.work_match.suggested_genres.length > 0 && <p>Katalogvorschläge: {book.work_match.suggested_genres.slice(0, 8).join(', ')}</p>}{book.work_match.work_series.length > 0 && <p>Werkreihe: {book.work_match.work_series.join(', ')}</p>}</div>}
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

function ImportPanel({ jobs, collapsed, toggle, retry }: { jobs: ImportJob[]; collapsed: boolean; toggle: () => void; retry: (jobId: string, itemId: string) => void }) {
  if (!jobs.length) return null
  const counts = jobs.reduce((total, job) => ({
    queued: total.queued + job.queued, active: total.active + job.active,
    review: total.review + job.needs_review, completed: total.completed + job.completed,
  }), { queued: 0, active: 0, review: 0, completed: 0 })
  return <aside className={`import-panel ${collapsed ? 'collapsed' : ''}`}>
    <button className="import-head" onClick={toggle}><span><i /> Import</span><strong>{counts.queued} wartet · {counts.active} aktiv · {counts.review} prüfen · {counts.completed} fertig</strong><b>{collapsed ? '⌃' : '⌄'}</b></button>
    {!collapsed && <div className="import-items">{jobs.flatMap(job => job.items.map(item => ({ job, item }))).slice(-8).map(({ job, item }) => <div className={`import-item ${item.status}${item.warnings.length ? ' with-warnings' : ''}`} key={item.id}>
      <span className="state">{item.warnings.length || item.status === 'failed' ? '!' : item.status === 'finished' ? '✓' : item.status === 'duplicate' ? '↺' : '●'}</span>
      <div><strong>{item.filename}</strong><small title={item.message || ''}>{item.message || eventLabels[item.event] || item.event}</small>{item.failed_steps.length > 0 && <button className="import-retry" onClick={() => retry(job.id, item.id)}>Fehlgeschlagene Schritte wiederholen</button>}</div>
    </div>)}</div>}
  </aside>
}

export default function App({ onLogout }: { onLogout: () => void }) {
  const [books, setBooks] = useState<Book[]>([])
  const [totalBooks, setTotalBooks] = useState(0)
  const [page, setPage] = useState(0)
  const pageSize = 50
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
  const [aiSettingsOpen, setAiSettingsOpen] = useState(false)
  const [backupOpen, setBackupOpen] = useState(false)
  const settingsButtonRef = useRef<HTMLButtonElement>(null)
  const [clearing, setClearing] = useState(false)
  const [notice, setNotice] = useState('')
  const [coverVersions, setCoverVersions] = useState<Record<string, number>>({})
  const dragDepth = useRef(0)
  const listAbort = useRef<AbortController | null>(null)
  const importRefreshTimer = useRef<number | null>(null)
  const completedRefreshes = useRef<Set<string>>(new Set())

  const mergeJobs = useCallback((incoming: ImportJob[]) => {
    setJobs(current => {
      const byId = new Map(current.map(job => [job.id, job]))
      for (const job of incoming) {
        const previous = byId.get(job.id)
        if (!previous || job.revision >= previous.revision) byId.set(job.id, job)
      }
      return [...byId.values()]
    })
  }, [])

  useEffect(() => {
    if (!aiSettingsOpen) return
    document.querySelector<HTMLButtonElement>('.ai-dialog .ai-close')?.focus()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setAiSettingsOpen(false) }
    window.addEventListener('keydown', closeOnEscape)
    return () => { window.removeEventListener('keydown', closeOnEscape); document.body.style.overflow = previousOverflow; settingsButtonRef.current?.focus() }
  }, [aiSettingsOpen])

  const params = useMemo(() => {
    const result = new URLSearchParams()
    if (query.trim()) result.set('q', query.trim())
    else {
      Object.entries(filters).forEach(([key, value]) => value && result.set(key, value))
      result.set('limit', String(pageSize))
      result.set('offset', String(page * pageSize))
    }
    return result
  }, [query, filters, page])

  const filterSignature = useMemo(() => JSON.stringify([query, filters]), [query, filters])
  const previousFilterSignature = useRef(filterSignature)
  useEffect(() => {
    if (previousFilterSignature.current !== filterSignature) {
      previousFilterSignature.current = filterSignature
      setPage(0)
    }
  }, [filterSignature])

  const loadBooks = useCallback(async () => {
    listAbort.current?.abort()
    const controller = new AbortController()
    listAbort.current = controller
    try {
      setError('')
      const result = await getBooks(params, controller.signal)
      setBooks(result.items)
      setTotalBooks(result.total)
    }
    catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        setError(err instanceof Error ? err.message : 'Bibliothek konnte nicht geladen werden')
      }
    }
    finally { setLoading(false) }
  }, [params])

  const loadFilterOptions = useCallback(async () => {
    try { setFilterOptions(await getFilterOptions()) }
    catch (err) { setError(err instanceof Error ? err.message : 'Filter konnten nicht geladen werden') }
  }, [])

  const refreshAfterImport = useCallback(() => {
    if (importRefreshTimer.current) window.clearTimeout(importRefreshTimer.current)
    importRefreshTimer.current = window.setTimeout(() => {
      void Promise.all([loadBooks(), loadFilterOptions()])
    }, 250)
  }, [loadBooks, loadFilterOptions])

  useEffect(() => { const timer = window.setTimeout(loadBooks, 180); return () => clearTimeout(timer) }, [loadBooks])
  useEffect(() => { loadFilterOptions() }, [loadFilterOptions])
  useEffect(() => { void listImports().then(mergeJobs).catch(() => {}) }, [mergeJobs])
  useEffect(() => {
    if (!query.trim() && totalBooks > 0 && page > 0 && page * pageSize >= totalBooks) {
      setPage(Math.max(0, Math.ceil(totalBooks / pageSize) - 1))
    }
  }, [page, query, totalBooks])

  useEffect(() => {
    const source = new EventSource('/api/events')
    const names = Object.keys(eventLabels).filter(name => name.startsWith('import.'))
    const onEvent = async (rawEvent: Event) => {
      const event = rawEvent as MessageEvent
      const payload = JSON.parse(event.data)
      if (payload.import_id) {
        try {
          const job = await getImport(payload.import_id)
          mergeJobs([job])
        } catch { /* job may have vanished after a backend restart */ }
      }
      if (event.type === 'import.archived' || event.type === 'import.finished' || event.type === 'import.duplicate') {
        const marker = `${payload.import_id || payload.book_id}:${event.type}`
        if (!completedRefreshes.current.has(marker)) {
          completedRefreshes.current.add(marker)
          refreshAfterImport()
        }
      }
    }
    source.onopen = () => { void listImports().then(mergeJobs).catch(() => {}) }
    names.forEach(name => source.addEventListener(name, onEvent))
    return () => source.close()
  }, [mergeJobs, refreshAfterImport])

  useEffect(() => {
    const active = jobs.filter(job => job.status === 'queued' || job.status === 'running' || job.status === 'needs_review')
    if (!active.length) return
    const timer = window.setInterval(async () => {
      const updates = await Promise.all(active.map(job => getImport(job.id).catch(() => job)))
      let newlyFinished = false
      setJobs(current => current.map(job => {
        const update = updates.find(candidate => candidate.id === job.id)
        if (update && job.status !== 'finished' && update.status === 'finished' && !completedRefreshes.current.has(`${job.id}:import.finished`)) {
          completedRefreshes.current.add(`${job.id}:import.finished`)
          newlyFinished = true
        }
        return update && update.revision >= job.revision ? update : job
      }))
      if (newlyFinished) {
        refreshAfterImport()
      }
    }, 1200)
    return () => clearInterval(timer)
  }, [jobs, refreshAfterImport])

  async function importFiles(files: File[], direct = false) {
    if (!files.length) { setError('Keine unterstützten E-Books gefunden.'); return }
    let uploaded = 0
    try {
      setError('')
      const limits = await getImportLimits()
      const oversized = files.find(file => file.size > limits.max_bytes)
      if (oversized) {
        throw new Error(`„${oversized.name}“ überschreitet das Uploadlimit von ${Math.floor(limits.max_bytes / 1024 / 1024)} MiB.`)
      }
      const maxFiles = Math.min(limits.max_files, 10)
      const maxBytes = Math.min(limits.max_bytes, 100 * 1024 * 1024)
      let batch: File[] = []
      let batchBytes = 0
      const sendBatch = async () => {
        if (direct) {
          const job = await uploadBooks(batch)
          mergeJobs([job])
          setCollapsed(false)
        } else {
          await uploadPreviews(batch)
          window.dispatchEvent(new Event('goblin-previews-changed'))
        }
        uploaded += batch.length
        batch = []
        batchBytes = 0
      }
      for (const file of files) {
        if (batch.length && (batch.length >= maxFiles || batchBytes + file.size > maxBytes)) await sendBatch()
        batch.push(file)
        batchBytes += file.size
      }
      if (batch.length) await sendBatch()
    } catch (err) {
      const message = err instanceof TypeError
        ? 'Verbindung zum Server beim Upload abgebrochen. Bitte Server und Uploadgröße prüfen.'
        : err instanceof Error ? err.message : 'Upload fehlgeschlagen'
      setError(uploaded ? `${uploaded} von ${files.length} Dateien übertragen. Danach fehlgeschlagen: ${message}` : message)
    }
  }

  async function importSelection(input: HTMLInputElement, direct = false) {
    const files = compatibleBookFiles(input.files || [])
    input.value = ''
    await importFiles(files, direct)
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

  return <div className="app" onDragEnter={event => { event.preventDefault(); dragDepth.current++; setDragging(true) }} onDragLeave={event => { event.preventDefault(); if (--dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false) } }} onDragOver={event => event.preventDefault()} onDrop={async event => { event.preventDefault(); dragDepth.current = 0; setDragging(false); try { await importFiles(await filesFromDrop(event.dataTransfer), true) } catch (err) { setError(err instanceof Error ? err.message : 'Ordner konnte nicht gelesen werden') } }}>
    <header>
      <a className="brand" href="/"><span className="goblin">G</span><span><strong>Goblin</strong><small>ARCHIVAR</small></span></a>
      <div className="settings-wrap">
        <UpdatePrompt />
        <button ref={settingsButtonRef} className="settings" title="Einstellungen" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)}>⚙</button>
        {settingsOpen && <div className="settings-menu">
          <button type="button" className="settings-entry" onClick={() => { setSettingsOpen(false); setAiSettingsOpen(true) }}>KI-Anbindung einrichten <span>→</span></button>
          <button type="button" className="settings-entry" onClick={() => { setSettingsOpen(false); setBackupOpen(true) }}>Backup und Wiederherstellung <span>→</span></button>
          <div className="settings-danger"><button className="danger-button" onClick={onLogout}>Abmelden</button></div>
          <div className="settings-danger">
          <strong>Entwicklung</strong>
          <p>Entfernt alle importierten Bücher und Dateien aus dem lokalen Archiv.</p>
          <button className="danger-button" disabled={clearing} onClick={deleteEverything}>{clearing ? 'Archiv wird geleert…' : 'Gesamtes Archiv leeren'}</button>
          </div>
        </div>}
      </div>
    </header>
    {aiSettingsOpen && <div className="ai-dialog-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) setAiSettingsOpen(false)
    }}><div className="ai-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-settings-title">
      <AiSettingsPanel onClose={() => setAiSettingsOpen(false)} onSaved={() => { setAiSettingsOpen(false); setNotice('KI-Einstellungen gespeichert. Sie gelten sofort für neue Anfragen.') }} />
    </div></div>}
    {backupOpen && <div className="ai-dialog-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) setBackupOpen(false)
    }}><div className="ai-dialog" role="dialog" aria-modal="true" aria-label="Backup und Wiederherstellung"><BackupPanel onClose={() => setBackupOpen(false)} /></div></div>}
    <main>
      <section className="intro">
        <label className="search library-search"><span>⌕</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Titel, Autor, ISBN durchsuchen…" /><kbd>⌘ K</kbd></label>
        <div className="import-actions">
          <label className="add-button"><b aria-hidden="true">＋</b><span>Bücher hinzufügen</span><input type="file" multiple accept=".epub,.pdf,.mobi,.azw3,.fb2" onChange={event => importSelection(event.currentTarget)} /></label>
          <label className="folder-button"><b aria-hidden="true">▣</b><span>Ordner direkt importieren</span><input ref={input => { if (input) { input.webkitdirectory = true; input.setAttribute('directory', '') } }} type="file" multiple accept=".epub,.pdf,.mobi,.azw3,.fb2" onChange={event => importSelection(event.currentTarget, true)} /></label>
        </div>
      </section>
      <PreviewPanel refreshArchive={refreshAfterImport} />
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
      {loading ? <div className="empty">Der Goblin blättert durch das Archiv…</div> : books.length ? <>
        <section className="book-grid">{books.map(book => <BookCard key={book.id} book={book} coverVersion={coverVersions[book.id]} onClick={() => openBook(book.id)} onTagClick={filterByTag} />)}</section>
        {!query.trim() && totalBooks > pageSize && <nav className="pagination" aria-label="Seitennavigation">
          <button disabled={page === 0} onClick={() => setPage(current => Math.max(0, current - 1))}>← Zurück</button>
          <span>Seite {page + 1} von {Math.ceil(totalBooks / pageSize)} · {totalBooks} Bücher</span>
          <button disabled={(page + 1) * pageSize >= totalBooks} onClick={() => setPage(current => current + 1)}>Weiter →</button>
        </nav>}
      </> : <div className="empty"><span>🧌</span><h2>Das Archiv ist noch hungrig</h2><p>Ziehe EPUB-, PDF-, MOBI- oder AZW3-Dateien hierher.</p></div>}
    </main>
    {dragging && <div className="drop-overlay"><div><span>🧌</span><h2>Bücher dem Goblin verfüttern</h2><p>Dateien oder Ordner hier ablegen</p></div></div>}
    <ImportPanel jobs={jobs} collapsed={collapsed} toggle={() => setCollapsed(!collapsed)} retry={(jobId, itemId) => {
      void retryImportStep(jobId, itemId).then(job => mergeJobs([job])).catch(err => setError(err instanceof Error ? err.message : 'Wiederholung fehlgeschlagen'))
    }} />
    {selected && <Detail book={selected} tagOptions={filterOptions.tags.map(tag => tag.label)} close={closeDetail} filterByTag={filterByTag} refreshed={book => { setSelected(book); setBooks(current => current.map(item => item.id === book.id ? book : item)); setCoverVersions(current => ({ ...current, [book.id]: Date.now() })); loadBooks(); loadFilterOptions() }} />}
  </div>
}
