import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { Branch, Role, StaffUser } from '../../api'
import { useAuth } from '../../auth'
import { Card, ErrorNote, Field, StatusPill } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: 'front_desk', label: 'Front desk', hint: 'Books guests at their branches' },
  { value: 'manager', label: 'Manager', hint: 'Also sets up rooms, prices, therapists and shifts at their branches' },
  { value: 'admin', label: 'Admin', hint: 'Everything, at every branch, including staff accounts' },
]

function UserForm({ initial, branches, onDone }: { initial?: StaffUser; branches: Branch[]; onDone: () => void }) {
  const [name, setName] = useState(initial?.name ?? '')
  const [email, setEmail] = useState(initial?.email ?? '')
  const [role, setRole] = useState<Role>(initial?.role ?? 'front_desk')
  const [password, setPassword] = useState('')
  const [active, setActive] = useState(initial?.active ?? true)
  const [branchIds, setBranchIds] = useState<Set<string>>(new Set(initial?.branch_ids ?? []))
  const { busy, error, run } = useAction()

  function toggle(id: string) {
    setBranchIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = { name, email, role, active, password: password || null, branch_ids: [...branchIds] }
    const ok = await run(() =>
      initial ? api(`/users/${initial.id}`, { method: 'PUT', json }) : api('/users', { method: 'POST', json }),
    )
    if (ok) onDone()
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </Field>
      <Field label="Email">
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </Field>
      <Field label="Role" hint={ROLES.find((r) => r.value === role)?.hint}>
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label={initial ? 'New password' : 'Password'} hint={initial ? 'Leave empty to keep the current one' : 'At least 8 characters'}>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          required={!initial}
          minLength={8}
        />
      </Field>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Can sign in
      </label>
      {role !== 'admin' && (
        <fieldset className="skills">
          <legend>Branches</legend>
          {branches.map((b) => (
            <label key={b.id} className="check">
              <input type="checkbox" checked={branchIds.has(b.id)} onChange={() => toggle(b.id)} /> {b.name}
            </label>
          ))}
        </fieldset>
      )}
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

export default function StaffPage() {
  const { isAdmin } = useAuth()
  const { data: users, error, reload } = useLoad(() => (isAdmin ? api<StaffUser[]>('/users') : Promise.resolve([])), [isAdmin])
  const { data: branches } = useLoad(() => api<Branch[]>('/branches'), [])
  const [editing, setEditing] = useState<string | 'new' | null>(null)
  const branchName = new Map((branches ?? []).map((b) => [b.id, b.name]))
  const done = () => {
    setEditing(null)
    void reload()
  }

  if (!isAdmin) return <p>Only admins can manage staff accounts.</p>
  return (
    <div className="stack">
      <h1>Staff accounts</h1>
      <Card title="Who can sign in" actions={editing === null ? <button onClick={() => setEditing('new')}>Add staff</button> : undefined}>
        <ErrorNote message={error} />
        {editing === 'new' && <UserForm branches={branches ?? []} onDone={done} />}
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Branches</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users?.map((u) =>
              editing === u.id ? (
                <tr key={u.id}>
                  <td colSpan={5}>
                    <UserForm initial={u} branches={branches ?? []} onDone={done} />
                  </td>
                </tr>
              ) : (
                <tr key={u.id}>
                  <td>
                    {u.name}
                    <div className="muted small">{u.email}</div>
                  </td>
                  <td>{ROLES.find((r) => r.value === u.role)?.label}</td>
                  <td>
                    {u.role === 'admin' ? 'All' : u.branch_ids.map((id) => branchName.get(id)).filter(Boolean).join(', ') || '—'}
                  </td>
                  <td>
                    <StatusPill active={u.active} />
                  </td>
                  <td className="row-actions">
                    <button onClick={() => setEditing(u.id)}>Edit</button>
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
