import * as Plot from '@observablehq/plot'
import { useEffect, useRef } from 'react'

// One Observable Plot chart. `make(width)` returns the Plot options; it is drawn again when the
// width of its box changes, so the chart always fits the page. `onPick(index)` runs when a bar
// (or the number on it) is clicked, with the index of its row in the chart's data.
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
      // the clicked bar, or the number written on it: Plot keeps the index of its data row in __data__
      chart.addEventListener('click', (event) => {
        const mark = event.target.closest("g[aria-label='bar'] rect, g[aria-label='text'] text")
        if (mark && Number.isInteger(mark.__data__) && pick.current) pick.current(mark.__data__)
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
