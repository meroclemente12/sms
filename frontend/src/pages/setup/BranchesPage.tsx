import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { BedType, Branch, Room, RoomType } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorNote, Field, ReadOnlyNote, StatusPill } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'

const ROOM_TYPES: { value: RoomType; label: string }[] = [
  { value: 'single', label: 'Single' },
  { value: 'couples', label: 'Couples' },
  { value: 'wet', label: 'Wet room' },
]

function BranchForm({ initial, onDone }: { initial?: Branch; onDone: () => void }) {
  const [name, setName] = useState(initial?.name ?? '')
  const [address, setAddress] = useState(initial?.address ?? '')
  const [opens, setOpens] = useState(initial?.opens_at.slice(0, 5) ?? '09:00')
  const [closes, setCloses] = useState(initial?.closes_at.slice(0, 5) ?? '22:00')
  const [active, setActive] = useState(initial?.active ?? true)
  const { busy, error, run } = useAction()
  const { refresh } = useAuth()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = {
      name,
      address: address || null,
      timezone: initial?.timezone ?? 'Asia/Manila',
      opens_at: opens,
      closes_at: closes,
      active,
    }
    const ok = await run(() =>
      initial
        ? api(`/branches/${initial.id}`, { method: 'PUT', json })
        : api('/branches', { method: 'POST', json }),
    )
    if (ok) {
      await refresh()
      onDone()
    }
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </Field>
      <Field label="Address">
        <input value={address} onChange={(e) => setAddress(e.target.value)} />
      </Field>
      <Field label="Opens">
        <input type="time" value={opens} onChange={(e) => setOpens(e.target.value)} required />
      </Field>
      <Field label="Closes">
        <input type="time" value={closes} onChange={(e) => setCloses(e.target.value)} required />
      </Field>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
      </label>
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

function BranchesCard() {
  const { isAdmin, canManage, me } = useAuth()
  const { data, error, reload } = useLoad(() => api<Branch[]>('/branches'), [])
  const [editing, setEditing] = useState<string | 'new' | null>(null)
  const mine = new Set(me?.branches.map((b) => b.id))

  return (
    <Card
      title="Branches"
      actions={
        isAdmin && editing === null ? (
          <button onClick={() => setEditing('new')}>Add branch</button>
        ) : undefined
      }
    >
      <ErrorNote message={error} />
      {editing === 'new' && (
        <BranchForm
          onDone={() => {
            setEditing(null)
            void reload()
          }}
        />
      )}
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Hours</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.map((b) =>
            editing === b.id ? (
              <tr key={b.id}>
                <td colSpan={4}>
                  <BranchForm
                    initial={b}
                    onDone={() => {
                      setEditing(null)
                      void reload()
                    }}
                  />
                </td>
              </tr>
            ) : (
              <tr key={b.id}>
                <td>
                  {b.name}
                  {b.address && <div className="muted small">{b.address}</div>}
                </td>
                <td>
                  {b.opens_at.slice(0, 5)} to {b.closes_at.slice(0, 5)}
                </td>
                <td>
                  <StatusPill active={b.active} />
                </td>
                <td className="row-actions">
                  {canManage && (isAdmin || mine.has(b.id)) && <button onClick={() => setEditing(b.id)}>Edit</button>}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </Card>
  )
}

function BedTypesCard({ bedTypes, reload }: { bedTypes: BedType[]; reload: () => void }) {
  const { isAdmin } = useAuth()
  const [name, setName] = useState('')
  const { busy, error, run } = useAction()

  async function add(e: FormEvent) {
    e.preventDefault()
    if (await run(() => api('/bed-types', { method: 'POST', json: { name } }))) {
      setName('')
      reload()
    }
  }

  return (
    <Card title="Bed types">
      <p className="muted">Each service needs one bed type, for example massage bed, foot spa chair or tub.</p>
      <ul className="chips">
        {bedTypes.map((t) => (
          <li key={t.id} className="chip">
            {t.name}
          </li>
        ))}
      </ul>
      {isAdmin && (
        <form className="inline-form" onSubmit={add}>
          <Field label="New bed type">
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <div className="form-actions">
            <button className="primary" disabled={busy}>
              Add
            </button>
          </div>
          <ErrorNote message={error} />
        </form>
      )}
    </Card>
  )
}

function RoomEditor({
  branchId,
  room,
  bedTypes,
  onDone,
}: {
  branchId: string
  room?: Room
  bedTypes: BedType[]
  onDone: () => void
}) {
  const [name, setName] = useState(room?.name ?? '')
  const [roomType, setRoomType] = useState<RoomType>(room?.room_type ?? 'single')
  const [active, setActive] = useState(room?.active ?? true)
  const { busy, error, run } = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = { name, room_type: roomType, active }
    const ok = await run(async () => {
      if (room) {
        await api(`/rooms/${room.id}`, { method: 'PUT', json })
      } else {
        const created = await api<Room>(`/branches/${branchId}/rooms`, { method: 'POST', json })
        // A new room gets one bed (two for a couples room) so it is usable straight away.
        const count = roomType === 'couples' ? 2 : 1
        for (let i = 1; i <= count && bedTypes[0]; i++) {
          await api(`/rooms/${created.id}/beds`, {
            method: 'POST',
            json: { label: `${name} bed ${i}`, bed_type_id: bedTypes[0].id },
          })
        }
      }
    })
    if (ok) onDone()
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Room name">
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </Field>
      <Field label="Type">
        <select value={roomType} onChange={(e) => setRoomType(e.target.value as RoomType)}>
          {ROOM_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </Field>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
      </label>
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

function BedRow({ bed, bedTypes, canEdit, onChange }: { bed: Room['beds'][number]; bedTypes: BedType[]; canEdit: boolean; onChange: () => void }) {
  const { error, run } = useAction()
  const typeName = bedTypes.find((t) => t.id === bed.bed_type_id)?.name ?? ''

  async function update(patch: Partial<{ label: string; bed_type_id: string; active: boolean }>) {
    if (await run(() => api(`/beds/${bed.id}`, { method: 'PUT', json: { label: bed.label, bed_type_id: bed.bed_type_id, active: bed.active, ...patch } }))) onChange()
  }

  return (
    <li className="bed">
      <span>{bed.label}</span>
      {canEdit ? (
        <select value={bed.bed_type_id} onChange={(e) => void update({ bed_type_id: e.target.value })} aria-label={`Bed type for ${bed.label}`}>
          {bedTypes.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      ) : (
        <span className="muted">{typeName}</span>
      )}
      {canEdit ? (
        <label className="check">
          <input type="checkbox" checked={bed.active} onChange={(e) => void update({ active: e.target.checked })} /> Active
        </label>
      ) : (
        <StatusPill active={bed.active} />
      )}
      <ErrorNote message={error} />
    </li>
  )
}

function AddBed({ room, bedTypes, onDone }: { room: Room; bedTypes: BedType[]; onDone: () => void }) {
  const [label, setLabel] = useState('')
  const [chosenType, setType] = useState('')
  const type = chosenType || bedTypes[0]?.id || ''
  const { busy, error, run } = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (await run(() => api(`/rooms/${room.id}/beds`, { method: 'POST', json: { label, bed_type_id: type } }))) {
      setLabel('')
      onDone()
    }
  }

  return (
    <form className="inline-form compact" onSubmit={submit}>
      <input placeholder="New bed label" value={label} onChange={(e) => setLabel(e.target.value)} required aria-label="New bed label" />
      <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Bed type">
        {bedTypes.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      <button disabled={busy || !type}>Add bed</button>
      <ErrorNote message={error} />
    </form>
  )
}

function RoomsCard({ bedTypes }: { bedTypes: BedType[] }) {
  const { branch, canManage } = useAuth()
  const branchId = branch?.id
  const { data: rooms, error, reload } = useLoad(
    () => (branchId ? api<Room[]>(`/branches/${branchId}/rooms`) : Promise.resolve([])),
    [branchId],
  )
  const [editing, setEditing] = useState<string | 'new' | null>(null)

  if (!branchId) return <Empty>You have no branch yet. An admin can give you access to one.</Empty>
  const done = () => {
    setEditing(null)
    void reload()
  }

  return (
    <Card
      title={`Rooms and beds at ${branch?.name}`}
      actions={canManage && editing === null ? <button onClick={() => setEditing('new')}>Add room</button> : undefined}
    >
      <ReadOnlyNote show={!canManage} />
      {canManage && bedTypes.length === 0 && <p className="note">Add a bed type first, then rooms.</p>}
      <ErrorNote message={error} />
      {editing === 'new' && <RoomEditor branchId={branchId} bedTypes={bedTypes} onDone={done} />}
      {rooms && rooms.length === 0 && editing !== 'new' && <Empty>No rooms yet.</Empty>}
      <div className="room-grid">
        {rooms?.map((room) => (
          <div key={room.id} className="room">
            {editing === room.id ? (
              <RoomEditor branchId={branchId} room={room} bedTypes={bedTypes} onDone={done} />
            ) : (
              <div className="room-head">
                <strong>{room.name}</strong>
                <span className="muted">{ROOM_TYPES.find((t) => t.value === room.room_type)?.label}</span>
                <StatusPill active={room.active} />
                {canManage && <button onClick={() => setEditing(room.id)}>Edit</button>}
              </div>
            )}
            <ul className="beds">
              {room.beds.map((bed) => (
                <BedRow key={bed.id} bed={bed} bedTypes={bedTypes} canEdit={canManage} onChange={() => void reload()} />
              ))}
            </ul>
            {canManage && bedTypes.length > 0 && <AddBed room={room} bedTypes={bedTypes} onDone={() => void reload()} />}
          </div>
        ))}
      </div>
    </Card>
  )
}

export default function BranchesPage() {
  const { data: bedTypes, reload } = useLoad(() => api<BedType[]>('/bed-types'), [])
  return (
    <div className="stack">
      <h1>Branches and rooms</h1>
      <BranchesCard />
      <RoomsCard bedTypes={bedTypes ?? []} />
      <BedTypesCard bedTypes={bedTypes ?? []} reload={() => void reload()} />
    </div>
  )
}
