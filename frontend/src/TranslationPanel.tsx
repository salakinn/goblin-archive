import { useEffect, useState } from 'react'
import { createTranslation, getTranslation, getTranslationSegments, listGlossaries, listTranslations, repairTranslationSegment, saveTranslationBudget, saveTranslationGlossary, translationAction } from './api'
import type { Book, TranslationGlossary, TranslationJob, TranslationSegments } from './types'

const statusLabels: Record<string, string> = {
  ready: 'Vorbereitet', preview: 'Vorschau läuft', awaiting_glossary: 'Vorschau bereit',
  translating: 'Übersetzung läuft', assembling: 'EPUB wird geprüft', paused: 'Pausiert',
  failed: 'Fehlgeschlagen', completed: 'Fertig', cancelled: 'Abgebrochen',
}
const visibleText = (value: string) => value.replace(/\[\[\/?\d+\]\]/g, '')

export function TranslationPanel({ book, finished }: { book: Book; finished: () => void }) {
  const [jobs, setJobs] = useState<TranslationJob[]>([])
  const [language, setLanguage] = useState(book.language === 'de' ? 'en' : 'de')
  const [profile, setProfile] = useState('buch')
  const [budget, setBudget] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [glossary, setGlossary] = useState('')
  const [style, setStyle] = useState('')
  const [glossaries, setGlossaries] = useState<TranslationGlossary[]>([])
  const [glossaryId, setGlossaryId] = useState('')
  const [segmentOffset, setSegmentOffset] = useState(0)
  const [segmentPage, setSegmentPage] = useState<TranslationSegments | null>(null)
  const active = jobs[0]
  useEffect(() => { setSegmentOffset(0); setSegmentPage(null) }, [active?.id])
  useEffect(() => {
    let cancelled = false
    listTranslations(book.id).then(items => { if (!cancelled) setJobs(items) }).catch(err => { if (!cancelled) setNotice(String(err)) })
    return () => { cancelled = true }
  }, [book.id])
  useEffect(() => { listGlossaries().then(setGlossaries).catch(() => undefined) }, [])
  useEffect(() => {
    if (!active || !['paused', 'failed', 'awaiting_glossary'].includes(active.status)) return
    getTranslationSegments(active.id, segmentOffset).then(setSegmentPage).catch(() => setSegmentPage(null))
  }, [active?.id, active?.status, segmentOffset])
  useEffect(() => {
    if (!active || !['preview', 'translating', 'assembling'].includes(active.status)) return
    const timer = window.setInterval(() => {
      getTranslation(active.id).then(updated => {
        setJobs(current => current.map(item => item.id === updated.id ? updated : item))
        if (updated.status === 'completed') finished()
      }).catch(err => setNotice(String(err)))
    }, 1800)
    return () => window.clearInterval(timer)
  }, [active?.id, active?.status, finished])
  async function create() {
    try {
      setBusy(true); setNotice('')
      const job = await createTranslation(book.id, language, profile, budget ? Number(budget) : null, glossaryId ? Number(glossaryId) : null)
      setJobs(current => [job, ...current])
    } catch (err) { setNotice(err instanceof Error ? err.message : 'Auftrag konnte nicht erstellt werden') }
    finally { setBusy(false) }
  }
  async function act(action: 'start' | 'pause' | 'cancel') {
    if (!active) return
    try {
      setBusy(true); setNotice('')
      const job = await translationAction(active.id, action)
      setJobs(current => current.map(item => item.id === job.id ? job : item))
    } catch (err) { setNotice(err instanceof Error ? err.message : 'Aktion fehlgeschlagen') }
    finally { setBusy(false) }
  }
  async function saveGlossary() {
    if (!active) return
    try {
      setBusy(true); setNotice('')
      const entries = glossary.split('\n').filter(line => line.trim()).map(line => {
        const [source, ...rest] = line.split('=>')
        if (!source.trim() || !rest.join('=>').trim()) throw new Error('Glossar: eine Zeile pro Begriff, Format Original => Übersetzung')
        return { source: source.trim(), target: rest.join('=>').trim() }
      })
      const job = await saveTranslationGlossary(active.id, entries, style)
      setJobs(current => current.map(item => item.id === job.id ? job : item))
      setNotice('Glossar gespeichert.')
    } catch (err) { setNotice(err instanceof Error ? err.message : 'Glossar konnte nicht gespeichert werden') }
    finally { setBusy(false) }
  }
  async function saveBudget() {
    if (!active) return
    try {
      setBusy(true); setNotice('')
      const job = await saveTranslationBudget(active.id, budget ? Number(budget) : null)
      setJobs(current => current.map(item => item.id === job.id ? job : item))
      setNotice('Budget gespeichert.')
    } catch (err) { setNotice(err instanceof Error ? err.message : 'Budget konnte nicht gespeichert werden') }
    finally { setBusy(false) }
  }
  async function repair(segmentId: string) {
    if (!active) return
    try {
      setBusy(true); setNotice('')
      const updated = await repairTranslationSegment(active.id, segmentId)
      setJobs(current => current.map(item => item.id === updated.id ? updated : item))
      setSegmentPage(await getTranslationSegments(active.id, segmentOffset))
      setNotice('Segment wurde erneut durch alle Profilstufen bearbeitet.')
    } catch (err) { setNotice(err instanceof Error ? err.message : 'Reparatur fehlgeschlagen') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (active) { setGlossary(active.glossary.map(item => `${item.source} => ${item.target}`).join('\n')); setStyle(active.style); setBudget(active.budget_usd?.toString() || '') }
  }, [active?.id])
  if (book.format !== 'epub') return null
  return <section className="translation-panel">
    <h3 className="section-title">Buch übersetzen</h3>
    {!active || ['completed', 'cancelled'].includes(active.status) ? <div className="translation-controls">
      <label>Zielsprache <select value={language} onChange={e => setLanguage(e.target.value)}><option value="de">Deutsch</option><option value="en">Englisch</option><option value="fr">Französisch</option><option value="es">Spanisch</option><option value="it">Italienisch</option></select></label>
      <label>Profil <select value={profile} onChange={e => setProfile(e.target.value)}><option value="schnell">Schnell</option><option value="buch">Buch</option><option value="literarisch">Literarisch</option></select></label>
      <label>Budgetgrenze (USD, optional) <input type="number" min="0.01" step="0.01" value={budget} onChange={e => setBudget(e.target.value)} /></label>
      <label>Glossar (optional) <select value={glossaryId} onChange={e => setGlossaryId(e.target.value)}><option value="">Keines</option>{glossaries.filter(item => item.source_language === (book.language || '') && item.target_language === language).map(item => <option key={item.id} value={item.id}>{item.name} (v{item.version})</option>)}</select></label>
      <button disabled={busy || language === book.language} onClick={create}>Übersetzung vorbereiten</button>
    </div> : null}
    {active && <div className="translation-job">
      <p><strong>{active.target_language.toUpperCase()} · {active.profile}</strong> · {statusLabels[active.status] || active.status} · {active.completed_segments}/{active.total_segments} Texte</p>
      <p>Geschätzte Kosten: {active.estimated_cost_usd == null ? 'Preis nicht konfiguriert' : `$${active.estimated_cost_usd.toFixed(4)}`} · bisher: ${active.cost_usd.toFixed(4)}</p>
      {active.error && <p role="alert">{active.error}</p>}
      {active.status === 'ready' && <button disabled={busy} onClick={() => act('start')}>Erstes Kapitel übersetzen</button>}
      {active.status === 'awaiting_glossary' && <>
        <h4>Vorschau des ersten Kapitels</h4>
        <div className="translation-preview">{active.preview.map((item, index) => <div key={index}><p>{visibleText(item.source)}</p><strong>{visibleText(item.translated)}</strong></div>)}</div>
        <label>Glossar (Original =&gt; Übersetzung, je Zeile)<textarea value={glossary} onChange={e => setGlossary(e.target.value)} rows={4} /></label>
        <label>Stilvorgaben<textarea value={style} onChange={e => setStyle(e.target.value)} rows={3} /></label>
        <button disabled={busy} onClick={saveGlossary}>Glossar speichern</button>{' '}
        <button disabled={busy} onClick={() => act('start')}>Restübersetzung starten</button>
      </>}
      {['ready', 'awaiting_glossary', 'paused', 'failed'].includes(active.status) && <div className="translation-controls"><label>Budgetgrenze (USD, leer = keine) <input type="number" min="0.01" step="0.01" value={budget} onChange={e => setBudget(e.target.value)} /></label><button disabled={busy} onClick={saveBudget}>Budget speichern</button></div>}
      {['preview', 'translating', 'assembling'].includes(active.status) && <button disabled={busy} onClick={() => act('pause')}>Pausieren</button>}
      {['paused', 'failed'].includes(active.status) && <button disabled={busy} onClick={() => act('start')}>Fortsetzen</button>}
      {['paused', 'failed', 'awaiting_glossary'].includes(active.status) && segmentPage && <details className="translation-segments"><summary>Einzelne Textstellen prüfen und reparieren</summary>
        {segmentPage.items.map(segment => <article key={segment.id}><small>{segment.file} · {segment.id}</small><p>{visibleText(segment.source)}</p><strong>{visibleText(segment.translated || segment.draft || 'Noch nicht übersetzt')}</strong><button type="button" disabled={busy || (!segment.translated && !segment.draft && active.status !== 'failed')} onClick={() => repair(segment.id)}>Erneut übersetzen</button></article>)}
        <div><button type="button" disabled={busy || segmentOffset === 0} onClick={() => setSegmentOffset(Math.max(0, segmentOffset - 50))}>Zurück</button>{' '}<span>{segmentOffset + 1}–{Math.min(segmentOffset + 50, segmentPage.total)} von {segmentPage.total}</span>{' '}<button type="button" disabled={busy || segmentOffset + 50 >= segmentPage.total} onClick={() => setSegmentOffset(segmentOffset + 50)}>Weiter</button></div>
      </details>}
      {!['completed', 'cancelled'].includes(active.status) && <button className="quiet" disabled={busy} onClick={() => act('cancel')}>Abbrechen</button>}
      {active.output_book_id && <a href={`/api/books/${active.output_book_id}/download`}>Übersetztes EPUB herunterladen</a>}
    </div>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
