import { useEffect, useState } from 'react'
import { acceptPreviewTitle, decidePreviewDuplicate, editPreview, listPreviews, previewAction, suggestPreviewTitle } from './api'
import type { PreviewTitleSuggestion } from './api'
import type { ImportPreview } from './types'

const labels: Record<string, string> = {
  title: 'Titel', authors: 'Autoren', publication_year: 'Jahr', language: 'Sprache',
  publisher: 'Verlag', isbn: 'ISBN', series: 'Reihe', description: 'Beschreibung', genres: 'Tags',
}
const fields = Object.keys(labels)
const sourceLabel: Record<string, string> = { embedded: 'Datei', manual: 'Manuell', filename: 'Dateiname' }

function valueOf(preview: ImportPreview, name: string): string {
  const value = preview.metadata?.[name]?.value
  return Array.isArray(value) ? value.join(', ') : value == null ? '' : String(value)
}

function PreviewCard({ preview, changed, archived }: { preview: ImportPreview; changed: () => void; archived: () => void }) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [suggestion, setSuggestion] = useState<PreviewTitleSuggestion | null>(null)
  useEffect(() => { setSuggestion(null) }, [preview.revision])
  useEffect(() => {
    if (!dirty) setDraft(Object.fromEntries(fields.map(name => [name, valueOf(preview, name)])))
  }, [preview.revision, preview.status, dirty])

  async function action(name: 'confirm' | 'discard' | 'enrich' | 'reanalyze' | 'cover/search' | 'skip' | 'resume') {
    setBusy(true); setError('')
    try {
      await previewAction(preview.id, name)
      if (name === 'confirm') archived()
      changed()
    } catch (err) { setError(err instanceof Error ? err.message : 'Aktion fehlgeschlagen') }
    finally { setBusy(false) }
  }

  async function save() {
    const changes: Record<string, unknown> = {}
    for (const name of fields) {
      if ((draft[name] ?? '') === valueOf(preview, name)) continue
      const raw = (draft[name] || '').trim()
      changes[name] = name === 'authors' || name === 'genres' ? raw.split(',').map(x => x.trim()).filter(Boolean)
        : name === 'publication_year' ? (raw ? Number(raw) : null) : raw || null
    }
    if (!Object.keys(changes).length) { setDirty(false); return }
    setBusy(true); setError('')
    try { await editPreview(preview.id, preview.revision, changes); setDirty(false); changed() }
    catch (err) { setError(err instanceof Error ? err.message : 'Speichern fehlgeschlagen') }
    finally { setBusy(false) }
  }

  async function chooseCover(choice: 'none' | 'embedded' | 'external') {
    setBusy(true); setError('')
    try { await editPreview(preview.id, preview.revision, {}, choice); changed() }
    catch (err) { setError(err instanceof Error ? err.message : 'Cover konnte nicht ausgewählt werden') }
    finally { setBusy(false) }
  }

  async function decide(action: 'keep_both' | 'use_existing', bookId?: string) {
    setBusy(true); setError('')
    try {
      await decidePreviewDuplicate(preview.id, preview.revision, action, bookId)
      if (action === 'use_existing') archived()
      changed()
    } catch (err) { setError(err instanceof Error ? err.message : 'Entscheidung fehlgeschlagen') }
    finally { setBusy(false) }
  }

  async function checkTitle() {
    setBusy(true); setError(''); setSuggestion(null)
    try { setSuggestion(await suggestPreviewTitle(preview.id, preview.revision, valueOf(preview, 'title'))) }
    catch (err) { setError(err instanceof Error ? err.message : 'KI-Prüfung fehlgeschlagen') }
    finally { setBusy(false) }
  }

  async function acceptTitle() {
    if (!suggestion) return
    setBusy(true); setError('')
    try { await acceptPreviewTitle(preview.id, suggestion); setSuggestion(null); changed() }
    catch (err) { setError(err instanceof Error ? err.message : 'Vorschlag konnte nicht übernommen werden') }
    finally { setBusy(false) }
  }

  return <article className="preview-card">
    <div className="preview-head"><strong>{preview.filename}</strong><span>{preview.status === 'ready' ? 'Bereit' : preview.status === 'queued' || preview.status === 'analyzing' ? 'Wird analysiert…' : preview.status === 'cover_search' ? 'Cover-Suche…' : preview.status === 'archiving' ? 'Wird archiviert…' : preview.status === 'archived' ? 'Archiviert' : preview.status === 'failed' ? 'Analyse fehlgeschlagen' : preview.status === 'skipped' ? 'Übersprungen' : 'Verworfen'}</span></div>
    {preview.duplicate_book_id && <p className="preview-warning">Duplikat: Diese Datei ist bereits im Archiv ({preview.duplicate_book_id}).</p>}
    {preview.duplicate_book_id && preview.status === 'ready' && <button disabled={busy || dirty} onClick={() => decide('use_existing', preview.duplicate_book_id!)}>Vorhandenes Buch verwenden</button>}
    {preview.duplicate_preview_id && <p className="preview-warning">Duplikat einer anderen Vorschau ({preview.duplicate_preview_id}).</p>}
    {preview.similar_previews?.map(match => <p className="preview-warning" key={match.preview_id}>Ähnliche frühere Vorschau: {match.filename} · {match.reasons.join('; ')}. Bitte diese Vorschau zuerst bearbeiten.</p>)}
    {preview.duplicate_matches?.length > 0 && <div className="preview-warning"><strong>Ähnliche Bücher im Archiv</strong>
      {preview.duplicate_matches.map(match => <p key={match.book_id}><a href={`/books/${match.book_id}`}>{match.title}</a> ({match.format}, {match.authors.join(', ')}) · {match.reasons.join('; ')}{match.conflicts.length > 0 && ` · Unterschiede: ${match.conflicts.join('; ')}`}
        {preview.status === 'ready' && <button disabled={busy || dirty} onClick={() => decide('use_existing', match.book_id)}>Vorhandenes verwenden</button>}</p>)}
      {preview.status === 'ready' && <button disabled={busy || dirty} onClick={() => decide('keep_both')}>Beide behalten</button>}
      {preview.duplicate_decision?.action === 'keep_both' && <small>Beide behalten wurde bestätigt.</small>}
    </div>}
    {preview.fingerprint && preview.fingerprint.status !== 'complete' && <p className="preview-hint">Inhaltsprüfung: {preview.fingerprint.reason || preview.fingerprint.status}</p>}
    {preview.error && <p className="preview-warning">{preview.error}</p>}
    {preview.status === 'ready' && <>
      <div className="preview-content">
        <div className="preview-fields">{fields.map(name => <label key={name}><span>{labels[name]} <small>{sourceLabel[preview.metadata?.[name]?.source || ''] || preview.metadata?.[name]?.source || 'Unbekannt'}</small></span>
          {name === 'description' ? <textarea value={draft[name] ?? valueOf(preview, name)} onChange={event => { setDraft({ ...draft, [name]: event.target.value }); setDirty(true) }} />
            : <input value={draft[name] ?? valueOf(preview, name)} onChange={event => { setDraft({ ...draft, [name]: event.target.value }); setDirty(true) }} />}
        </label>)}</div>
        <div className="preview-cover">{preview.cover_selected && preview.cover ? <img src={`/api/import/previews/${preview.id}/cover?v=${preview.revision}`} alt={`Cover von ${preview.filename}`} /> : <div className="preview-no-cover">Kein Cover ausgewählt</div>}
          <small>{preview.cover?.source === 'embedded' ? 'Aus Datei' : preview.cover?.provider || ''}</small>
          {preview.embedded_cover && <div className="preview-cover-option"><img src={`/api/import/previews/${preview.id}/cover?choice=embedded&v=${preview.revision}`} alt="Eingebettetes Cover" /><button disabled={busy} onClick={() => chooseCover('embedded')}>Datei-Cover wählen</button></div>}
          {preview.external_cover && <div className="preview-cover-option"><img src={`/api/import/previews/${preview.id}/cover?choice=external&v=${preview.revision}`} alt="Gefundenes Cover" /><button disabled={busy} onClick={() => chooseCover('external')}>Suchergebnis wählen</button></div>}
          <button disabled={busy} onClick={() => action('cover/search')}>Cover suchen</button>
          {preview.cover_selected && <button disabled={busy} onClick={() => chooseCover('none')}>Ohne Cover</button>}
        </div>
      </div>
      <div className="preview-actions"><button disabled={busy || !dirty} onClick={save}>Korrekturen speichern</button><button disabled={busy || dirty} onClick={checkTitle}>Titel mit KI prüfen</button><button disabled={busy || dirty} onClick={() => action('enrich')}>Externe Metadaten suchen</button><button disabled={busy || dirty} onClick={() => action('reanalyze')}>Neu analysieren</button><button disabled={busy || dirty || Boolean(preview.duplicate_book_id || preview.duplicate_preview_id || preview.similar_previews?.length) || (preview.duplicate_matches?.length > 0 && preview.duplicate_decision?.action !== 'keep_both')} onClick={() => action('confirm')}>Archivieren</button><button disabled={busy} onClick={() => action('skip')}>Später</button><button disabled={busy} onClick={() => action('discard')}>Verwerfen</button></div>
      {dirty && <small className="preview-hint">Vor dem Archivieren Korrekturen speichern.</small>}
      {suggestion && <div className="preview-warning"><p>Bisher: {suggestion.original_title}</p><p>Vorschlag: {suggestion.suggested_title}</p><button disabled={busy || dirty || suggestion.revision !== preview.revision} onClick={acceptTitle}>Übernehmen</button><button disabled={busy} onClick={() => setSuggestion(null)}>Verwerfen</button></div>}
    </>}
    {preview.status === 'failed' && <div className="preview-actions"><button disabled={busy} onClick={() => action('reanalyze')}>Erneut analysieren</button><button disabled={busy} onClick={() => action('discard')}>Verwerfen</button></div>}
    {preview.status === 'skipped' && <div className="preview-actions"><button disabled={busy} onClick={() => action('resume')}>Weiter bearbeiten</button><button disabled={busy} onClick={() => action('discard')}>Verwerfen</button></div>}
    {error && <p className="preview-warning">{error}</p>}
  </article>
}

export function PreviewPanel({ refreshArchive }: { refreshArchive: () => void }) {
  const [items, setItems] = useState<ImportPreview[]>([])
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const refresh = () => { void listPreviews().then(setItems).catch(err => setError(err instanceof Error ? err.message : 'Vorschauen konnten nicht geladen werden')) }
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 2500); return () => window.clearInterval(timer) }, [])
  useEffect(() => { const listener = () => { setOpen(true); refresh() }; window.addEventListener('goblin-previews-changed', listener); return () => window.removeEventListener('goblin-previews-changed', listener) }, [])
  const active = items.filter(item => !['archived', 'discarded'].includes(item.status))
  if (!active.length) return null
  const safe = active.filter(item => item.status === 'ready' && !item.duplicate_book_id && !item.duplicate_preview_id && !item.duplicate_matches?.length && !item.similar_previews?.length && !item.edited)
  async function confirmSafe() {
    setBusy(true); setError('')
    try { for (const item of safe) await previewAction(item.id, 'confirm'); refreshArchive(); refresh() }
    catch (err) { setError(err instanceof Error ? err.message : 'Sammelbestätigung fehlgeschlagen'); refresh() }
    finally { setBusy(false) }
  }
  return <section className="preview-panel"><div className="preview-title"><div><h2>Import-Vorschau</h2><p>Prüfe Metadaten und Cover vor dem Archivieren. Vorschauen bleiben 24 Stunden gespeichert.</p></div><div><button disabled={busy || safe.length === 0} onClick={confirmSafe}>Unauffällige archivieren ({safe.length})</button><button onClick={() => setOpen(!open)}>{open ? 'Einklappen' : 'Anzeigen'} ({active.length})</button></div></div>
    {error && <p className="preview-warning">{error}</p>}
    {open && <div className="preview-list">{active.map(item => <PreviewCard key={item.id} preview={item} changed={refresh} archived={refreshArchive} />)}</div>}
  </section>
}
