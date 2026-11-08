import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DragEvent, MouseEvent } from 'react'
import { api } from '../api'
import type { Board as BoardData, BoardItem, BoardTherapist, Booking, Span } from '../api'
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
type GenderFilter = 'all' | 'male' | 'female'

const ACTIVE = new Set(['booked', 'confirmed', 'checked_in', 'in_service', 'completed'])

interface Lane {
  id: string
  title: string
  sub: string
  colour: string | null
  shifts: Span[] | null // null = always open (beds, or a therapist clocked in today)
  /** Today's therapist lanes: clocked in (true) or not (false). Undefined otherwise. */
  clockedIn?: boolean
}

function readGender(): GenderFilter {
  try {
    const g = localStorage.getItem('banahaw.boardGender')
    return g === 'male' || g === 'female' ? g : 'all'
  } catch {
    return 'all'
  }
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
  const [gender, setGenderState] = useState<GenderFilter>(readGender)
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

  function setGender(g: GenderFilter) {
    setGenderState(g)
    try {
      localStorage.setItem('banahaw.boardGender', g)
    } catch {
      // ignore
    }
  }

  // Keyboard shortcuts for the desk. Ignored while typing, with Ctrl/Cmd/Alt held,
  // or while a booking panel or any other dialog is open.
  const shortcuts = useRef<(e: KeyboardEvent) => void>(() => {})
  shortcuts.current = (e: KeyboardEvent) => {
    if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return
    if (isTyping(e.target) || newBooking || openId || document.querySelector('[role="dialog"], dialog[open]')) return
    const key = e.key.toLowerCase()
    const byKey: Record<string, GenderFilter> = { a: 'all', m: 'male', f: 'female' }
    if (key in byKey) {
      setGender(byKey[key])
      setView('therapists') // the gender filter only applies to the therapist lanes
    } else if (key === 't') setView('therapists')
    else if (key === 'b') setView('beds')
    else if (key === 'w') setNewBooking({ walkIn: true, day: todayIn(tz) })
    else if (key === 'n') setNewBooking({ walkIn: false, day })
    else return
    e.preventDefault()
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => shortcuts.current(e)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

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
    const isToday = board.date === todayIn(tz)
    const shiftText = (t: BoardTherapist) =>
      t.shifts.length > 0 ? t.shifts.map((s) => `${clockIn(s.starts_at, tz)}–${clockIn(s.ends_at, tz)}`).join(', ') : 'Day off'
    // Today: clocked-in therapists first (only they can be booked). Other days: on shift first.
    const rank = (t: BoardTherapist) => (isToday ? Number(Boolean(t.clocked_in_at)) * 2 : 0) + Number(t.shifts.length > 0)
    return [...board.therapists]
      .filter((t) => gender === 'all' || t.gender === gender)
      .sort((a, b) => rank(b) - rank(a))
      .map((t) => ({
        id: t.id,
        title: t.name,
        sub: !isToday
          ? shiftText(t)
          : t.clocked_in_at
            ? `In since ${clockIn(t.clocked_in_at, tz)}`
            : 'Not clocked in',
        colour: t.colour,
        // Today the clock-in decides availability: open all day when in, closed when out.
        shifts: !isToday ? t.shifts : t.clocked_in_at ? null : [],
        clockedIn: isToday ? Boolean(t.clocked_in_at) : undefined,
      }))
  }, [board, view, tz, gender])

  const clock = useAction()
  async function toggleClock(lane: Lane) {
    const action = lane.clockedIn ? 'clock-out' : 'clock-in'
    const ok = await clock.run(() => api(`/therapists/${lane.id}/${action}`, { method: 'POST' }))
    if (ok) setFlash(`${lane.title} ${lane.clockedIn ? 'clocked out' : 'clocked in'}`)
    void reload()
  }

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

  /** Snaps to the nearest step (dragging), or with `floor` to the 15-minute cell under the pointer. */
  function minuteAt(e: { clientY: number; currentTarget: Element }, grabOffsetMin = 0, floor = false): number {
    const rect = e.currentTarget.getBoundingClientRect()
    const raw = startMin + (e.clientY - rect.top) / PX_PER_MIN - grabOffsetMin
    const snapped = (floor ? Math.floor(raw / STEP) : Math.round(raw / STEP)) * STEP
    return Math.max(startMin, Math.min(endMin - STEP, snapped))
  }

  // --- Hover: highlight the lane (column) and the 15-minute row under the pointer ----
  const [hover, setHover] = useState<{ lane: string; minute: number } | null>(null)

  /** Therapists free for the whole hovered 15 minutes: on shift, not booked, not blocked. */
  const freeAtHover = useMemo(() => {
    if (!board || !hover) return null
    const from = Date.parse(zonedIso(day, hhmm(hover.minute), tz))
    const to = from + STEP * 60000
    const overlaps = (a: string, b: string) => Date.parse(a) < to && Date.parse(b) > from
    const busy = new Set<string>()
    for (const it of board.items) if (ACTIVE.has(it.status) && overlaps(it.starts_at, it.ends_at)) busy.add(it.therapist_id)
    for (const bl of board.blocks) if (bl.therapist_id && overlaps(bl.starts_at, bl.ends_at)) busy.add(bl.therapist_id)
    const isToday = board.date === todayIn(tz)
    const free = board.therapists.filter(
      (t) =>
        !busy.has(t.id) &&
        (isToday
          ? Boolean(t.clocked_in_at)
          : t.shifts.some((sh) => Date.parse(sh.starts_at) <= from && Date.parse(sh.ends_at) >= to)),
    )
    return {
      male: free.filter((t) => t.gender === 'male').length,
      female: free.filter((t) => t.gender === 'female').length,
      total: free.length,
    }
  }, [board, hover, day, tz])

  function onLaneMove(e: MouseEvent<HTMLDivElement>, lane: Lane) {
    // Over a booking the click opens that booking, so no slot is highlighted.
    if (e.target !== e.currentTarget) {
      if (hover) setHover(null)
      return
    }
    const minute = minuteAt(e, 0, true)
    if (hover?.lane !== lane.id || hover.minute !== minute) setHover({ lane: lane.id, minute })
  }

  function laneClick(e: MouseEvent<HTMLDivElement>, lane: Lane) {
    if (e.target !== e.currentTarget) return
    const m = minuteAt(e, 0, true)
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
          {view === 'therapists' && (
            <div className="segmented-buttons" role="group" aria-label="Therapist gender">
              {(['all', 'male', 'female'] as const).map((g) => (
                <button
                  key={g}
                  className={gender === g ? 'on' : ''}
                  aria-pressed={gender === g}
                  aria-keyshortcuts={g[0].toUpperCase()}
                  title={`Shortcut: ${g[0].toUpperCase()}`}
                  onClick={() => setGender(g)}
                >
                  {g === 'all' ? 'All' : g === 'male' ? 'Male' : 'Female'} <kbd>{g[0].toUpperCase()}</kbd>
                </button>
              ))}
            </div>
          )}
          <div className="segmented-buttons" role="group" aria-label="Show lanes by">
            <button
              className={view === 'therapists' ? 'on' : ''}
              aria-pressed={view === 'therapists'}
              aria-keyshortcuts="T"
              title="Shortcut: T"
              onClick={() => setView('therapists')}
            >
              Therapists <kbd>T</kbd>
            </button>
            <button
              className={view === 'beds' ? 'on' : ''}
              aria-pressed={view === 'beds'}
              aria-keyshortcuts="B"
              title="Shortcut: B"
              onClick={() => setView('beds')}
            >
              Beds <kbd>B</kbd>
            </button>
          </div>
          <label className="check small">
            <input type="checkbox" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} />
            Show cancelled
          </label>
          <button aria-keyshortcuts="W" title="Shortcut: W" onClick={() => setNewBooking({ walkIn: true, day: today })}>
            Walk-in <kbd>W</kbd>
          </button>
          <button className="primary" aria-keyshortcuts="N" title="Shortcut: N" onClick={() => setNewBooking({ walkIn: false, day })}>
            New booking <kbd>N</kbd>
          </button>
        </div>
      </header>

      <p className="board-summary muted">
        {counts.total === 0
          ? 'No bookings yet.'
          : `${counts.total} guest${counts.total === 1 ? '' : 's'}: ${counts.parts.join(', ')}.`}{' '}
        Click an empty time to book it. Drag a booking to move it.
      </p>
      <p className="free-summary" aria-live="polite">
        {hover && freeAtHover ? (
          <>
            Free at {clockIn(zonedIso(day, hhmm(hover.minute), tz), tz)}: <strong>{freeAtHover.male}</strong> male ·{' '}
            <strong>{freeAtHover.female}</strong> female · <strong>{freeAtHover.total}</strong> in all
          </>
        ) : (
          <span className="muted">Point at a time to see how many male and female therapists are free.</span>
        )}
      </p>
      <ErrorNote message={error} />
      <ErrorNote message={move.error} />
      <ErrorNote message={clock.error} />
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
              <div key={l.id} className={`lane-head${hover?.lane === l.id ? ' hover-col' : ''}`}>
                {l.colour && <span className="swatch" style={{ background: l.colour }} />}
                <strong>{l.title}</strong>
                <span className="muted small">{l.sub}</span>
                {l.clockedIn !== undefined && (
                  <button
                    className={`clock-toggle${l.clockedIn ? ' on' : ''}`}
                    role="switch"
                    aria-checked={l.clockedIn}
                    aria-label={`${l.title} available for booking`}
                    title={l.clockedIn ? 'Clocked in. Click to clock out.' : 'Not clocked in. Click to clock in.'}
                    disabled={clock.busy}
                    onClick={() => void toggleClock(l)}
                  >
                    <span className="dot" aria-hidden="true" />
                    {l.clockedIn ? 'Available' : 'Clock in'}
                  </button>
                )}
              </div>
            ))}

            <div className="time-gutter" style={{ height }}>
              {hours.map((m) => (
                <span key={m} style={{ top: (m - startMin) * PX_PER_MIN }}>
                  {clockIn(zonedIso(day, hhmm(m), tz), tz)}
                </span>
              ))}
              {hover && (
                <span className="hover-time" style={{ top: (hover.minute - startMin) * PX_PER_MIN, height: STEP * PX_PER_MIN }}>
                  {clockIn(zonedIso(day, hhmm(hover.minute), tz), tz)}
                </span>
              )}
            </div>

            {lanes.map((lane) => (
              <div
                key={lane.id}
                className={`lane${hover?.lane === lane.id ? ' hover-col' : ''}`}
                style={{ height }}
                onClick={(e) => laneClick(e, lane)}
                onMouseMove={(e) => onLaneMove(e, lane)}
                onMouseLeave={() => setHover(null)}
                onDragStart={() => setHover(null)}
                onDragOver={(e) => onDragOver(e, lane)}
                onDragLeave={() => setDropHint(null)}
                onDrop={(e) => void onDrop(e, lane)}
                title="Click to book this time"
              >
                {hours.map((m) => (
                  <div key={m} className="hour-line" style={{ top: (m - startMin) * PX_PER_MIN }} />
                ))}
                {hover && (
                  <div
                    className={hover.lane === lane.id ? 'hover-cell' : 'hover-row'}
                    style={{ top: (hover.minute - startMin) * PX_PER_MIN, height: STEP * PX_PER_MIN }}
                  >
                    {hover.lane === lane.id && freeAtHover && (
                      <span className="hover-free" title="Therapists free at this time: male, female, in all">
                        {freeAtHover.male} M · {freeAtHover.female} F · {freeAtHover.total} free
                      </span>
                    )}
                  </div>
                )}
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
            setFlash(`Booked ${b.client.name} at ${clockIn(b.starts_at, tz)} with ${listNames(b.items.map((i) => i.therapist_name))}`)
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
  // Couples and group bookings move from the details panel so every guest stays together.
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
          {item.couples ? (
            <span className="tag">couples</span>
          ) : (
            item.guest_count > 1 && <span className="tag">group of {item.guest_count}</span>
          )}
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

/** True when a key press is going into a text box, dropdown or other editable field. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  if (target instanceof HTMLInputElement) return !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(target.type)
  return target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement
}

function listNames(names: string[]): string {
  return names.length <= 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}
