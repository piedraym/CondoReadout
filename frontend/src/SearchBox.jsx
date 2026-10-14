import { useEffect, useState } from 'react'
import { searchCondos } from './api.js'

const WAIT_MS = 150 // wait for a pause in the typing before asking the API

// Address box with suggestions: arrows to move, Enter to pick, Escape to close
export default function SearchBox({ onPick }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [active, setActive] = useState(-1)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!query.trim()) return
    const controller = new AbortController() // a newer keystroke cancels this request
    const timer = setTimeout(() => {
      searchCondos(query, controller.signal)
        .then((rows) => {
          setResults(rows)
          setActive(-1)
          setError(null)
        })
        .catch((err) => {
          if (err.name !== 'AbortError') setError('The search is not available right now.')
        })
    }, WAIT_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query])

  function pick(row) {
    setQuery(row.address)
    setOpen(false)
    onPick(row)
  }

  function onKeyDown(event) {
    if (event.key === 'ArrowDown') setActive((i) => Math.min(i + 1, results.length - 1))
    else if (event.key === 'ArrowUp') setActive((i) => Math.max(i - 1, 0))
    else if (event.key === 'Enter' && results.length) pick(results[Math.max(active, 0)])
    else if (event.key === 'Escape') setOpen(false)
    else return
    event.preventDefault()
  }

  const showList = open && query.trim()

  return (
    <div className="search">
      <input
        autoFocus
        placeholder="Address or building name, e.g. 1717 N Bayshore"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setOpen(true)
        }}
        onKeyDown={onKeyDown}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-label="Search a condo"
      />
      {showList && (
        <ul className="results">
          {error && <li className="empty">{error}</li>}
          {!error && results.length === 0 && (
            <li className="empty">No City of Miami condo matches that.</li>
          )}
          {results.map((row, i) => (
            <li
              key={`${row.folio} ${row.address}`}
              className={i === active ? 'active' : ''}
              onMouseDown={() => pick(row)} // before the input loses focus and closes the list
            >
              <div className="address">
                {row.address}
                {row.unbuilt && <span className="unbuilt">Not built yet</span>}
              </div>
              <div className="details">
                {row.name} · {row.units} units · Miami, FL {row.zip}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
