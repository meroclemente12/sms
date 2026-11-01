import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { Client } from '../api'
import { ErrorNote, Field } from '../components/ui'
import { useAction } from '../components/hooks'

/** Find a client by mobile or name, or add a new one, in one box. */
export default function ClientPicker({
  value,
  onChange,
  autoFocus,
}: {
  value: Client | null
  onChange: (c: Client | null) => void
  autoFocus?: boolean
}) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Client[]>([])
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    const term = q.trim()
    if (term.length < 2) {
      setResults([])
      return
    }
    let stale = false
    const t = window.setTimeout(() => {
      api<Client[]>(`/clients?q=${encodeURIComponent(term)}&limit=8`)
        .then((r) => !stale && setResults(r))
        .catch(() => !stale && setResults([]))
    }, 200)
    return () => {
      stale = true
      window.clearTimeout(t)
    }
  }, [q])

  if (value)
    return (
      <div className="picked-client">
        <div>
          <strong>{value.name}</strong> <span className="muted">{value.mobile}</span>
          {value.allergies && <p className="allergy">Allergies: {value.allergies}</p>}
          {value.notes && <p className="muted small">{value.notes}</p>}
        </div>
        <button type="button" onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    )

  if (adding)
    return (
      <NewClient
        initial={q}
        onCancel={() => setAdding(false)}
        onCreated={(c) => {
          setAdding(false)
          onChange(c)
        }}
      />
    )

  const looksLikeNumber = /\d{3}/.test(q)
  return (
    <div className="client-search">
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Mobile number or name"
        aria-label="Find client by mobile number or name"
        autoFocus={autoFocus}
      />
      {results.length > 0 && (
        <ul className="client-results">
          {results.map((c) => (
            <li key={c.id}>
              <button type="button" onClick={() => onChange(c)}>
                <strong>{c.name}</strong> <span className="muted">{c.mobile ?? 'no mobile'}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {q.trim().length >= 2 && results.length === 0 && <p className="muted small">No client found.</p>}
      <button type="button" className="linklike" onClick={() => setAdding(true)}>
        + New client{q.trim() && !looksLikeNumber ? ` “${q.trim()}”` : ''}
      </button>
    </div>
  )
}

function NewClient({
  initial,
  onCancel,
  onCreated,
}: {
  initial: string
  onCancel: () => void
  onCreated: (c: Client) => void
}) {
  const isNumber = /\d{3}/.test(initial)
  const [name, setName] = useState(isNumber ? '' : initial.trim())
  const [mobile, setMobile] = useState(isNumber ? initial.trim() : '')
  const [email, setEmail] = useState('')
  const [allergies, setAllergies] = useState('')
  const { busy, error, run } = useAction()

  // A form inside the booking form would submit both, so this is a plain group.
  async function save(e?: FormEvent) {
    e?.preventDefault()
    await run(async () => {
      const c = await api<Client>('/clients', {
        method: 'POST',
        json: { name, mobile: mobile || null, email: email || null, allergies: allergies || null },
      })
      onCreated(c)
    })
  }

  return (
    <div className="new-client" role="group" aria-label="New client">
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus={!isNumber} />
      </Field>
      <Field label="Mobile">
        <input value={mobile} onChange={(e) => setMobile(e.target.value)} inputMode="tel" autoFocus={isNumber} />
      </Field>
      <Field label="Email (optional)">
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Allergies (optional)">
        <input value={allergies} onChange={(e) => setAllergies(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button type="button" className="primary" disabled={busy || !name.trim()} onClick={() => void save()}>
          Add client
        </button>
        <button type="button" onClick={onCancel}>
          Back to search
        </button>
      </div>
      <ErrorNote message={error} />
    </div>
  )
}
