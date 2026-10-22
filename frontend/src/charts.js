import * as Plot from '@observablehq/plot'

// The same traffic light colors as the report (backend/templates/report.html)
export const LIGHTS = ['green', 'yellow', 'red']
const LIGHT_COLORS = ['#3f8f3a', '#d18f00', '#c0392b']
const LIGHT_NAMES = { green: 'Green', yellow: 'Yellow', red: 'Red' }

// One entry per query of backend/stats.py: how to draw its rows, and how to show them as a table
export const CHARTS = [
  {
    name: 'lights_by_jurisdiction',
    title: 'Traffic light of condos with 11+ units',
    note: 'Worst recertification status of each building, by area.',
    columns: [
      ['jurisdiction', 'Area'],
      ['light', 'Light'],
      ['condos', 'Condos'],
    ],
    make: (rows) => (width) => {
      const stacked = { x: 'condos', y: 'jurisdiction', z: 'light', order: LIGHTS }
      return {
        width,
        height: 170,
        marginLeft: 220,
        x: { label: 'Condos', grid: true },
        y: { label: null },
        color: { domain: LIGHTS, range: LIGHT_COLORS, legend: true, tickFormat: (d) => LIGHT_NAMES[d] },
        marks: [
          Plot.barX(rows, Plot.stackX({ ...stacked, fill: 'light', inset: 1, rx: 2, tip: true })),
          Plot.textX(
            rows,
            Plot.stackX({
              ...stacked,
              text: 'condos',
              fill: (d) => (d.light === 'yellow' ? '#1f2430' : 'white'), // dark text: yellow is too light
              fontWeight: 'bold',
            }),
          ),
          Plot.ruleX([0]),
        ],
      }
    },
  },
]
