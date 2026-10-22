import * as Plot from '@observablehq/plot'
import { useEffect, useRef } from 'react'

// One Observable Plot chart. `make(width)` returns the Plot options; it is drawn again when the
// width of its box changes, so the chart always fits the page. `onPick(datum)` runs when a mark
// with a tip is clicked: Plot keeps the datum under the pointer in the chart's `value`.
export default function Chart({ make, onPick }) {
  const box = useRef(null)
  const pick = useRef(onPick)
  pick.current = onPick // the latest handler, without drawing the chart again

  useEffect(() => {
    const node = box.current
    let chart = null
    const draw = () => {
      chart?.remove()
      chart = Plot.plot(make(node.clientWidth))
      chart.addEventListener('click', () => {
        if (chart.value && pick.current) pick.current(chart.value)
      })
      node.append(chart)
    }
    const resize = new ResizeObserver(draw)
    resize.observe(node)
    return () => {
      resize.disconnect()
      chart?.remove()
    }
  }, [make])

  return <div className={onPick ? 'chart clickable' : 'chart'} ref={box} />
}
