import { useEffect, useState } from 'react'
import { getLoads } from './api.js'

const DAYS = 14 // the same as ADMIN_DAYS in backend/api.py
const KEY = 'condoreadout-admin' // the admin password, kept until the browser tab is closed

// The last DAYS days in Miami, newest first, as 'YYYY-MM-DD' like the API's `day`
function lastDays() {
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
  const noon = new Date(`${today}T12:00:00Z`)
  return Array.from({ length: DAYS }, (_, i) => new Date(noon - i * 86400000).toISOString().slice(0, 10))
}

// 'Nov' over '5', so fourteen days fit next to the names
function DayLabel({ day }) {
  const date = new Date(`${day}T12:00:00Z`)
  return <>{date.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' })}<br />{date.getUTCDate()}</>
}

const SHORT_AREA = { 'Unincorporated Miami-Dade County': 'Unincorporated Miami-Dade', 'City of Miami Beach': 'Miami Beach' }

// One line per run for the cell's tooltip: '02:05 failed: 503 Server Error…' or '02:03 ok · 16,853 rows · 15 s'
function runLine(run) {
  const at = new Date(run.started_at).toLocaleTimeString('en-GB', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit' })
  if (run.status === 'failed') return `${at} failed: ${run.error}`
  if (run.status === 'running') return `${at} running`
  const seconds = run.finished_at ? Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000) : null
  const rows = run.row_count == null ? '' : ` · ${run.row_count.toLocaleString('en-US')} rows`
  return `${at} ok${rows}${seconds == null ? '' : ` · ${seconds} s`}`
}

const MARK = { ok: '✓', failed: '✕', running: '…' }

// Every nightly load of the last two weeks: a row per source and area, a column per day.
// The cell shows the last run of that day; its tooltip lists them all.
export default function AdminPage() {
  const [password, setPassword] = useState(() => sessionStorage.getItem(KEY) || '')
  const [runs, setRuns] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getLoads(password)
      .then((rows) => { setRuns(rows); setError(null) })
      .catch((err) => setError(err.message))
  }, [password])

  if (error === 'password') return <PasswordForm onSubmit={(given) => { sessionStorage.setItem(KEY, given); setPassword(given) }} />
  if (error) return <p className="empty">{error}</p>
  if (!runs) return <p className="empty">Loading…</p>

  const days = lastDays()
  const byLoad = new Map() // 'recert|City of Miami' -> {source, area, days: {day: [runs]}}
  for (const run of runs) {
    const key = `${run.source}|${run.area}`
    if (!byLoad.has(key)) byLoad.set(key, { source: run.source, area: run.area, days: {} })
    const load = byLoad.get(key)
    ;(load.days[run.day] ??= []).push(run)
  }
  const loads = [...byLoad.values()].sort((a, b) => a.source.localeCompare(b.source) || a.area.localeCompare(b.area))

  return (
    <section className="admin">
      <h2>Nightly loads</h2>
      <p className="note">Last {DAYS} days, newest on the left. Hover a cell to see every run of that day.</p>
      <table className="loads">
        <thead>
          <tr>
            <th>Source</th>
            <th>Area</th>
            {days.map((day) => <th key={day} className="day"><DayLabel day={day} /></th>)}
          </tr>
        </thead>
        <tbody>
          {loads.map((load) => (
            <tr key={`${load.source}|${load.area}`}>
              <td>{load.source}</td>
              <td>{SHORT_AREA[load.area] ?? load.area}</td>
              {days.map((day) => {
                const dayRuns = load.days[day] || []
                const last = dayRuns.at(-1)
                return (
                  <td key={day} className={`load ${last ? last.status : 'none'}`} title={dayRuns.map(runLine).join('\n') || 'no load'}>
                    {last ? MARK[last.status] : '·'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function PasswordForm({ onSubmit }) {
  const [given, setGiven] = useState('')
  return (
    <form className="admin-login" onSubmit={(event) => { event.preventDefault(); onSubmit(given) }}>
      <label>
        Admin password <input type="password" value={given} onChange={(event) => setGiven(event.target.value)} autoFocus />
      </label>
      <button type="submit">Open</button>
    </form>
  )
}
