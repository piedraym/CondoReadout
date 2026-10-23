import * as Plot from '@observablehq/plot'

// Each chart groups the condos of an area by one value: these are its possible values, in stacking
// order, with their colors (the same as the report's badges) and their names for people
const LIGHT = {
  field: 'light',
  values: ['green_current', 'green_not_due', 'yellow', 'red'],
  colors: ['#3f8f3a', '#a3d39c', '#d18f00', '#c0392b'],
  names: {
    green_current: 'Green · current',
    green_not_due: 'Green · not yet due',
    yellow: 'Yellow',
    red: 'Red',
  },
}
// the report's three traffic light colors, for charts that color by the plain light
const TRAFFIC = { domain: ['green', 'yellow', 'red'], range: ['#3f8f3a', '#d18f00', '#c0392b'] }

const SIRS = {
  field: 'sirs',
  values: ['reported', 'not_in_registry', 'no_dbpr'],
  colors: ['#3f8f3a', '#d18f00', '#9aa0aa'],
  names: { reported: 'Reported', not_in_registry: 'Not in registry', no_dbpr: 'No DBPR match' },
}
// Recertification status of the worst tower (the labels of backend/core.py), in the report's words,
// ordered from good to bad; each bar takes the traffic light color of its status
const LABEL = {
  field: 'label',
  values: [
    'Completed',
    'Exempted',
    'Not due yet',
    'Not built yet',
    'Pending: city review',
    'Pending: corrections',
    'Pending: other',
    'Completed: overdue',
    'Closed',
    'no record',
    'Pending: corrections >1y',
    'Pending: stalled',
    'Pending: enforcement',
    'Canceled',
  ],
  names: {
    Completed: 'Current',
    Exempted: 'Exempt',
    'Not due yet': 'Not due yet',
    'Not built yet': 'Not built yet',
    'Pending: city review': 'In city review',
    'Pending: corrections': 'Corrections requested',
    'Pending: other': 'Pending',
    'Completed: overdue': 'Overdue',
    Closed: 'Case closed',
    'no record': 'No record',
    'Pending: corrections >1y': 'Corrections > 1 year',
    'Pending: stalled': 'Stalled',
    'Pending: enforcement': 'Enforcement',
    Canceled: 'Canceled',
  },
}
// When the next recertification is due: overdue, each of the next five years, or later
const THIS_YEAR = new Date().getFullYear()
const NEXT_YEARS = [0, 1, 2, 3, 4].map((n) => String(THIS_YEAR + n))
const DUE = {
  field: 'due',
  values: ['overdue', ...NEXT_YEARS, 'later'],
  names: { overdue: 'Overdue', later: `${THIS_YEAR + 5} or later`, ...Object.fromEntries(NEXT_YEARS.map((y) => [y, y])) },
}

const DARK_TEXT = new Set(['green_not_due', 'yellow', 'not_in_registry', 'no_dbpr']) // light fills: white text would not read

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


// One stacked bar per area: a segment per value, with the count written inside
const stackedBars = (group) => (rows) => (width) => {
  const stacked = { x: 'condos', y: 'jurisdiction', z: group.field, order: group.values }
  return {
    width,
    height: 170,
    marginLeft: 220,
    x: { label: 'Condos', grid: true },
    y: { label: null },
    color: { domain: group.values, range: group.colors, legend: true, tickFormat: (d) => group.names[d] },
    marks: [
      Plot.barX(rows, Plot.stackX({ ...stacked, fill: group.field, inset: 1, rx: 2, tip: true })),
      Plot.textX(
        rows,
        Plot.stackX({
          ...stacked,
          text: 'condos',
          fill: (d) => (DARK_TEXT.has(d[group.field]) ? '#1f2430' : 'white'),
          fontWeight: 'bold',
        }),
      ),
      Plot.ruleX([0]),
    ],
  }
}

const SHORT_AREA = { 'Unincorporated Miami-Dade County': 'Unincorporated Miami-Dade', 'City of Miami Beach': 'Miami Beach' }

// One column per area and a bar per value of `group` (only the values that occur), colored by
// `colorKey(row)` on the `color` scale, with the count after the bar
const barsByArea = (group, colorKey, color) => (rows) => (width) => {
  const shown = group.values.filter((value) => rows.some((row) => row[group.field] === value))
  return {
    width,
    height: 40 + 24 * shown.length,
    marginLeft: 150,
    marginRight: 40,
    fx: { label: null, tickFormat: (d) => SHORT_AREA[d] ?? d },
    x: { label: 'Condos', grid: true, ticks: 3, insetRight: 32 }, // room for the number after the longest bar
    y: { label: null, domain: shown, tickFormat: (d) => group.names[d] },
    color,
    marks: [
      Plot.barX(rows, { fx: 'jurisdiction', y: group.field, x: 'condos', fill: colorKey, rx: 2, insetTop: 2, insetBottom: 2, tip: true }),
      Plot.text(rows, { fx: 'jurisdiction', y: group.field, x: 'condos', text: 'condos', textAnchor: 'start', dx: 4, fill: '#1f2430' }),
      Plot.ruleX([0]),
    ],
  }
}

// statuses colored by their traffic light; due dates in one blue, overdue in red
const labelBars = barsByArea(LABEL, 'light', TRAFFIC)
const dueBars = barsByArea(DUE, (d) => (d.due === 'overdue' ? 'overdue' : 'due'), {
  domain: ['overdue', 'due'],
  range: ['#c0392b', '#1f4e79'],
})

const columnsFor = (group, label) => [
  ['jurisdiction', 'Area'],
  [group.field, label],
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
    group: LIGHT,
    columns: columnsFor(LIGHT, 'Light'),
    make: stackedBars(LIGHT),
    list: true, // clicking a segment lists its condos (stats.LISTS in the backend)

  },
  {
    name: 'recert_lights_by_jurisdiction',
    section: 'recertification',
    title: 'Traffic light of all condos',
    note:
      'Worst recertification status of each building, by area. Every condo except those of 1 or 2 units ' +
      '(houses and duplexes are exempt from recertification).',
    group: LIGHT,
    columns: columnsFor(LIGHT, 'Light'),
    make: stackedBars(LIGHT),
    list: true, // clicking a segment lists its condos (stats.LISTS in the backend)
  },

  {
    name: 'milestone_lights_by_jurisdiction',
    section: 'milestone',
    title: 'Traffic light of condos under the milestone law',
    note:
      'Worst recertification status of each building, by area: in Miami-Dade the recertification counts ' +
      'as the state milestone inspection. Buildings of 3+ floors (floors estimated; when unknown, condos of 11+ units).',
    group: LIGHT,
    columns: columnsFor(LIGHT, 'Light'),
    make: stackedBars(LIGHT),
    list: true, // clicking a segment lists its condos (stats.LISTS in the backend)
  },
  {
    name: 'milestone_sirs_by_jurisdiction',
    section: 'milestone',
    title: 'Structural Integrity Reserve Study (SIRS)',
    note:
      'Whether the association filed a SIRS with the state (DBPR), for the buildings under the milestone law. ' +
      '"Not in registry" means we found the association but no SIRS filed: it does not prove there is none. ' +
      '"No DBPR match" means we could not identify the association in the state registry.',
    group: SIRS,
    columns: columnsFor(SIRS, 'SIRS'),
    make: stackedBars(SIRS),
    list: true, // clicking a segment lists its condos (stats.LISTS in the backend)
  },
  {
    name: 'recert_labels_by_jurisdiction',
    section: 'recertification',
    title: 'Why condos are green, yellow or red',
    note:
      'Recertification status of the worst tower of each condo, by area, in the words of the report. ' +
      'Every condo except those of 1 or 2 units. "No record" means no case was found in the public records.',
    group: LABEL,
    columns: [
      ['jurisdiction', 'Area'],
      ['label', 'Status'],
      ['condos', 'Condos'],
    ],
    make: labelBars,
    list: true, // clicking a bar lists its condos (stats.LISTS in the backend)
  },
  {
    name: 'recert_due_by_jurisdiction',
    section: 'recertification',
    title: 'When the next recertification is due',
    note:
      'Year the first tower of each condo is due: the last recertification + 10 years, the year of an open ' +
      'case, or, with no record, the County schedule (Sec. 8-11(f) as amended in 2022). Estimated; every ' +
      'condo except those of 1 or 2 units, when a date can be computed.',
    group: DUE,
    columns: [
      ['jurisdiction', 'Area'],
      ['due', 'Due'],
      ['condos', 'Condos'],
    ],
    make: dueBars,
    list: true, // clicking a bar lists its condos (stats.LISTS in the backend)
  },
]
