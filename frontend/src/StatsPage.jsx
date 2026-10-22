import { useEffect, useMemo, useState } from 'react'
import { getStats } from './api.js'
import Chart from './Chart.jsx'
import { CHARTS } from './charts.js'

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
      {CHARTS.filter((chart) => data.stats[chart.name]).map((chart) => (
        <StatCard key={chart.name} chart={chart} rows={data.stats[chart.name]} />
      ))}
    </section>
  )
}

function StatCard({ chart, rows }) {
  const make = useMemo(() => chart.make(rows), [chart, rows])
  return (
    <article className="stat">
      <h2>{chart.title}</h2>
      <p className="note">{chart.note}</p>
      <Chart make={make} />
      <details>
        <summary>Show the data</summary>
        <table>
          <thead>
            <tr>{chart.columns.map(([key, label]) => <th key={key}>{label}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>{chart.columns.map(([key]) => <td key={key}>{row[key]}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </details>
    </article>
  )
}
