import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { Gender, Service, Therapist } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorNote, Field, ReadOnlyNote, StatusPill } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'

const GENDERS: { value: Gender; label: string }[] = [
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
  { value: 'other', label: 'Other' },
]

function TherapistForm({
  branchId,
  initial,
  services,
  onDone,
}: {
  branchId: string
  initial?: Therapist
  services: Service[]
  onDone: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [gender, setGender] = useState<Gender>(initial?.gender ?? 'female')
  const [colour, setColour] = useState(initial?.display_colour ?? '#5b8def')
  const [active, setActive] = useState(initial?.active ?? true)
  const [skills, setSkills] = useState<Set<string>>(new Set(initial?.skill_service_ids ?? []))
  const { busy, error, run } = useAction()

  function toggle(id: string) {
    setSkills((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = {
      name,
      gender,
      display_colour: colour,
      active,
      employment_status: initial?.employment_status ?? 'regular',
      skill_service_ids: [...skills],
    }
    const ok = await run(() =>
      initial
        ? api(`/therapists/${initial.id}`, { method: 'PUT', json })
        : api(`/branches/${branchId}/therapists`, { method: 'POST', json }),
    )
    if (ok) onDone()
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </Field>
      <Field label="Gender">
        <select value={gender} onChange={(e) => setGender(e.target.value as Gender)}>
          {GENDERS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Board colour">
        <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
      </label>
      <fieldset className="skills">
        <legend>Can perform</legend>
        {services.length === 0 && <span className="muted">Add services first.</span>}
        {services.map((s) => (
          <label key={s.id} className="check">
            <input type="checkbox" checked={skills.has(s.id)} onChange={() => toggle(s.id)} /> {s.name}
          </label>
        ))}
      </fieldset>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
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

export default function TherapistsPage() {
  const { branch, canManage } = useAuth()
  const branchId = branch?.id
  const { data: services } = useLoad(() => api<Service[]>('/services'), [])
  const { data, error, reload } = useLoad(
    () => (branchId ? api<Therapist[]>(`/branches/${branchId}/therapists`) : Promise.resolve([])),
    [branchId],
  )
  const [editing, setEditing] = useState<string | 'new' | null>(null)
  const serviceName = new Map((services ?? []).map((s) => [s.id, s.name]))
  const done = () => {
    setEditing(null)
    void reload()
  }

  if (!branchId) return <Empty>You have no branch yet.</Empty>
  return (
    <div className="stack">
      <h1>Therapists</h1>
      <Card
        title={`Therapists at ${branch?.name}`}
        actions={canManage && editing === null ? <button onClick={() => setEditing('new')}>Add therapist</button> : undefined}
      >
        <ReadOnlyNote show={!canManage} />
        <ErrorNote message={error} />
        {editing === 'new' && <TherapistForm branchId={branchId} services={services ?? []} onDone={done} />}
        {data && data.length === 0 && editing !== 'new' && <Empty>No therapists yet.</Empty>}
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Gender</th>
              <th>Can perform</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data?.map((t) =>
              editing === t.id ? (
                <tr key={t.id}>
                  <td colSpan={5}>
                    <TherapistForm branchId={branchId} initial={t} services={services ?? []} onDone={done} />
                  </td>
                </tr>
              ) : (
                <tr key={t.id}>
                  <td>
                    <span className="swatch" style={{ background: t.display_colour ?? 'transparent' }} aria-hidden />
                    {t.name}
                  </td>
                  <td>{GENDERS.find((g) => g.value === t.gender)?.label}</td>
                  <td>
                    {t.skill_service_ids.length === 0 ? (
                      <span className="muted">None yet</span>
                    ) : (
                      t.skill_service_ids.map((id) => serviceName.get(id)).filter(Boolean).join(', ')
                    )}
                  </td>
                  <td>
                    <StatusPill active={t.active} />
                  </td>
                  <td className="row-actions">{canManage && <button onClick={() => setEditing(t.id)}>Edit</button>}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
