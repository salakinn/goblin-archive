import { useEffect, useState } from 'react'
import { getAiSettings, getAiUsage, listAiModels, saveAiSettings, testAiSettings } from './api'
import type { AiModelPrices } from './api'
import type { AiSettings } from './types'

const tasks = [
  ['ai_tagging_model', 'Tags'], ['ai_language_model', 'Spracherkennung'],
  ['ai_translation_model', 'Übersetzung'], ['ai_translation_qa_model', 'Übersetzungsprüfung'],
  ['ai_translation_editor_model', 'Lektorat'],
] as const
const prices = [
  ['ai_tagging_input_usd_per_million', 'Tags: Eingabe'],
  ['ai_tagging_output_usd_per_million', 'Tags: Ausgabe'],
  ['ai_language_input_usd_per_million', 'Sprache: Eingabe'],
  ['ai_language_output_usd_per_million', 'Sprache: Ausgabe'],
  ['ai_translation_input_usd_per_million', 'Übersetzung: Eingabe'],
  ['ai_translation_output_usd_per_million', 'Übersetzung: Ausgabe'],
  ['ai_translation_qa_input_usd_per_million', 'Prüfung: Eingabe'],
  ['ai_translation_qa_output_usd_per_million', 'Prüfung: Ausgabe'],
  ['ai_translation_editor_input_usd_per_million', 'Lektorat: Eingabe'],
  ['ai_translation_editor_output_usd_per_million', 'Lektorat: Ausgabe'],
] as const
const rateBindings = [
  ['ai_tagging_model', 'ai_tagging_input_usd_per_million', 'ai_tagging_output_usd_per_million', 0],
  ['ai_language_model', 'ai_language_input_usd_per_million', 'ai_language_output_usd_per_million', 0],
  ['ai_translation_model', 'ai_translation_input_usd_per_million', 'ai_translation_output_usd_per_million', 0],
  ['ai_translation_qa_model', 'ai_translation_qa_input_usd_per_million', 'ai_translation_qa_output_usd_per_million', null],
  ['ai_translation_editor_model', 'ai_translation_editor_input_usd_per_million', 'ai_translation_editor_output_usd_per_million', null],
] as const

function withCatalogPrices(current: AiSettings, catalog: AiModelPrices,
                           only?: typeof rateBindings[number][0], clearUnknown = false): AiSettings {
  const next = { ...current }
  for (const [modelField, inputField, outputField, missing] of rateBindings) {
    if (only && only !== modelField) continue
    const rate = catalog[current[modelField]]
    if (rate) Object.assign(next, { [inputField]: rate.input_usd_per_million,
                                    [outputField]: rate.output_usd_per_million })
    else if (clearUnknown) Object.assign(next, { [inputField]: missing, [outputField]: missing })
  }
  return next
}

function clearPrices(current: AiSettings): AiSettings {
  return { ...current,
    ai_translation_input_usd_per_million: 0, ai_translation_output_usd_per_million: 0,
    ai_translation_qa_input_usd_per_million: null, ai_translation_qa_output_usd_per_million: null,
    ai_translation_editor_input_usd_per_million: null, ai_translation_editor_output_usd_per_million: null,
    ai_tagging_input_usd_per_million: 0, ai_tagging_output_usd_per_million: 0,
    ai_language_input_usd_per_million: 0, ai_language_output_usd_per_million: 0,
  }
}
function forAll(current: AiSettings, model: string): AiSettings {
  return { ...current, ai_tagging_model: model, ai_language_model: model,
    ai_translation_model: model, ai_translation_qa_model: model, ai_translation_editor_model: model }
}

export function AiSettingsPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<AiSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [savedProvider, setSavedProvider] = useState<AiSettings['provider'] | null>(null)
  const [savedBaseUrl, setSavedBaseUrl] = useState('')
  const [savedKeyConfigured, setSavedKeyConfigured] = useState(false)
  const [availableModels, setAvailableModels] = useState<string[]>([])
  const [modelPrices, setModelPrices] = useState<AiModelPrices>({})
  const [priceNotice, setPriceNotice] = useState('')
  const [priceError, setPriceError] = useState(false)
  const [manualModel, setManualModel] = useState(false)
  const [step, setStep] = useState<0 | 1 | 2>(0)
  const [busy, setBusy] = useState<'connection' | 'model' | 'prices' | 'save' | 'remove' | null>(null)
  const [connectionNotice, setConnectionNotice] = useState('')
  const [connectionError, setConnectionError] = useState(false)
  const [modelNotice, setModelNotice] = useState('')
  const [modelError, setModelError] = useState(false)
  const [saveNotice, setSaveNotice] = useState('')
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof getAiUsage>> | null>(null)
  const hasSavedKey = Boolean(savedKeyConfigured && form?.provider === savedProvider && form.base_url === savedBaseUrl)
  const canConnect = Boolean(form && (apiKey || hasSavedKey) && (form.provider === 'openai' || form.base_url.trim()))

  useEffect(() => {
    let active = true
    getAiSettings().then(settings => {
      if (active) { setForm(settings); setSavedProvider(settings.provider); setSavedBaseUrl(settings.base_url); setSavedKeyConfigured(settings.key_configured) }
    }).catch(err => { if (active) setConnectionNotice(err instanceof Error ? err.message : 'KI-Einstellungen konnten nicht geladen werden.') })
    getAiUsage().then(result => { if (active) setUsage(result) }).catch(() => undefined)
    return () => { active = false }
  }, [])

  function connectionInput() {
    if (!form) throw new Error('KI-Einstellungen fehlen.')
    return { provider: form.provider, base_url: form.base_url, ...(apiKey ? { api_key: apiKey } : {}) }
  }
  function changeProvider(provider: AiSettings['provider']) {
    setApiKey(''); setAvailableModels([]); setModelPrices({}); setPriceNotice(''); setPriceError(false); setConnectionNotice(''); setModelNotice(''); setSaveNotice('')
    setForm(current => {
      if (!current) return current
      const next = clearPrices(current)
      next.provider = provider
      next.key_configured = provider === savedProvider && current.base_url === savedBaseUrl && savedKeyConfigured
      return forAll(next, provider === 'custom' ? '' : 'gpt-5.4-nano')
    })
  }
  async function checkConnection() {
    if (!form) return
    setBusy('connection'); setConnectionNotice(''); setConnectionError(false); setModelNotice('')
    try {
      const catalog = await listAiModels(connectionInput())
      setAvailableModels(catalog.models)
      setModelPrices(catalog.prices)
      setForm(current => current && withCatalogPrices(current, catalog.prices))
      setManualModel(Boolean(form.ai_tagging_model && !catalog.models.includes(form.ai_tagging_model)))
      setConnectionNotice(catalog.models.length ? `Verbindung hergestellt. ${catalog.models.length} Modelle verfügbar.${Object.keys(catalog.prices).length ? ` Preise für ${Object.keys(catalog.prices).length} Modelle geladen.` : ''}` :
        'Verbindung hergestellt. Der Anbieter meldet keine Modelle; gib den Namen im nächsten Schritt ein.')
      setStep(1)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Verbindung konnte nicht geprüft werden.'
      setConnectionError(true)
      setConnectionNotice(`${message} Falls der Anbieter keine Modellliste anbietet, kannst du im nächsten Schritt einen Modellnamen eingeben und testen.`)
    } finally { setBusy(null) }
  }
  async function checkModel() {
    if (!form || !form.ai_tagging_model.trim()) return
    setBusy('model'); setModelNotice(''); setModelError(false)
    try {
      const result = await testAiSettings({ ...connectionInput(), ai_tagging_model: form.ai_tagging_model.trim() })
      setModelNotice(`Modell ${result.model} erfolgreich geprüft.`)
    } catch (err) { setModelError(true); setModelNotice(err instanceof Error ? err.message : 'Modell konnte nicht geprüft werden.') }
    finally { setBusy(null) }
  }
  async function loadPrices() {
    if (!form) return
    setBusy('prices'); setPriceNotice(''); setPriceError(false)
    try {
      const catalog = await listAiModels(connectionInput())
      setAvailableModels(catalog.models)
      setModelPrices(catalog.prices)
      setForm(current => current && withCatalogPrices(current, catalog.prices))
      setPriceNotice(Object.keys(catalog.prices).length
        ? `Preise für ${Object.keys(catalog.prices).length} Modelle geladen. Passende Preise sind im Formular eingetragen; bitte speichern.`
        : 'Dieser Anbieter liefert keine Tokenpreise über die API. Du kannst sie manuell eintragen.')
    } catch (err) { setPriceError(true); setPriceNotice(err instanceof Error ? err.message : 'Tokenpreise konnten nicht geladen werden.') }
    finally { setBusy(null) }
  }
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!form) return
    if (!canConnect) { setStep(0); setConnectionError(true); setConnectionNotice('Bitte Base URL und API-Key eingeben.'); return }
    if (step === 0) { await checkConnection(); return }
    if (tasks.some(([name]) => !form[name].trim())) {
      setStep(1); setModelError(true); setModelNotice('Bitte ein Modell auswählen oder eingeben.'); return
    }
    setBusy('save'); setSaveNotice('')
    try {
      const { key_configured: _configured, ...values } = form
      const saved = await saveAiSettings({ ...values, ...(apiKey ? { api_key: apiKey } : {}) })
      setForm(saved); setSavedProvider(saved.provider); setSavedBaseUrl(saved.base_url); setSavedKeyConfigured(saved.key_configured); setApiKey('')
      onSaved()
    } catch (err) { setSaveNotice(err instanceof Error ? err.message : 'Speichern fehlgeschlagen.') }
    finally { setBusy(null) }
  }
  async function removeKey() {
    setBusy('remove'); setConnectionNotice('')
    try {
      await saveAiSettings({ api_key: null })
      setForm(current => current && { ...current, key_configured: false })
      setSavedKeyConfigured(false); setApiKey(''); setConnectionNotice('API-Key entfernt.')
    } catch (err) { setConnectionError(true); setConnectionNotice(err instanceof Error ? err.message : 'API-Key konnte nicht entfernt werden.') }
    finally { setBusy(null) }
  }

  return <section className="ai-settings">
    <div className="ai-dialog-header">
      <div><p className="ai-eyebrow">EINSTELLUNGEN</p><h2 id="ai-settings-title">KI-Anbindung</h2></div>
      <button type="button" className="ai-close" onClick={onClose} aria-label="Schließen">×</button>
    </div>
    <ol className="ai-steps" aria-label="Einrichtungsschritte">
      {(['Verbinden', 'Modell wählen', 'Optionen'] as const).map((label, index) =>
        <li key={label} className={step === index ? 'active' : ''}><button type="button"
          disabled={index > 0 && !canConnect} onClick={() => setStep(index as 0 | 1 | 2)}
          aria-current={step === index ? 'step' : undefined}><span>{index + 1}</span>{label}</button></li>)}
    </ol>
    {!form ? <p className="ai-dialog-message">{connectionNotice || 'Einstellungen werden geladen…'}</p> : <form onSubmit={save}>
      <div className="ai-dialog-body">
        {step === 0 && <div className="ai-step-content">
          <div><h3>Mit einem KI-Dienst verbinden</h3><p>Wähle den Anbieter und gib seine Zugangsdaten ein.</p></div>
          <label>Anbieter<select value={form.provider} onChange={event => changeProvider(event.target.value as AiSettings['provider'])}>
            <option value="openai">OpenAI</option><option value="custom">OpenAI-kompatibler Dienst</option>
          </select></label>
          {form.provider === 'custom' && <label>Base URL<input type="url" required value={form.base_url}
            placeholder="https://anbieter.example/v1" onChange={event => {
              const base_url = event.target.value
              setAvailableModels([]); setModelPrices({}); setConnectionNotice(''); setSaveNotice('')
              setForm(current => current && { ...clearPrices(current), base_url,
                key_configured: savedKeyConfigured && current.provider === savedProvider && base_url === savedBaseUrl })
            }} /><small>API-Stammverzeichnis des Anbieters, meist mit /v1 am Ende.</small></label>}
          <label>API-Key <span className="ai-key-state">{hasSavedKey && !apiKey ? 'Gespeichert' : apiKey ? 'Neuer Key eingegeben' : 'Fehlt'}</span>
            <input type="password" autoComplete="new-password" value={apiKey}
              placeholder={hasSavedKey ? 'Leer lassen, um den gespeicherten Key zu verwenden' : 'API-Key eingeben'}
              onChange={event => { setApiKey(event.target.value); setAvailableModels([]); setModelPrices({}); setConnectionNotice(''); setSaveNotice('') }} /></label>
          {hasSavedKey && <button type="button" className="ai-text-button" disabled={busy !== null} onClick={removeKey}>Gespeicherten Key entfernen</button>}
          <div className="ai-check-row"><button type="button" className="ai-secondary" disabled={busy !== null || !canConnect} onClick={checkConnection}>
            {busy === 'connection' ? 'Prüft…' : 'Verbindung prüfen & Modelle laden'}</button><span>Der Test speichert nichts.</span></div>
          {connectionNotice && <p className={`ai-feedback ${connectionError ? 'error' : ''}`} role={connectionError ? 'alert' : 'status'}>{connectionNotice}</p>}
        </div>}
        {step === 1 && <div className="ai-step-content">
          <div><h3>Ein Modell für den Start wählen</h3><p>Dieses Modell gilt zunächst für Tags, Sprache und Übersetzung. Unter „Optionen“ kannst du pro Aufgabe ein anderes wählen.</p></div>
          {connectionNotice && <p className={`ai-feedback ${connectionError ? 'error' : ''}`} role={connectionError ? 'alert' : 'status'}>{connectionNotice}</p>}
          {tasks.some(([name]) => form[name] !== form.ai_tagging_model) && <p className="ai-note">Für einzelne Aufgaben sind bereits andere Modelle eingestellt. Eine neue Auswahl setzt alle Aufgaben auf dasselbe Modell.</p>}
          {availableModels.length > 0 && <label>Verfügbares Modell<select value={manualModel ? '__manual__' : form.ai_tagging_model}
            onChange={event => {
              if (event.target.value === '__manual__') setManualModel(true)
              else { setManualModel(false); setForm(current => current && withCatalogPrices(forAll(clearPrices(current), event.target.value), modelPrices)); setModelNotice('') }
            }}><option value="">Modell auswählen…</option>
            {availableModels.map(model => <option key={model} value={model}>{model}</option>)}
            <option value="__manual__">Modellname selbst eingeben</option></select></label>}
          {(availableModels.length === 0 || manualModel) && <label>Modellname<input value={form.ai_tagging_model}
            maxLength={120} placeholder="Modell-ID des Anbieters" onChange={event => {
              setForm(current => current && withCatalogPrices(forAll(clearPrices(current), event.target.value), modelPrices)); setModelNotice('')
            }} /></label>}
          {modelPrices[form.ai_tagging_model] && <p className="ai-note">API-Preis: Eingabe {modelPrices[form.ai_tagging_model].input_usd_per_million} USD, Ausgabe {modelPrices[form.ai_tagging_model].output_usd_per_million} USD je Million Tokens.</p>}
          <div className="ai-check-row"><button type="button" className="ai-secondary" disabled={busy !== null || !form.ai_tagging_model.trim()} onClick={checkModel}>
            {busy === 'model' ? 'Prüft…' : 'Modell testen'}</button><span>Sendet eine kurze KI-Anfrage.</span></div>
          {modelNotice && <p className={`ai-feedback ${modelError ? 'error' : ''}`} role={modelError ? 'alert' : 'status'}>{modelNotice}</p>}
        </div>}
        {step === 2 && <div className="ai-step-content">
          <div><h3>Optionen</h3><p>Die ersten beiden Schritte reichen für den Start. Hier kannst du Modelle, Preise und Limits getrennt festlegen.</p></div>
          <details><summary>Modelle je Aufgabe</summary><div className="ai-models">{tasks.map(([name, label]) => <label key={name}>{label}
            <input required maxLength={120} value={form[name]} onChange={event => setForm(current => current &&
              withCatalogPrices({ ...current, [name]: event.target.value }, modelPrices, name, true))} />
          </label>)}</div></details>
          <details><summary>Tokenpreise</summary><p>USD je Million Tokens. Wenn der Anbieter Modellpreise über die API liefert, kannst du sie hier übernehmen. Fehlende Preise bleiben manuell editierbar.</p>
            <button type="button" className="ai-secondary" disabled={busy !== null || !canConnect} onClick={loadPrices}>{busy === 'prices' ? 'Lädt…' : 'Preise aus API laden'}</button>
            {priceNotice && <p className={`ai-feedback ${priceError ? 'error' : ''}`} role={priceError ? 'alert' : 'status'}>{priceNotice}</p>}
            <div className="ai-prices">{prices.map(([name, label], index) => <label key={name}>{label}
              <input type="number" min="0" step="any" required={index < 6} value={form[name] ?? ''}
                onChange={event => setForm(current => current && { ...current, [name]: event.target.value === '' ? (index < 6 ? 0 : null) : Number(event.target.value) })} />
            </label>)}</div></details>
          <details><summary>Kostenlimits und Verbrauch</summary>
            <p>Erfasste Kosten: heute {usage?.day_usd.toFixed(4) ?? '…'} USD, diesen Monat {usage?.month_usd.toFixed(4) ?? '…'} USD. Reserviert: {usage?.reserved_usd.toFixed(4) ?? '…'} USD. Der Wert 0 deaktiviert ein Limit.</p>
            {usage && (usage.warning.day || usage.warning.month) && <p className="ai-feedback error" role="alert">Mindestens ein KI-Limit ist zu {form.ai_warning_percent}% erreicht.</p>}
            {usage && <ul className="ai-usage-list">{Object.entries(usage.by_feature).map(([feature, item]) => <li key={feature}>
              {feature}: {item.cost_usd.toFixed(4)} USD · {item.requests} Anfragen{item.unpriced_requests ? ` · ${item.unpriced_requests} mit unbekannten Kosten` : ''}
            </li>)}</ul>}
            <div className="ai-prices"><label>Tageslimit (USD)<input type="number" min="0" step="any" value={form.ai_daily_limit_usd}
              onChange={event => setForm(current => current && { ...current, ai_daily_limit_usd: Number(event.target.value) })} /></label>
              <label>Monatslimit (USD)<input type="number" min="0" step="any" value={form.ai_monthly_limit_usd}
                onChange={event => setForm(current => current && { ...current, ai_monthly_limit_usd: Number(event.target.value) })} /></label></div>
            <label>Warnschwelle (%)<input type="number" min="1" max="100" step="1" value={form.ai_warning_percent}
              onChange={event => setForm(current => current && { ...current, ai_warning_percent: Number(event.target.value) })} /></label>
          </details>
        </div>}
      </div>
      <div className="ai-dialog-footer">
        {saveNotice && <p className="ai-feedback error" role="alert">{saveNotice}</p>}
        <div className="ai-dialog-actions">
          {step > 0 && <button type="button" className="ai-secondary" onClick={() => setStep(step === 2 ? 1 : 0)}>Zurück</button>}
          {step < 2 && <button type="button" className="ai-secondary" disabled={!canConnect}
            onClick={() => setStep(step === 0 ? 1 : 2)}>{step === 0 ? 'Weiter: Modell' : 'Weitere Optionen'}</button>}
          {step > 0 && <button type="submit" className="ai-primary" disabled={busy !== null || !canConnect || !form.ai_tagging_model.trim()}>
            {busy === 'save' ? 'Speichert…' : 'Speichern'}</button>}
        </div>
      </div>
    </form>}
  </section>
}
