import { useEffect, useState } from 'react'
import App from './App'
import { setCsrfToken } from './api'

type AuthStatus = { configured: boolean; authenticated: boolean; csrf_token: string | null }

export default function AuthGate() {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch('/api/auth/status', { cache: 'no-store' }).then(response => response.json())
      .then((next: AuthStatus) => { setCsrfToken(next.csrf_token || ''); setStatus(next) })
      .catch(() => setError('Der Dienst ist derzeit nicht erreichbar.'))
    const expired = () => { setCsrfToken(''); setStatus(current => current ? { ...current, authenticated: false, csrf_token: null } : current) }
    window.addEventListener('goblin-unauthorized', expired)
    return () => window.removeEventListener('goblin-unauthorized', expired)
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const response = await fetch(status?.configured ? '/api/auth/login' : '/api/auth/setup', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(status?.configured ? { password } : { code, password }),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(body?.detail?.message || 'Anmeldung fehlgeschlagen.')
      setCsrfToken(body.csrf_token)
      setStatus(body)
      setPassword('')
      setCode('')
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Anmeldung fehlgeschlagen.') }
    finally { setBusy(false) }
  }

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': status?.csrf_token || '' } })
      .catch(() => undefined)
    setCsrfToken('')
    setStatus({ configured: true, authenticated: false, csrf_token: null })
  }

  if (status?.authenticated) return <App onLogout={logout} />
  return <div className="auth-page"><div className="auth-card">
    <div className="auth-brand"><span className="goblin">G</span><strong>Goblin Archivar</strong></div>
    <h1>{status?.configured ? 'Anmelden' : 'Administrator einrichten'}</h1>
    {!status ? <p>{error || 'Verbindung wird geprüft…'}</p> : <>
      {!status.configured && <p>Den einmaligen Setup-Code findest du auf dem Server in <code>auth-setup-code</code> im Goblin-Datenverzeichnis.</p>}
      <form onSubmit={submit}>
        {!status.configured && <label>Setup-Code<input type="text" autoComplete="off" value={code} onChange={event => setCode(event.target.value)} required /></label>}
        <label>Admin-Passwort<input type="password" autoComplete={status.configured ? 'current-password' : 'new-password'} minLength={status.configured ? 1 : 12} value={password} onChange={event => setPassword(event.target.value)} required /></label>
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button disabled={busy}>{busy ? 'Bitte warten…' : status.configured ? 'Anmelden' : 'Einrichten'}</button>
      </form>
    </>}
  </div></div>
}
