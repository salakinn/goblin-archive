import { useEffect, useState } from 'react'
import { createBackup, deleteBackup, deletePreviousBackup, listBackups, restoreBackup, uploadBackup } from './api'
import type { BackupJob } from './api'

const bytes = (n?: number) => n === undefined ? '—' : `${(n / 1024 / 1024).toFixed(1)} MiB`

export function BackupPanel({ onClose }: { onClose: () => void }) {
  const [jobs, setJobs] = useState<BackupJob[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<File | null>(null)
  const [prepared, setPrepared] = useState<BackupJob | null>(null)

  useEffect(() => {
    let mounted = true
    const refresh = async () => {
      try {
        const result = await listBackups()
        if (mounted) { setJobs(result); setPrepared(current => current ? result.find(item => item.id === current.id) || null : null) }
      } catch (cause) { if (mounted) setError(cause instanceof Error ? cause.message : 'Aufträge konnten nicht geladen werden') }
    }
    void refresh()
    const interval = setInterval(refresh, 2000)
    return () => { mounted = false; clearInterval(interval) }
  }, [])

  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await operation(); setJobs(await listBackups()) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Aktion fehlgeschlagen') }
    finally { setBusy(false) }
  }

  return <div className="backup-panel">
    <div className="ai-dialog-header"><div><p className="eyebrow">Einstellungen</p><h2>Backup und Wiederherstellung</h2></div><button type="button" onClick={onClose} aria-label="Schließen">×</button></div>
    <div className="ai-dialog-body">
      <section className="backup-section">
        <h3>Vollständiges Backup</h3>
        <p>Enthält Bücher, Cover, Metadaten und Anmeldung. Die Datei ist unverschlüsselt; API-Schlüssel sind ausgeschlossen.</p>
        <button type="button" disabled={busy || jobs.some(job => job.status === 'running' || job.status === 'waiting')} onClick={() => act(createBackup)}>Vollständiges Backup erstellen</button>
      </section>
      <section className="backup-section">
        <h3>Backup wiederherstellen</h3>
        <p>Die Wiederherstellung ersetzt das gesamte Archiv. Das Passwort aus der Sicherung gilt anschließend.</p>
        <input aria-label="Backup-Datei auswählen" type="file" accept=".zip,application/zip" onChange={event => setSelected(event.target.files?.[0] || null)} />
        <button type="button" disabled={!selected || busy} onClick={() => selected && act(async () => { const job = await uploadBackup(selected); setPrepared(job); setSelected(null) })}>{busy ? 'Bitte warten…' : 'Datei hochladen und prüfen'}</button>
        {prepared?.status === 'ready' && <div className="backup-preview"><h4>Geprüftes Backup</h4>
          <p>Sicherung: {prepared.created_backup_at ? new Date(prepared.created_backup_at).toLocaleString('de-DE') : '—'} · Format {prepared.format}</p>
          <p>{prepared.book_count} Bücher · {bytes(prepared.total_bytes)} · Aktuelles Archiv: {prepared.current_book_count} Bücher</p>
          <p><strong>Das aktuelle Archiv wird vollständig ersetzt. Es werden keine Bücher zusammengeführt.</strong></p>
          <button type="button" className="danger-button" disabled={busy} onClick={() => act(async () => { await restoreBackup(prepared.id); setPrepared(null) })}>Ersetzen ausdrücklich bestätigen</button>
        </div>}
      </section>
      {error && <p role="alert" className="auth-error">{error}</p>}
      <section className="backup-section"><h3>Aufträge</h3>
        {jobs.length === 0 && <p>Noch keine Aufträge.</p>}
        {jobs.map(job => <div className="backup-job" key={job.id}>
          <div><strong>{job.kind === 'backup' ? 'Backup' : 'Wiederherstellung'}</strong> · {new Date(job.created_at).toLocaleString('de-DE')}<br />
            <span>{job.phase} {job.total_bytes && job.status === 'running' ? `· ${bytes(job.processed_bytes)} / ${bytes(job.total_bytes)}` : ''}</span>
            {job.error && <p role="alert">{job.error}</p>}
            {job.status === 'done' && <p>{job.book_count} Bücher wiederhergestellt. Anmeldung mit dem Passwort der Sicherung erforderlich. API-Schlüssel bei Bedarf neu einrichten.</p>}
            {job.previous && <p>Rückfallkopie: {bytes(job.previous_bytes)}</p>}
          </div>
          <div className="backup-actions">
            {job.kind === 'backup' && job.status === 'ready' && <a href={`/api/backups/${job.id}/download`}>Backup herunterladen</a>}
            {job.previous && <button type="button" disabled={busy} onClick={() => act(() => deletePreviousBackup(job.id))}>Rückfallkopie löschen</button>}
            {!['running', 'waiting'].includes(job.status) && <button type="button" disabled={busy} onClick={() => act(() => deleteBackup(job.id))}>Auftrag löschen</button>}
          </div>
        </div>)}
      </section>
    </div>
  </div>
}
