import { useEffect, useMemo, useState } from 'react'
import { getStatCondos, getStats } from './api.js'
import Chart from './Chart.jsx'
import { CHARTS, SECTIONS } from './charts.js'

// The Stats page: every chart of charts.js, drawn from the results the API computed last night
export default function StatsPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getStats().then(setData).catch(() => setError('The statistics could not be loaded.'))
  }, [])

  if (error) return <p className="empty">{error}</p>
  if (!data) return <p className="empty">Loading…</p>

  const day = data.computed_at && new Date(data.computed_at).toLocaleDateString('en-US', { dateStyle: 'long' })
  return (
    <section className="stats">
      {day && <p className="as-of">Data as of {day}</p>}
      {SECTIONS.map((section) => {
        const charts = CHARTS.filter((chart) => chart.section === section.id && data.stats[chart.name])
        return (
          <section key={section.id} className="stat-section">
            <h2>{section.title}</h2>
            <p className="note">{section.note}</p>
            {charts.length ? (
              charts.map((chart) => <StatCard key={chart.name} chart={chart} rows={data.stats[chart.name]} />)
            ) : (
              <p className="empty">No charts in this section yet.</p>
            )}
          </section>
        )
      })}
    </section>
  )
}

function StatCard({ chart, rows }) {
  const make = useMemo(() => chart.make(rows), [chart, rows])
  const [picked, setPicked] = useState(null) // the row of the bar segment clicked

  return (
    <article className="stat">
      <h3>{chart.title}</h3>
      <p className="note">{chart.note}</p>
       {chart.list && !picked && <p className="hint">Click a colored segment to see the list of its condos</p>}
      <Chart make={make} onPick={chart.list ? (index) => setPicked(rows[index]) : null} />
      {picked && <CondoList chart={chart} picked={picked} onClose={() => setPicked(null)} />}
    </article>
  )
}

// The condos behind one bar segment (an area and a value), each a link to its report, biggest first, with a search box
function CondoList({ chart, picked, onClose }) {
  const value = picked[chart.group.field] // e.g. 'red' or 'reported'
  const [condos, setCondos] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    setCondos(null)
    setError(null)
    setFilter('')
    getStatCondos(chart.name, picked.jurisdiction, value)
      .then(setCondos)
      .catch(() => setError('The list could not be loaded.'))
  }, [chart.name, picked.jurisdiction, value])

  // every typed word must appear in the name or the address, in any case
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean)
  const shown = (condos || []).filter((condo) => {
    const text = `${condo.name || ''} ${condo.address || ''}`.toLowerCase()
    return words.every((word) => text.includes(word))
  })

  return (
    <div className="condo-list">
      <div className="condo-list-bar">
        <strong>
          {words.length && condos ? `${shown.length} of ` : ''}
          {picked.condos} condos · {picked.jurisdiction} · {chart.group.names[value]}
        </strong>
        <input
          type="search"
          placeholder="Search name or address"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          aria-label="Search the list"
        />
        <button onClick={onClose}>Close</button>
      </div>
      {error && <p className="empty">{error}</p>}
      {!condos && !error && <p className="empty">Loading…</p>}
      {condos && !shown.length && <p className="empty">No condo in this list matches that.</p>}
      {condos && (
        <ul>
          {shown.map((condo) => (
            <li key={condo.folio}>
              <a href={`?folio=${condo.folio}`}>{condo.name || condo.address}</a>
              <span>
                {condo.name && condo.address ? `${condo.address} · ` : ''}
                {condo.units} units
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

