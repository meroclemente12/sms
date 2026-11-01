import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DragEvent, MouseEvent } from 'react'
import { api } from '../api'
import type { Board as BoardData, BoardItem, Booking, Span } from '../api'
import { useAuth } from '../auth'
import { ErrorNote, StatusBadge } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import BookingDrawer from '../desk/BookingDrawer'
import NewBooking from '../desk/NewBooking'
import type { BookingStart } from '../desk/NewBooking'
import type { Names } from '../desk/SlotPicker'
import { addDays, clockIn, dayOf, hhmm, longDayLabel, minutesOf, todayIn, zonedIso } from '../time'

const PX_PER_MIN = 1.6 // 15 minutes = 24px
const STEP = 15
const REFRESH_MS = 20_000
const DEFAULT_TZ = 'Asia/Manila'

type View = 'therapists' | 'beds'

interface Lane {
  id: string
  title: string
  sub: string
  colour: string | null
  shifts: Span[] | null // null = always open (beds)
}

function readView(): View {
  try {
    return localStorage.getItem('banahaw.boardView') === 'beds' ? 'beds' : 'therapists'
  } catch {
    return 'therapists'
  }
}

export default function Board() {
  const { branch } = useAuth()
  const [day, setDay] = useState(() => todayIn(DEFAULT_TZ))
  const [view, setViewState] = useState<View>(readView)
  const [showCancelled, setShowCancelled] = useState(false)
  const [newBooking, setNewBooking] = useState<BookingStart | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const move = useAction()

  const { data: board, error, reload } = useLoad(
    () =>
      branch
        ? api<BoardData>(`/branches/${branch.id}/board?date=${day}&include_cancelled=${showCancelled}`)
        : Promise.resolve(null),
    [branch?.id, day, showCancelled],
  )
  const tz = board?.timezone ?? DEFAULT_TZ

  // Keep every desk's board current: refresh every 20 s while the tab is visible.
  useEffect(() => {
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') void reload()
    }, REFRESH_MS)
    return () => window.clearInterval(t)
  }, [reload])

  function setView(v: View) {
    setViewState(v)
    try {
      localStorage.setItem('banahaw.boardView', v)
    } catch {
      // ignore
    }
  }

  const names: Names = useMemo(() => {
    const t = new Map(board?.therapists.map((x) => [x.id, x.name]))
    const b = new Map(board?.beds.map((x) => [x.id, `${x.room_name} · ${x.label}`]))
    return { therapist: (id) => t.get(id) ?? 'Therapist', bed: (id) => b.get(id) ?? 'a bed' }
  }, [board])

  const lanes: Lane[] = useMemo(() => {
    if (!board) return []
    if (view === 'beds')
      return board.beds.map((b) => ({
        id: b.id,
        title: b.label,
        sub: b.room_type === 'couples' ? `${b.room_name} (couples)` : b.room_name,
        colour: null,
        shifts: null,
      }))
    // Therapists on shift today first, then the rest.
    return [...board.therapists]
      .sort((a, b) => Number(b.shifts.length > 0) - Number(a.shifts.length > 0))
      .map((t) => ({
        id: t.id,
        title: t.name,
        sub:
          t.shifts.length > 0
            ? t.shifts.map((s) => `${clockIn(s.starts_at, tz)}–${clockIn(s.ends_at, tz)}`).join(', ')
            : 'Day off',
        colour: t.colour,
        shifts: t.shifts,
      }))
  }, [board, view, tz])

  // The visible hours: opening to closing, stretched to fit anything booked outside them.
  const [startMin, endMin] = useMemo(() => {
    if (!board) return [9 * 60, 22 * 60]
    let s = minutesOf(board.opens_at)
    let e = minutesOf(board.closes_at)
    if (e <= s) e += 24 * 60
    const dayStart = Date.parse(zonedIso(day, '00:00', tz))
    for (const it of board.items) {
      s = Math.min(s, Math.floor((Date.parse(it.starts_at) - dayStart) / 60000 / 60) * 60)
      e = Math.max(e, Math.ceil((Date.parse(it.blocked_until) - dayStart) / 60000 / 60) * 60)
    }
    return [Math.max(0, s), Math.min(24 * 60, e)]
  }, [board, day, tz])

  const dayStartMs = useMemo(() => Date.parse(zonedIso(day, '00:00', tz)), [day, tz])
  const y = useCallback((iso: string) => ((Date.parse(iso) - dayStartMs) / 60000 - startMin) * PX_PER_MIN, [dayStartMs, startMin])
  const height = (endMin - startMin) * PX_PER_MIN

  function minuteAt(e: { clientY: number; currentTarget: Element }, grabOffsetMin = 0): number {
    const rect = e.currentTarget.getBoundingClientRect()
    const raw = startMin + (e.clientY - rect.top) / PX_PER_MIN - grabOffsetMin
    return Math.max(startMin, Math.min(endMin - STEP, Math.round(raw / STEP) * STEP))
  }

  function laneClick(e: MouseEvent<HTMLDivElement>, lane: Lane) {
    if (e.target !== e.currentTarget) return
    const m = minuteAt(e)
    setNewBooking({
      walkIn: false,
      day,
      time: hhmm(m),
      therapistId: view === 'therapists' ? lane.id : null,
      bedId: view === 'beds' ? lane.id : null,
    })
  }

  // --- Drag to reschedule ------------------------------------------------------------
  const drag = useRef<{ item: BoardItem; grabMin: number } | null>(null)
  const [dropHint, setDropHint] = useState<{ lane: string; minute: number } | null>(null)

  function onDragStart(e: DragEvent<HTMLButtonElement>, item: BoardItem) {
    const rect = e.currentTarget.getBoundingClientRect()
    drag.current = { item, grabMin: (e.clientY - rect.top) / PX_PER_MIN }
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', item.booking_id)
  }

  function onDragOver(e: DragEvent<HTMLDivElement>, lane: Lane) {
    if (!drag.current) return
    e.preventDefault()
    const minute = minuteAt(e, drag.current.grabMin)
    if (dropHint?.lane !== lane.id || dropHint.minute !== minute) setDropHint({ lane: lane.id, minute })
  }

  async function onDrop(e: DragEvent<HTMLDivElement>, lane: Lane) {
    e.preventDefault()
    const d = drag.current
    drag.current = null
    setDropHint(null)
    if (!d) return
    const minute = minuteAt(e, d.grabMin)
    const startsAt = zonedIso(day, hhmm(minute), tz)
    const sameLane = view === 'therapists' ? d.item.therapist_id === lane.id : d.item.bed_id === lane.id
    if (sameLane && Date.parse(startsAt) === Date.parse(d.item.starts_at)) return
    const guest = view === 'therapists' ? { therapist_id: lane.id } : { bed_id: lane.id, therapist_id: d.item.therapist_id }
    const ok = await move.run(() =>
      api<Booking>(`/bookings/${d.item.booking_id}/reschedule`, {
        method: 'POST',
        json: { starts_at: startsAt, guests: [guest] },
      }),
    )
    if (ok) setFlash(`Moved ${d.item.client_name} to ${clockIn(startsAt, tz)} with ${view === 'therapists' ? lane.title : names.therapist(d.item.therapist_id)}`)
    void reload()
  }

  useEffect(() => {
    if (!flash) return
    const t = window.setTimeout(() => setFlash(null), 4000)
    return () => window.clearTimeout(t)
  }, [flash])

  if (!branch) return <p className="note">You are not assigned to a branch yet. Ask an admin to add you to one.</p>

  const today = todayIn(tz)
  const nowY = day === today ? y(new Date().toISOString()) : null
  const hours: number[] = []
  for (let m = Math.ceil(startMin / 60) * 60; m < endMin; m += 60) hours.push(m)

  const counts = countByStatus(board?.items ?? [])

  return (
    <div className="board-page">
      <header className="board-toolbar">
        <div className="day-nav">
          <button onClick={() => setDay(addDays(day, -1))} aria-label="Previous day">
            ‹
          </button>
          <button onClick={() => setDay(today)} disabled={day === today}>
            Today
          </button>
          <button onClick={() => setDay(addDays(day, 1))} aria-label="Next day">
            ›
          </button>
          <input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="Day" />
          <h1>{longDayLabel(day)}</h1>
        </div>
        <div className="board-actions">
          <div className="segmented-buttons" role="group" aria-label="Show lanes by">
            <button className={view === 'therapists' ? 'on' : ''} aria-pressed={view === 'therapists'} onClick={() => setView('therapists')}>
              Therapists
            </button>
            <button className={view === 'beds' ? 'on' : ''} aria-pressed={view === 'beds'} onClick={() => setView('beds')}>
              Beds
            </button>
          </div>
          <label className="check small">
            <input type="checkbox" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} />
            Show cancelled
          </label>
          <button onClick={() => setNewBooking({ walkIn: true, day: today })}>Walk-in</button>
          <button className="primary" onClick={() => setNewBooking({ walkIn: false, day })}>
            New booking
          </button>
        </div>
      </header>

      <p className="board-summary muted">
        {counts.total === 0
          ? 'No bookings yet.'
          : `${counts.total} guest${counts.total === 1 ? '' : 's'}: ${counts.parts.join(', ')}.`}{' '}
        Click an empty time to book it. Drag a booking to move it.
      </p>
      <ErrorNote message={error} />
      <ErrorNote message={move.error} />
      {flash && (
        <p className="flash" role="status">
          {flash}
        </p>
      )}

      {board && lanes.length === 0 && (
        <p className="note">
          Nothing to show yet. Add {view === 'beds' ? 'rooms and beds' : 'therapists and their shifts'} in Setup first.
        </p>
      )}

      {board && lanes.length > 0 && (
        <div className="board-scroll">
          <div
            className="board-grid"
            style={{
              gridTemplateColumns: `56px repeat(${lanes.length}, minmax(128px, 1fr))`,
              minWidth: 56 + lanes.length * 128,
            }}
          >
            <div className="lane-head corner" />
            {lanes.map((l) => (
              <div key={l.id} className="lane-head">
                {l.colour && <span className="swatch" style={{ background: l.colour }} />}
                <strong>{l.title}</strong>
                <span className="muted small">{l.sub}</span>
              </div>
            ))}

            <div className="time-gutter" style={{ height }}>
              {hours.map((m) => (
                <span key={m} style={{ top: (m - startMin) * PX_PER_MIN }}>
                  {clockIn(zonedIso(day, hhmm(m), tz), tz)}
                </span>
              ))}
            </div>

            {lanes.map((lane) => (
              <div
                key={lane.id}
                className="lane"
                style={{ height }}
                onClick={(e) => laneClick(e, lane)}
                onDragOver={(e) => onDragOver(e, lane)}
                onDragLeave={() => setDropHint(null)}
                onDrop={(e) => void onDrop(e, lane)}
                title="Click to book this time"
              >
                {hours.map((m) => (
                  <div key={m} className="hour-line" style={{ top: (m - startMin) * PX_PER_MIN }} />
                ))}
                {lane.shifts && offShift(lane.shifts, day, tz, startMin, endMin).map(([a, b]) => (
                  <div key={a} className="off-shift" style={{ top: (a - startMin) * PX_PER_MIN, height: (b - a) * PX_PER_MIN }} />
                ))}
                {board.blocks
                  .filter((bl) => (view === 'therapists' ? bl.therapist_id : bl.bed_id) === lane.id)
                  .map((bl) => (
                    <div key={bl.id} className="time-block" style={{ top: y(bl.starts_at), height: y(bl.ends_at) - y(bl.starts_at) }}>
                      {bl.reason}
                    </div>
                  ))}
                {board.items
                  .filter((it) => (view === 'therapists' ? it.therapist_id : it.bed_id) === lane.id)
                  .map((it) => (
                    <BookingBlock
                      key={it.id}
                      item={it}
                      tz={tz}
                      top={y(it.starts_at)}
                      height={y(it.ends_at) - y(it.starts_at)}
                      cleanup={view === 'beds' ? y(it.blocked_until) - y(it.ends_at) : 0}
                      sub={view === 'beds' ? names.therapist(it.therapist_id) : names.bed(it.bed_id)}
                      onOpen={() => setOpenId(it.booking_id)}
                      onDragStart={onDragStart}
                      onDragEnd={() => {
                        drag.current = null
                        setDropHint(null)
                      }}
                    />
                  ))}
                {dropHint?.lane === lane.id && drag.current && (
                  <div
                    className="drop-hint"
                    style={{
                      top: (dropHint.minute - startMin) * PX_PER_MIN,
                      height: (Date.parse(drag.current.item.ends_at) - Date.parse(drag.current.item.starts_at)) / 60000 * PX_PER_MIN,
                    }}
                  >
                    {clockIn(zonedIso(day, hhmm(dropHint.minute), tz), tz)}
                  </div>
                )}
                {nowY !== null && nowY >= 0 && nowY <= height && <div className="now-line" style={{ top: nowY }} />}
              </div>
            ))}
          </div>
        </div>
      )}

      {newBooking && board && (
        <NewBooking
          branchId={branch.id}
          tz={tz}
          start={newBooking}
          names={names}
          onClose={() => setNewBooking(null)}
          onBooked={(b) => {
            setNewBooking(null)
            const bookedDay = dayOf(b.starts_at, tz)
            if (bookedDay !== day) setDay(bookedDay)
            else void reload()
            setFlash(`Booked ${b.client.name} at ${clockIn(b.starts_at, tz)} with ${b.items.map((i) => i.therapist_name).join(' and ')}`)
            setOpenId(b.id)
          }}
        />
      )}
      {openId && (
        <BookingDrawer
          bookingId={openId}
          tz={tz}
          names={names}
          onClose={() => setOpenId(null)}
          onChanged={() => void reload()}
        />
      )}
    </div>
  )
}

function BookingBlock({
  item,
  tz,
  top,
  height,
  cleanup,
  sub,
  onOpen,
  onDragStart,
  onDragEnd,
}: {
  item: BoardItem
  tz: string
  top: number
  height: number
  cleanup: number
  sub: string
  onOpen: () => void
  onDragStart: (e: DragEvent<HTMLButtonElement>, item: BoardItem) => void
  onDragEnd: () => void
}) {
  // Couples bookings move from the details panel so both guests stay together.
  const draggable = (item.status === 'booked' || item.status === 'confirmed') && item.guest_count === 1
  const who = item.guest_name ? `${item.guest_name} (with ${item.client_name})` : item.client_name
  return (
    <>
      <button
        className={`booking st-${item.status}`}
        style={{ top, height: Math.max(height, 22) }}
        onClick={onOpen}
        draggable={draggable}
        onDragStart={(e) => onDragStart(e, item)}
        onDragEnd={onDragEnd}
        aria-label={`${who}, ${item.service_name} ${item.variant_label}, ${clockIn(item.starts_at, tz)}`}
      >
        <span className="b-line">
          <strong>{who}</strong>
          {item.guest_count > 1 && <span className="tag">couples</span>}
          {item.therapist_requested && <span className="tag" title="Therapist requested">★</span>}
          {item.has_notes && <span className="tag" title="Has notes">✎</span>}
        </span>
        <span className="b-line small">
          {clockIn(item.starts_at, tz)}–{clockIn(item.ends_at, tz)} · {item.service_name} {item.variant_label}
        </span>
        {height > 60 && <span className="b-line small muted-ink">{sub}</span>}
        {height > 80 && (
          <span className="b-line">
            <StatusBadge status={item.status} />
          </span>
        )}
      </button>
      {cleanup > 0 && <div className="cleanup" style={{ top: top + height, height: cleanup }} title="Cleanup" />}
    </>
  )
}

/** Stretches of the visible day (in minutes) that no shift covers. */
function offShift(shifts: Span[], day: string, tz: string, startMin: number, endMin: number): [number, number][] {
  const dayStart = Date.parse(zonedIso(day, '00:00', tz))
  const on = shifts
    .map((s) => [(Date.parse(s.starts_at) - dayStart) / 60000, (Date.parse(s.ends_at) - dayStart) / 60000] as const)
    .sort((a, b) => a[0] - b[0])
  const out: [number, number][] = []
  let cursor = startMin
  for (const [a, b] of on) {
    if (a > cursor) out.push([cursor, Math.min(a, endMin)])
    cursor = Math.max(cursor, b)
  }
  if (cursor < endMin) out.push([cursor, endMin])
  return out.filter(([a, b]) => b > a)
}

function countByStatus(items: BoardItem[]) {
  const n = { waiting: 0, here: 0, done: 0, cancelled: 0 }
  for (const it of items) {
    if (it.status === 'booked' || it.status === 'confirmed') n.waiting++
    else if (it.status === 'checked_in' || it.status === 'in_service') n.here++
    else if (it.status === 'completed') n.done++
    else n.cancelled++
  }
  const parts = [
    n.waiting && `${n.waiting} expected`,
    n.here && `${n.here} in the spa`,
    n.done && `${n.done} done`,
    n.cancelled && `${n.cancelled} cancelled or no-show`,
  ].filter(Boolean) as string[]
  return { total: items.length, parts }
}
