import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { BlockReason, Branch, Room, Shift, Therapist, TimeBlock } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorNote, Field, ReadOnlyNote } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'

// --- Date helpers (dates are YYYY-MM-DD strings in the branch's own calendar) ----------

function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

function todayIn(tz: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date())
}

function mondayOf(day: string): string {
  const dow = new Date(`${day}T00:00:00Z`).getUTCDay() // 0 = Sunday
  return addDays(day, -((dow + 6) % 7))
}

function timeIn(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
}

function dayLabel(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString('en-PH', {
    timeZone: 'UTC',
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

/** ISO timestamp for a wall-clock date and time in the given timezone. */
function zonedIso(day: string, time: string, tz: string): string {
  const guess = new Date(`${day}T${time}:00Z`)
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(guess)
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value)
  const shown = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'))
  return new Date(guess.getTime() - (shown - guess.getTime())).toISOString()
}

// --- Shifts grid ---------------------------------------------------------------------

function AddShift({
  branch,
  therapist,
  day,
  onDone,
}: {
  branch: Branch
  therapist: Therapist
  day: string
  onDone: () => void
}) {
  const [open, setOpen] = useState(false)
  const [start, setStart] = useState(branch.opens_at.slice(0, 5))
  const [end, setEnd] = useState(branch.closes_at.slice(0, 5))
  const { busy, error, run } = useAction()

  if (!open)
    return (
      <button className="ghost" onClick={() => setOpen(true)} aria-label={`Add shift for ${therapist.name} on ${day}`}>
        +
      </button>
    )

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ok = await run(() =>
      api(`/branches/${branch.id}/shifts`, {
        method: 'POST',
        json: { therapist_id: therapist.id, work_date: day, start, end },
      }),
    )
    if (ok) {
      setOpen(false)
      onDone()
    }
  }

  return (
    <form className="shift-form" onSubmit={submit}>
      <input type="time" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Start" required />
      <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="End" required />
      <button className="primary" disabled={busy}>
        Add
      </button>
      <button type="button" onClick={() => setOpen(false)}>
        ×
      </button>
      <ErrorNote message={error} />
    </form>
  )
}

function ShiftChip({ shift, tz, canEdit, onDone }: { shift: Shift; tz: string; canEdit: boolean; onDone: () => void }) {
  const { busy, error, run } = useAction()
  return (
    <span className="shift">
      {timeIn(shift.starts_at, tz)}–{timeIn(shift.ends_at, tz)}
      {canEdit && (
        <button
          className="ghost"
          disabled={busy}
          aria-label="Remove shift"
          onClick={() => void run(() => api(`/shifts/${shift.id}`, { method: 'DELETE' })).then((ok) => {
            if (ok) onDone()
          })}
        >
          ×
        </button>
      )}
      <ErrorNote message={error} />
    </span>
  )
}

// --- Time off ------------------------------------------------------------------------

const REASONS: { value: BlockReason; label: string }[] = [
  { value: 'break', label: 'Break' },
  { value: 'leave', label: 'Leave' },
  { value: 'maintenance', label: 'Maintenance' },
  { value: 'other', label: 'Other' },
]

function AddTimeBlock({
  tz,
  therapists,
  rooms,
  defaultDay,
  onDone,
}: {
  tz: string
  therapists: Therapist[]
  rooms: Room[]
  defaultDay: string
  onDone: () => void
}) {
  const [who, setWho] = useState('')
  const [day, setDay] = useState(defaultDay)
  const [start, setStart] = useState('12:00')
  const [endDay, setEndDay] = useState(defaultDay)
  const [end, setEnd] = useState('13:00')
  const [reason, setReason] = useState<BlockReason>('break')
  const { busy, error, run } = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const [kind, id] = who.split(':')
    const ok = await run(() =>
      api('/time-blocks', {
        method: 'POST',
        json: {
          therapist_id: kind === 't' ? id : null,
          bed_id: kind === 'b' ? id : null,
          starts_at: zonedIso(day, start, tz),
          ends_at: zonedIso(endDay, end, tz),
          reason,
        },
      }),
    )
    if (ok) onDone()
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Who or what">
        <select value={who} onChange={(e) => setWho(e.target.value)} required>
          <option value="">Choose…</option>
          <optgroup label="Therapists">
            {therapists.map((t) => (
              <option key={t.id} value={`t:${t.id}`}>
                {t.name}
              </option>
            ))}
          </optgroup>
          <optgroup label="Beds">
            {rooms.flatMap((r) =>
              r.beds.map((b) => (
                <option key={b.id} value={`b:${b.id}`}>
                  {r.name} · {b.label}
                </option>
              )),
            )}
          </optgroup>
        </select>
      </Field>
      <Field label="Reason">
        <select value={reason} onChange={(e) => setReason(e.target.value as BlockReason)}>
          {REASONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="From">
        <span className="pair">
          <input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="From date" required />
          <input type="time" value={start} onChange={(e) => setStart(e.target.value)} aria-label="From time" required />
        </span>
      </Field>
      <Field label="Until">
        <span className="pair">
          <input type="date" value={endDay} onChange={(e) => setEndDay(e.target.value)} aria-label="Until date" required />
          <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Until time" required />
        </span>
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          Add time off
        </button>
      </div>
      <ErrorNote message={error} />
    </form>
  )
}

// --- Page ----------------------------------------------------------------------------

export default function SchedulePage() {
  const { branch: branchRef, canManage } = useAuth()
  const branchId = branchRef?.id
  const { data: branches } = useLoad(() => api<Branch[]>('/branches'), [])
  const branch = branches?.find((b) => b.id === branchId)
  const tz = branch?.timezone ?? 'Asia/Manila'
  const [week, setWeek] = useState(() => mondayOf(todayIn(tz)))
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(week, i)), [week])

  const { data: therapists } = useLoad(
    () => (branchId ? api<Therapist[]>(`/branches/${branchId}/therapists`) : Promise.resolve([])),
    [branchId],
  )
  const { data: rooms } = useLoad(
    () => (branchId ? api<Room[]>(`/branches/${branchId}/rooms`) : Promise.resolve([])),
    [branchId],
  )
  const shifts = useLoad(
    () =>
      branchId
        ? api<Shift[]>(`/branches/${branchId}/shifts?from=${week}&to=${addDays(week, 6)}`)
        : Promise.resolve([]),
    [branchId, week],
  )
  const blocks = useLoad(
    () =>
      branchId
        ? api<TimeBlock[]>(
            `/branches/${branchId}/time-blocks?from=${encodeURIComponent(zonedIso(week, '00:00', tz))}&to=${encodeURIComponent(zonedIso(addDays(week, 7), '00:00', tz))}`,
          )
        : Promise.resolve([]),
    [branchId, week, tz],
  )
  const copy = useAction()
  const removeBlock = useAction()

  if (!branchId) return <Empty>You have no branch yet.</Empty>
  if (!branch) return <p className="loading">Loading…</p>

  const activeTherapists = (therapists ?? []).filter((t) => t.active)
  const names = new Map<string, string>()
  activeTherapists.forEach((t) => names.set(t.id, t.name))
  ;(therapists ?? []).forEach((t) => names.set(t.id, t.name))
  ;(rooms ?? []).forEach((r) => r.beds.forEach((b) => names.set(b.id, `${r.name} · ${b.label}`)))

  async function copyPrevious() {
    const ok = await copy.run(() =>
      api(`/branches/${branchId}/shifts/copy-week`, {
        method: 'POST',
        json: { from_week_start: addDays(week, -7), to_week_start: week },
      }),
    )
    if (ok) void shifts.reload()
  }

  return (
    <div className="stack">
      <h1>Shifts and time off</h1>
      <Card
        title={`Week of ${dayLabel(week)} at ${branch.name}`}
        actions={
          <div className="toolbar">
            <button onClick={() => setWeek(addDays(week, -7))}>Previous week</button>
            <button onClick={() => setWeek(mondayOf(todayIn(tz)))}>This week</button>
            <button onClick={() => setWeek(addDays(week, 7))}>Next week</button>
            {canManage && (
              <button onClick={() => void copyPrevious()} disabled={copy.busy}>
                Copy last week's shifts
              </button>
            )}
          </div>
        }
      >
        <ReadOnlyNote show={!canManage} />
        <ErrorNote message={shifts.error ?? copy.error} />
        {activeTherapists.length === 0 ? (
          <Empty>Add therapists first.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table schedule">
              <thead>
                <tr>
                  <th>Therapist</th>
                  {days.map((d) => (
                    <th key={d}>{dayLabel(d)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {activeTherapists.map((t) => (
                  <tr key={t.id}>
                    <th scope="row">{t.name}</th>
                    {days.map((d) => (
                      <td key={d}>
                        {(shifts.data ?? [])
                          .filter((s) => s.therapist_id === t.id && s.work_date === d)
                          .map((s) => (
                            <ShiftChip key={s.id} shift={s} tz={tz} canEdit={canManage} onDone={() => void shifts.reload()} />
                          ))}
                        {canManage && <AddShift branch={branch} therapist={t} day={d} onDone={() => void shifts.reload()} />}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Time off and blocked beds this week">
        <p className="muted">Breaks and leave hide a therapist from booking; maintenance hides a bed.</p>
        <ErrorNote message={blocks.error ?? removeBlock.error} />
        {blocks.data && blocks.data.length === 0 ? (
          <Empty>Nothing blocked this week.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Who or what</th>
                <th>Reason</th>
                <th>From</th>
                <th>Until</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {blocks.data?.map((b) => (
                <tr key={b.id}>
                  <td>{names.get(b.therapist_id ?? b.bed_id ?? '') ?? '—'}</td>
                  <td>{REASONS.find((r) => r.value === b.reason)?.label}</td>
                  <td>
                    {dayLabel(new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date(b.starts_at)))}{' '}
                    {timeIn(b.starts_at, tz)}
                  </td>
                  <td>
                    {dayLabel(new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date(b.ends_at)))}{' '}
                    {timeIn(b.ends_at, tz)}
                  </td>
                  <td className="row-actions">
                    {canManage && (
                      <button
                        disabled={removeBlock.busy}
                        onClick={() =>
                          void removeBlock
                            .run(() => api(`/time-blocks/${b.id}`, { method: 'DELETE' }))
                            .then((ok) => {
                              if (ok) void blocks.reload()
                            })
                        }
                      >
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {canManage && (
          <AddTimeBlock
            tz={tz}
            therapists={activeTherapists}
            rooms={rooms ?? []}
            defaultDay={todayIn(tz) >= week && todayIn(tz) <= addDays(week, 6) ? todayIn(tz) : week}
            onDone={() => void blocks.reload()}
          />
        )}
      </Card>
    </div>
  )
}
