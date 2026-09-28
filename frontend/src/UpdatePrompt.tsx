import { useCallback, useEffect, useState } from 'react'
import { getUpdateStatus, installUpdate } from './api'
import type { UpdateStatus } from './types'

const IGNORE_KEY = 'goblin-update-ignored'
const REMIND_KEY = 'goblin-update-remind'
const DAY = 24 * 60 * 60 * 1000

function shouldShow(version: string) {
  if (localStorage.getItem(IGNORE_KEY) === version) return false
  try {
    const reminder = JSON.parse(localStorage.getItem(REMIND_KEY) || 'null') as { version?: string; until?: number } | null
    return reminder?.version !== version || !reminder.until || Date.now() >= reminder.until
  } catch { return true }
}

export function UpdatePrompt() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const check = useCallback(async () => {
    try {
      const next = await getUpdateStatus()
      setStatus(next)
      if (next.available && next.latest_version && shouldShow(next.latest_version)) setOpen(true)
    } catch { /* the release service may be temporarily unavailable */ }
  }, [])

  useEffect(() => {
    check()
    const timer = window.setInterval(check, 15 * 60 * 1000)
    return () => clearInterval(timer)
  }, [check])

  function defer(ignore: boolean) {
    if (!status?.latest_version) return
    if (ignore) localStorage.setItem(IGNORE_KEY, status.latest_version)
    else localStorage.setItem(REMIND_KEY, JSON.stringify({ version: status.latest_version, until: Date.now() + DAY }))
    setOpen(false)
  }

  async function install() {
    if (!password) { setMessage('Bitte das Update-Passwort eingeben.'); return }
    setBusy(true)
    setMessage('Update wird gestartet…')
    try {
      const result = await installUpdate(password)
      setPassword('')
      setMessage('Das Update wurde gestartet. Die Seite lädt nach dem Neustart neu.')
      const target = result.latest_version
      const timer = window.setInterval(async () => {
        try {
          const response = await fetch('/api/health', { cache: 'no-store' })
          const health = await response.json() as { version: string }
          if (health.version === target) { window.clearInterval(timer); window.location.reload() }
        } catch { /* app is restarting */ }
      }, 3000)
      window.setTimeout(() => { window.clearInterval(timer); setBusy(false); setMessage('Bitte den Update-Dienst prüfen und diese Seite neu laden.') }, 5 * 60 * 1000)
    } catch (error) {
      setBusy(false)
      setMessage(error instanceof Error ? error.message : 'Update konnte nicht gestartet werden.')
    }
  }

  if (!status?.available || !status.latest_version) return null
  return <>
    <button type="button" className="update-chip" onClick={() => setOpen(true)}>Update</button>
    {open && <div className="update-backdrop" role="presentation">
      <section className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-title">
        <span className="eyebrow">Neue Version</span>
        <h2 id="update-title">Goblin Archivar {status.latest_version}</h2>
        <p>Installiert: {status.current_version}. Beim Update startet die Anwendung neu.</p>
        {status.install_ready ? <label>Update-Passwort
          <input type="password" autoComplete="off" value={password} disabled={busy} onChange={event => setPassword(event.target.value)} />
        </label> : <p>Für diese Installation ist der Update-Knopf noch nicht eingerichtet oder das neue Image ist noch nicht verfügbar. <a href="https://github.com/salakinn/goblin-archive#versionen-und-updates" target="_blank" rel="noreferrer">Installationsanleitung öffnen</a></p>}
        <a href={`https://github.com/salakinn/goblin-archive/releases/tag/${status.latest_version}`} target="_blank" rel="noreferrer">Änderungen ansehen</a>
        {message && <p role="status">{message}</p>}
        <div className="update-actions">
          <button type="button" className="update-primary" disabled={busy || !status.install_ready} onClick={install}>{busy ? 'Installiert…' : 'Update installieren'}</button>
          <button type="button" disabled={busy} onClick={() => defer(false)}>Später erinnern</button>
          <button type="button" disabled={busy} onClick={() => defer(true)}>Diese Version ignorieren</button>
        </div>
      </section>
    </div>}
  </>
}
