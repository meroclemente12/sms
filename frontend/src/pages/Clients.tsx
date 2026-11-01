import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import type { Client, ClientDetail } from '../api'
import { Card, Empty, ErrorNote, Field, StatusBadge } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { clockIn, dayLabel, dayOf, peso } from '../time'

const TZ = 'Asia/Manila'

export default function Clients() {
  const { clientId } = useParams()
  return (
    <div className="stack">
      <ClientSearch />
      {clientId && <ClientCard key={clientId} id={clientId} />}
    </div>
  )
}

function ClientSearch() {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [adding, setAdding] = useState(false)
  const navigate = useNavigate()
  const { clientId } = useParams()

  useEffect(() => {
    const t = window.setTimeout(() => setTerm(q.trim()), 200)
    return () => window.clearTimeout(t)
  }, [q])
  const { data, error } = useLoad(() => api<Client[]>(`/clients?q=${encodeURIComponent(term)}`), [term])

  return (
    <Card
      title="Clients"
      actions={
        <button className="primary" onClick={() => setAdding((a) => !a)}>
          {adding ? 'Close' : 'Add client'}
        </button>
      }
    >
      {adding && (
        <ClientForm
          onSaved={(c) => {
            setAdding(false)
            navigate(`/clients/${c.id}`)
          }}
        />
      )}
      <input
        type="search"
        className="wide"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by mobile number or name"
        aria-label="Search clients"
        autoFocus
      />
      <ErrorNote message={error} />
      {data && data.length === 0 && <Empty>No clients found.</Empty>}
      {data && data.length > 0 && (
        <>
          {!term && <p className="muted small">Newest clients</p>}
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Mobile</th>
                  <th>Allergies</th>
                </tr>
              </thead>
              <tbody>
                {data.map((c) => (
                  <tr key={c.id} className={c.id === clientId ? 'selected' : ''}>
                    <td>
                      <Link to={`/clients/${c.id}`}>{c.name}</Link>
                    </td>
                    <td>{c.mobile ?? <span className="muted">–</span>}</td>
                    <td>{c.allergies ? <span className="allergy">{c.allergies}</span> : <span className="muted">–</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  )
}

function ClientCard({ id }: { id: string }) {
  const { data, error, reload } = useLoad(() => api<ClientDetail>(`/clients/${id}`), [id])
  const [editing, setEditing] = useState(false)
  if (error) return <ErrorNote message={error} />
  if (!data) return null
  return (
    <Card
      title={data.name}
      actions={
        <button onClick={() => setEditing((e) => !e)}>{editing ? 'Close' : 'Edit'}</button>
      }
    >
      {editing ? (
        <ClientForm
          client={data}
          onSaved={() => {
            setEditing(false)
            void reload()
          }}
        />
      ) : (
        <dl className="facts">
          <dt>Mobile</dt>
          <dd>{data.mobile ?? '–'}</dd>
          <dt>Email</dt>
          <dd>{data.email ?? '–'}</dd>
          <dt>Allergies</dt>
          <dd>{data.allergies ? <span className="allergy">{data.allergies}</span> : 'None noted'}</dd>
          <dt>Notes</dt>
          <dd>{data.notes ?? '–'}</dd>
        </dl>
      )}
      <h3 className="spaced">Visits</h3>
      {data.visits.length === 0 ? (
        <Empty>No visits yet.</Empty>
      ) : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Service</th>
                <th>Therapist</th>
                <th>Branch</th>
                <th>Price</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.visits.map((v) => (
                <tr key={v.booking_id}>
                  <td>
                    {dayLabel(dayOf(v.starts_at, TZ))} {clockIn(v.starts_at, TZ)}
                  </td>
                  <td>
                    {v.service_name} · {v.variant_label}
                  </td>
                  <td>{v.therapist_name}</td>
                  <td>{v.branch_name}</td>
                  <td>{peso(v.price)}</td>
                  <td>
                    <StatusBadge status={v.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function ClientForm({ client, onSaved }: { client?: Client; onSaved: (c: Client) => void }) {
  const [form, setForm] = useState({
    name: client?.name ?? '',
    mobile: client?.mobile ?? '',
    email: client?.email ?? '',
    gender: client?.gender ?? '',
    allergies: client?.allergies ?? '',
    notes: client?.notes ?? '',
  })
  const { busy, error, run } = useAction()
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = { ...form, gender: form.gender || null }
    await run(async () =>
      onSaved(
        await api<Client>(client ? `/clients/${client.id}` : '/clients', {
          method: client ? 'PUT' : 'POST',
          json,
        }),
      ),
    )
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Name">
        <input value={form.name} onChange={set('name')} required />
      </Field>
      <Field label="Mobile">
        <input value={form.mobile} onChange={set('mobile')} inputMode="tel" />
      </Field>
      <Field label="Email">
        <input type="email" value={form.email} onChange={set('email')} />
      </Field>
      <Field label="Gender">
        <select value={form.gender} onChange={set('gender')}>
          <option value="">Not given</option>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="other">Other</option>
        </select>
      </Field>
      <Field label="Allergies">
        <input value={form.allergies} onChange={set('allergies')} />
      </Field>
      <Field label="Notes">
        <textarea rows={2} value={form.notes} onChange={set('notes')} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {client ? 'Save' : 'Add client'}
        </button>
      </div>
      <ErrorNote message={error} />
    </form>
  )
}
