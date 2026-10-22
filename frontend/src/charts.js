import * as Plot from '@observablehq/plot'

// The same traffic light colors as the report (backend/templates/report.html)
export const LIGHTS = ['green', 'yellow', 'red']
const LIGHT_COLORS = ['#3f8f3a', '#d18f00', '#c0392b']
const LIGHT_NAMES = { green: 'Green', yellow: 'Yellow', red: 'Red' }

// The three parts of the Dashboard, one per norm, in the order they are shown
export const SECTIONS = [
  {
    id: 'recertification',
    title: 'Recertification',
    note:
      'Miami-Dade County Code Sec. 8-11: almost every building (not houses or duplexes), at 30 years and ' +
      'every 10 years after. In Miami-Dade it also counts as the state milestone inspection.',
  },
  {
    id: 'milestone',
    title: 'Milestone and SIRS',
    note:
      'Florida Statutes 553.899 and the condominium law: condo buildings of 3 or more floors, which also ' +
      'need a Structural Integrity Reserve Study (SIRS).',
  },
  {
    id: 'financing',
    title: 'Financing',
    note: 'Fannie Mae and Freddie Mac rules for conventional mortgages in a condo project.',
  },
]


// Traffic light by area as one stacked bar per area: green, yellow, red, with the count written inside
const lightBars = (rows) => (width) => {
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
}

const LIGHT_COLUMNS = [
  ['jurisdiction', 'Area'],
  ['light', 'Light'],
  ['condos', 'Condos'],
]


// One entry per query of backend/stats.py: its section, how to draw its rows, and its table columns
export const CHARTS = [

  {
    name: 'lights_by_jurisdiction',
    section: 'financing',
    title: 'Traffic light of condos with 11+ units.',
    note:
      'Worst recertification status of each building, by area. Only condos with 11+ units: the cut at which ' +
      'Fannie Mae and Freddie Mac require a lender Full Review of the condo project (more than 10 units).',
    columns: LIGHT_COLUMNS,
    make: lightBars,
    list: true, // clicking a segment lists its condos (stats.LISTS in the backend)

  },
  {
    name: 'recert_lights_by_jurisdiction',
    section: 'recertification',
    title: 'Traffic light of all condos',
    note:
      'Worst recertification status of each building, by area. Every condo except those of 1 or 2 units ' +
      '(houses and duplexes are exempt from recertification).',
    columns: LIGHT_COLUMNS,
    make: lightBars,
    list: true, // clicking a segment lists its condos (stats.LISTS in the backend)
  },

  {
    name: 'milestone_lights_by_jurisdiction',
    section: 'milestone',
    title: 'Traffic light of condos under the milestone law',
    note:
      'Worst recertification status of each building, by area: in Miami-Dade the recertification counts ' +
      'as the state milestone inspection. Buildings of 3+ floors (floors estimated; when unknown, condos of 11+ units).',
    columns: LIGHT_COLUMNS,
    make: lightBars,
    list: true, // clicking a segment lists its condos (stats.LISTS in the backend)
  },


]
