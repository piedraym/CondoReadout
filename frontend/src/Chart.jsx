import * as Plot from '@observablehq/plot'
import { useEffect, useRef } from 'react'

// One Observable Plot chart. `make(width)` returns the Plot options; it is drawn again when the
// width of its box changes, so the chart always fits the page.
export default function Chart({ make }) {
  const box = useRef(null)

  useEffect(() => {
    const node = box.current
    let chart = null
    const draw = () => {
      chart?.remove()
      chart = Plot.plot(make(node.clientWidth))
      node.append(chart)
    }
    const resize = new ResizeObserver(draw)
    resize.observe(node)
    return () => {
      resize.disconnect()
      chart?.remove()
    }
  }, [make])

  return <div className="chart" ref={box} />
}
