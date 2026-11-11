/* Graphify unit checks: chart rendering, interactivity hooks, accessibility
 * twins, and the no-style/no-HTML contract. Run with:
 *   node frontend/tests/graphify.test.mjs
 */
import { installDom, textOf } from './domshim.mjs';

const graphify = await import('../assets/js/graphify.js');

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (error) {
    results.push({ name, ok: false, detail: error && error.message ? error.message : String(error) });
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message);
}

await check('grouped bars render marks, readout, and hidden table', () => {
  installDom();
  const container = document.createElement('div');
  document.body.append(container);
  graphify.renderGroupedBarChart(container, {
    categories: ['2026-11-01', '2026-11-02'],
    series: [
      { key: 'logins', label: 'Logins', className: 'chart-bar--logins', swatchClass: 'chart-legend__swatch--logins', values: [2, 5] },
    ],
    ariaLabel: 'Audit activity summary.',
    tableCaption: 'Audit table',
    valueFormatter: (value) => String(value),
  });
  const text = textOf(container);
  const marks = container.querySelectorAll('rect');
  assert(marks.some((mark) => mark.getAttribute('aria-label') === 'Logins, 2026-11-01: 2'), 'interactive label missing');
  assert(text.includes('Audit table'), 'hidden data table missing');
  assert(marks.length === 2, 'expected one rect per category');
  assert(container.querySelectorAll('.chart-readout').length === 1, 'readout missing');
});

await check('line charts render points and keyboard-ready marks', () => {
  installDom();
  const container = document.createElement('div');
  document.body.append(container);
  graphify.renderLineChart(container, {
    series: [{
      key: 'HGB',
      label: 'Haemoglobin (HGB)',
      points: [
        { x: '2026-08-01', y: '11.8', unit: 'g/dL', flag: 'low' },
        { x: '2026-09-14', y: '13.5', unit: 'g/dL', flag: 'normal' },
      ],
    }],
    ariaLabel: 'Haemoglobin trend.',
    tableCaption: 'Trend table',
    valueFormatter: (value) => String(value),
    dateFormatter: (value) => String(value),
  });
  const text = textOf(container);
  const marks = container.querySelectorAll('.graphify-line__point');
  assert(marks.some((mark) => mark.getAttribute('aria-label') === 'Haemoglobin (HGB), 2026-08-01: 11.8 g/dL (low)'), 'point label missing');
  assert(text.includes('Trend table'), 'hidden data table missing');
  assert(marks.length === 2, 'expected two focusable points');
  assert(marks[0].getAttribute('tabindex') === '0', 'points must be keyboard reachable');
});

await check('horizontal bars expose exact values and a hidden table', () => {
  installDom();
  const container = document.createElement('div');
  document.body.append(container);
  graphify.renderHorizontalBars(container, {
    rows: [
      { label: 'Potassium (K)', value: 15 },
      { label: 'Glucose (GLU)', value: 19 },
    ],
    ariaLabel: 'Abnormal tests.',
    tableCaption: 'Abnormal table',
    valueFormatter: (value) => String(value),
  });
  const text = textOf(container);
  const rows = container.querySelectorAll('.graphify-bar-row');
  assert(rows.some((row) => row.getAttribute('aria-label') === 'Potassium (K): 15'), 'bar label missing');
  assert(text.includes('Abnormal table'), 'hidden data table missing');
  assert(rows.length === 2, 'rows missing');
});

await check('lab trend panels render one chart per test with ranges and labels', () => {
  installDom();
  const container = document.createElement('div');
  document.body.append(container);
  graphify.renderLabTrendPanels(container, {
    series: [
      {
        testName: 'Fasting glucose',
        testCode: 'GLU',
        unit: 'mg/dL',
        refLow: '70.0',
        refHigh: '99.0',
        abnormalCount: 1,
        points: [
          { x: '2026-07-01', y: '104.0', flag: 'high' },
          { x: '2026-09-14', y: '92.0', flag: 'normal' },
        ],
      },
      {
        testName: 'Haemoglobin',
        testCode: 'HGB',
        unit: 'g/dL',
        refLow: '12.0',
        refHigh: '17.0',
        abnormalCount: 0,
        points: [
          { x: '2026-08-01', y: '13.5', flag: 'normal' },
        ],
      },
    ],
  });
  const text = textOf(container);
  assert(container.querySelectorAll('.lab-chart').length === 2, 'expected one chart per test');
  assert(container.querySelectorAll('.chart-range-band').length === 2, 'expected a green reference band per chart');
  assert(container.querySelectorAll('.lab-chart__value').length >= 3, 'expected value labels on data points');
  assert(text.includes('Target: 70\u201399 mg/dL'), 'glucose target missing');
  assert(text.includes('Target: 12\u201317 g/dL'), 'haemoglobin target missing');
  assert(text.includes('Fasting glucose (GLU) trend values'), 'glucose hidden table missing');
  assert(text.includes('Haemoglobin (HGB) trend values'), 'haemoglobin hidden table missing');
  assert(container.querySelectorAll('.lab-chart__point--high').length === 1, 'high point styling missing');
  assert(container.querySelectorAll('.lab-chart__point--normal').length === 2, 'normal point styling missing');
});

for (const result of results) {
  process.stdout.write(`${result.ok ? 'ok' : 'FAIL'} ${result.name}${result.detail ? ` — ${result.detail}` : ''}\n`);
}
const failed = results.filter((result) => !result.ok).length;
process.stdout.write(`graphify: ${results.length - failed}/${results.length} passed\n`);
if (failed) process.exit(1);
