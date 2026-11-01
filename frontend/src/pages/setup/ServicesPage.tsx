import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { BedType, Price, Service, Variant } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorNote, Field, ReadOnlyNote, StatusPill } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'

const peso = (v: string | number) =>
  Number(v).toLocaleString('en-PH', { style: 'currency', currency: 'PHP', maximumFractionDigits: 2 })

function ServiceForm({ initial, bedTypes, onDone }: { initial?: Service; bedTypes: BedType[]; onDone: () => void }) {
  const [name, setName] = useState(initial?.name ?? '')
  const [category, setCategory] = useState(initial?.category ?? '')
  const [chosenBedType, setBedType] = useState(initial?.bed_type_id ?? '')
  // Bed types may finish loading after the form opens; default to the first one.
  const bedType = chosenBedType || bedTypes[0]?.id || ''
  const [before, setBefore] = useState(initial?.buffer_before_min ?? 0)
  const [after, setAfter] = useState(initial?.buffer_after_min ?? 20)
  const [couples, setCouples] = useState(initial?.guest_count === 2)
  const [active, setActive] = useState(initial?.active ?? true)
  // A new service is created with its first option in one step.
  const [label, setLabel] = useState('60 min')
  const [minutes, setMinutes] = useState(60)
  const [price, setPrice] = useState('')
  const { busy, error, run } = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = {
      name,
      category: category || null,
      bed_type_id: bedType,
      buffer_before_min: before,
      buffer_after_min: after,
      guest_count: couples ? 2 : 1,
      active,
    }
    const ok = await run(async () => {
      if (initial) {
        await api(`/services/${initial.id}`, { method: 'PUT', json })
      } else {
        const svc = await api<Service>('/services', { method: 'POST', json })
        await api(`/services/${svc.id}/variants`, {
          method: 'POST',
          json: { label, duration_min: minutes, default_price: price },
        })
      }
    })
    if (ok) onDone()
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Service name">
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </Field>
      <Field label="Category">
        <input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Massage" />
      </Field>
      <Field label="Needs bed type">
        <select value={bedType} onChange={(e) => setBedType(e.target.value)} required>
          {bedTypes.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Prep before (min)">
        <input type="number" min={0} max={120} value={before} onChange={(e) => setBefore(Number(e.target.value))} />
      </Field>
      <Field label="Cleanup after (min)">
        <input type="number" min={0} max={120} value={after} onChange={(e) => setAfter(Number(e.target.value))} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={couples} onChange={(e) => setCouples(e.target.checked)} /> Couples service (2
        guests, couples room)
      </label>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
      </label>
      {!initial && (
        <>
          <Field label="First option">
            <input value={label} onChange={(e) => setLabel(e.target.value)} required />
          </Field>
          <Field label="Minutes">
            <input type="number" min={5} max={600} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} required />
          </Field>
          <Field label="Default price (₱)">
            <input type="number" min={0} step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} required />
          </Field>
        </>
      )}
      <div className="form-actions">
        <button className="primary" disabled={busy || !bedType}>
          Save
        </button>
        <button type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      <ErrorNote message={error} />
    </form>
  )
}

function VariantRow({ v, canEdit, onChange }: { v: Variant; canEdit: boolean; onChange: () => void }) {
  const [edit, setEdit] = useState(false)
  const [label, setLabel] = useState(v.label)
  const [minutes, setMinutes] = useState(v.duration_min)
  const [price, setPrice] = useState(v.default_price)
  const [active, setActive] = useState(v.active)
  const { busy, error, run } = useAction()

  async function save(e: FormEvent) {
    e.preventDefault()
    const ok = await run(() =>
      api(`/variants/${v.id}`, { method: 'PUT', json: { label, duration_min: minutes, default_price: price, active } }),
    )
    if (ok) {
      setEdit(false)
      onChange()
    }
  }

  if (!edit)
    return (
      <li className="variant">
        <span>{v.label}</span>
        {v.label !== `${v.duration_min} min` && <span className="muted">{v.duration_min} min</span>}
        <span>{peso(v.default_price)}</span>
        {!v.active && <StatusPill active={false} />}
        {canEdit && <button onClick={() => setEdit(true)}>Edit</button>}
      </li>
    )
  return (
    <li>
      <form className="inline-form compact" onSubmit={save}>
        <input value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Option name" required />
        <input type="number" min={5} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Minutes" />
        <input type="number" min={0} step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} aria-label="Default price" />
        <label className="check">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
        </label>
        <button className="primary" disabled={busy}>
          Save
        </button>
        <button type="button" onClick={() => setEdit(false)}>
          Cancel
        </button>
        <ErrorNote message={error} />
      </form>
    </li>
  )
}

function AddVariant({ service, onDone }: { service: Service; onDone: () => void }) {
  const [label, setLabel] = useState('90 min')
  const [minutes, setMinutes] = useState(90)
  const [price, setPrice] = useState('')
  const { busy, error, run } = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ok = await run(() =>
      api(`/services/${service.id}/variants`, { method: 'POST', json: { label, duration_min: minutes, default_price: price } }),
    )
    if (ok) {
      setPrice('')
      onDone()
    }
  }

  return (
    <form className="inline-form compact" onSubmit={submit}>
      <input value={label} onChange={(e) => setLabel(e.target.value)} aria-label="New option name" placeholder="Option name" required />
      <input type="number" min={5} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Minutes" />
      <input type="number" min={0} step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Price" aria-label="Price" required />
      <button disabled={busy}>Add option</button>
      <ErrorNote message={error} />
    </form>
  )
}

function MenuCard({ bedTypes, onChanged }: { bedTypes: BedType[]; onChanged: () => void }) {
  const { isAdmin } = useAuth()
  const { data, error, reload } = useLoad(() => api<Service[]>('/services'), [])
  const [editing, setEditing] = useState<string | 'new' | null>(null)
  const done = () => {
    setEditing(null)
    void reload()
    onChanged()
  }

  return (
    <Card
      title="Service menu"
      actions={isAdmin && editing === null ? <button onClick={() => setEditing('new')}>Add service</button> : undefined}
    >
      <p className="muted">The menu is shared by all branches. Each branch can change its own prices below.</p>
      {isAdmin && bedTypes.length === 0 && <p className="note">Add a bed type on the Branches page first.</p>}
      <ErrorNote message={error} />
      {editing === 'new' && <ServiceForm bedTypes={bedTypes} onDone={done} />}
      {data && data.length === 0 && <Empty>No services yet.</Empty>}
      <div className="service-list">
        {data?.map((s) =>
          editing === s.id ? (
            <ServiceForm key={s.id} initial={s} bedTypes={bedTypes} onDone={done} />
          ) : (
            <div key={s.id} className="service">
              <div className="service-head">
                <strong>{s.name}</strong>
                {s.category && <span className="muted">{s.category}</span>}
                {s.guest_count === 2 && <span className="pill">Couples</span>}
                <StatusPill active={s.active} />
                {isAdmin && <button onClick={() => setEditing(s.id)}>Edit</button>}
              </div>
              <p className="muted small">
                {bedTypes.find((t) => t.id === s.bed_type_id)?.name ?? 'Bed'} · {s.buffer_after_min} min cleanup
                {s.buffer_before_min > 0 && ` · ${s.buffer_before_min} min prep`}
              </p>
              <ul className="variants">
                {s.variants.map((v) => (
                  <VariantRow key={v.id} v={v} canEdit={isAdmin} onChange={done} />
                ))}
              </ul>
              {isAdmin && <AddVariant service={s} onDone={done} />}
            </div>
          ),
        )}
      </div>
    </Card>
  )
}

function PriceRow({ p, branchId, canEdit, onChange }: { p: Price; branchId: string; canEdit: boolean; onChange: () => void }) {
  const [price, setPrice] = useState(p.branch_price ?? '')
  const { busy, error, run } = useAction()

  async function save(next: { price: string; offered: boolean }) {
    const ok = await run(() =>
      api(`/branches/${branchId}/prices/${p.variant_id}`, {
        method: 'PUT',
        json: { price: next.price === '' ? null : next.price, offered: next.offered },
      }),
    )
    if (ok) onChange()
  }

  return (
    <tr>
      <td>
        {p.service_name} <span className="muted">· {p.variant_label}</span>
      </td>
      <td>{peso(p.default_price)}</td>
      <td>
        {canEdit ? (
          <form
            className="inline-form compact"
            onSubmit={(e) => {
              e.preventDefault()
              void save({ price, offered: p.offered })
            }}
          >
            <input
              type="number"
              min={0}
              step="0.01"
              value={price}
              placeholder="Default"
              onChange={(e) => setPrice(e.target.value)}
              aria-label={`Branch price for ${p.service_name} ${p.variant_label}`}
            />
            <button disabled={busy || price === (p.branch_price ?? '')}>Save</button>
          </form>
        ) : p.branch_price ? (
          peso(p.branch_price)
        ) : (
          <span className="muted">Same as default</span>
        )}
        <ErrorNote message={error} />
      </td>
      <td>
        <label className="check">
          <input
            type="checkbox"
            checked={p.offered}
            disabled={!canEdit || busy}
            onChange={(e) => void save({ price: p.branch_price ?? '', offered: e.target.checked })}
          />{' '}
          Offered here
        </label>
      </td>
    </tr>
  )
}

function PricesCard({ version }: { version: number }) {
  const { branch, canManage } = useAuth()
  const branchId = branch?.id
  const { data, error, reload } = useLoad(
    () => (branchId ? api<Price[]>(`/branches/${branchId}/prices`) : Promise.resolve([])),
    [branchId, version],
  )
  if (!branchId) return null
  return (
    <Card title={`Prices at ${branch?.name}`}>
      <ReadOnlyNote show={!canManage} />
      <ErrorNote message={error} />
      {data && data.length === 0 ? (
        <Empty>No active services yet.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Service</th>
              <th>Default price</th>
              <th>Price at this branch</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data?.map((p) => (
              <PriceRow key={p.variant_id} p={p} branchId={branchId} canEdit={canManage} onChange={() => void reload()} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

export default function ServicesPage() {
  const { data: bedTypes } = useLoad(() => api<BedType[]>('/bed-types'), [])
  const [version, setVersion] = useState(0)
  return (
    <div className="stack">
      <h1>Services and prices</h1>
      <MenuCard bedTypes={bedTypes ?? []} onChanged={() => setVersion((n) => n + 1)} />
      <PricesCard version={version} />
    </div>
  )
}
