import { useEffect, useState } from 'react'
import { getAiSettings, getAiUsage, saveAiSettings, testAiSettings } from './api'
import type { AiSettings } from './types'

const models = [
  ['ai_tagging_model', 'Tags', 'Kurze Themen- und Genre-Tags'],
  ['ai_language_model', 'Spracherkennung', 'Sprache aus Buchtext erkennen'],
  ['ai_translation_model', 'Übersetzung', 'Flüssige Übersetzung des Buchtexts'],
  ['ai_translation_qa_model', 'Übersetzungsprüfung', 'Fehler und Auslassungen finden'],
  ['ai_translation_editor_model', 'Literarisches Lektorat', 'Stil und Lesbarkeit überarbeiten'],
] as const

const modelOptions = ['gpt-5.4-nano', 'gpt-5.4-mini', 'gpt-5.4']

const prices = [
  ['ai_tagging_input_usd_per_million', 'Tags: Eingabe'],
  ['ai_tagging_output_usd_per_million', 'Tags: Ausgabe'],
  ['ai_language_input_usd_per_million', 'Spracherkennung: Eingabe'],
  ['ai_language_output_usd_per_million', 'Spracherkennung: Ausgabe'],
  ['ai_translation_input_usd_per_million', 'Übersetzung: Eingabe'],
  ['ai_translation_output_usd_per_million', 'Übersetzung: Ausgabe'],
  ['ai_translation_qa_input_usd_per_million', 'Prüfung: Eingabe'],
  ['ai_translation_qa_output_usd_per_million', 'Prüfung: Ausgabe'],
  ['ai_translation_editor_input_usd_per_million', 'Lektorat: Eingabe'],
  ['ai_translation_editor_output_usd_per_million', 'Lektorat: Ausgabe'],
] as const

export function AiSettingsPanel() {
  const [form, setForm] = useState<AiSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [notice, setNotice] = useState('')
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof getAiUsage>> | null>(null)
  useEffect(() => {
    let active = true
    getAiSettings().then(settings => { if (active) setForm(settings) })
      .catch(err => { if (active) setNotice(err instanceof Error ? err.message : 'KI-Einstellungen konnten nicht geladen werden.') })
    return () => { active = false }
  }, [])
  useEffect(() => { getAiUsage().then(setUsage).catch(() => undefined) }, [])

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!form) return
    try {
      setBusy(true); setNotice('')
      const { provider: _provider, key_configured: _keyConfigured, ...values } = form
      const saved = await saveAiSettings({ ...values, ...(apiKey ? { api_key: apiKey } : {}) })
      setForm(saved); setApiKey('')
      setNotice('KI-Einstellungen gespeichert. Sie gelten sofort für neue Anfragen.')
    } catch (err) { setNotice(err instanceof Error ? err.message : 'Speichern fehlgeschlagen.') }
    finally { setBusy(false) }
  }

  async function removeKey() {
    try {
      setBusy(true); setNotice('')
      setForm(await saveAiSettings({ api_key: null }))
      setApiKey('')
      setNotice('API-Key entfernt.')
    } catch (err) { setNotice(err instanceof Error ? err.message : 'API-Key konnte nicht entfernt werden.') }
    finally { setBusy(false) }
  }

  async function testConnection() {
    try {
      setTesting(true); setNotice('')
      const result = await testAiSettings()
      setNotice(`Verbindung erfolgreich. Modell: ${result.model}`)
    } catch (err) { setNotice(err instanceof Error ? err.message : 'Verbindung fehlgeschlagen.') }
    finally { setTesting(false) }
  }

  return <section className="ai-settings">
    <h3>KI-Anbindung</h3>
    {!form ? <p>{notice || 'Einstellungen werden geladen…'}</p> : <form onSubmit={save}>
      <p>Anbieter: OpenAI · API-Key {form.key_configured ? 'hinterlegt' : 'fehlt'}</p>
      <label>Neuer API-Key
        <input type="password" autoComplete="new-password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={form.key_configured ? 'Leer lassen, um den Key zu behalten' : 'sk-…'} />
      </label>
      {form.key_configured && <button type="button" className="settings-secondary" disabled={busy || testing} onClick={removeKey}>API-Key entfernen</button>}
      <p>Modell je Aufgabe wählen oder einen eigenen Modellnamen eingeben. Größere Modelle kosten meist mehr.</p>
      <datalist id="ai-model-options">{modelOptions.map(model => <option key={model} value={model} />)}</datalist>
      <div className="ai-models">{models.map(([name, label, hint]) => <label key={name}>{label}
        <small>{hint}</small>
        <input required maxLength={120} list="ai-model-options" value={form[name]} onChange={event => setForm(current => current && { ...current, [name]: event.target.value })} />
      </label>)}</div>
      <details><summary>Tokenpreise für Schätzung und Budget</summary>
        <p>USD pro 1 Million Tokens. Für Kostenlimits brauchen alle verwendeten Modelle Preise. Leere Felder für Prüfung und Lektorat verwenden die Übersetzungspreise.</p>
        <div className="ai-prices">{prices.map(([name, label], index) => <label key={name}>{label}
          <input type="number" min="0" step="any" required={index < 6} value={form[name] ?? ''}
            onChange={event => setForm(current => current && { ...current, [name]: event.target.value === '' ? (index < 6 ? 0 : null) : Number(event.target.value) })} />
        </label>)}</div>
      </details>
      <details><summary>Kostenlimits und Verbrauch</summary>
        <p>0 deaktiviert das jeweilige Limit. Erfasste Kosten: heute {usage?.day_usd.toFixed(4) ?? '…'} USD, diesen Monat {usage?.month_usd.toFixed(4) ?? '…'} USD. Für laufende Anfragen reserviert: {usage?.reserved_usd.toFixed(4) ?? '…'} USD.</p>
        {usage && (usage.warning.day || usage.warning.month) && <p role="alert">Mindestens ein KI-Limit ist zu {form.ai_warning_percent}% erreicht.</p>}
        {usage && <ul>{Object.entries(usage.by_feature).map(([feature, item]) => <li key={feature}>{feature}: ${item.cost_usd.toFixed(4)} · {item.requests} Anfragen{item.unpriced_requests ? ` · ${item.unpriced_requests} mit unbekannten Kosten` : ''}</li>)}</ul>}
        <div className="ai-prices"><label>Tageslimit (USD)
          <input type="number" min="0" step="any" value={form.ai_daily_limit_usd} onChange={event => setForm(current => current && { ...current, ai_daily_limit_usd: Number(event.target.value) })} />
        </label><label>Monatslimit (USD)
          <input type="number" min="0" step="any" value={form.ai_monthly_limit_usd} onChange={event => setForm(current => current && { ...current, ai_monthly_limit_usd: Number(event.target.value) })} />
        </label></div>
        <label>Warnschwelle (% des Limits)
          <input type="number" min="1" max="100" step="1" value={form.ai_warning_percent} onChange={event => setForm(current => current && { ...current, ai_warning_percent: Number(event.target.value) })} />
        </label>
      </details>
      <div className="ai-settings-actions">
        <button type="submit" disabled={busy || testing}>{busy ? 'Speichert…' : 'Speichern'}</button>
        <button type="button" className="settings-secondary" disabled={busy || testing || !form.key_configured} onClick={testConnection}>{testing ? 'Prüft…' : 'Verbindung prüfen'}</button>
      </div>
      {notice && <p role="status" className="ai-settings-notice">{notice}</p>}
    </form>}
  </section>
}
