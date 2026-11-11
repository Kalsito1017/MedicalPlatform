// ---------------------------------------------------------------------------
// graphify.js — local interactive SVG chart primitives.
//
// The frontend is buildless and CSP-locked: this module is dependency-free,
// uses same-origin DOM APIs only, and never writes element styles. Geometry is
// SVG attributes, interactive state is classes/attributes/text content, and
// every chart ships a visually hidden data table for screen readers.
// ---------------------------------------------------------------------------

import * as ui from './ui.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, props = {}, ...children) {
  const node = typeof document.createElementNS === 'function'
    ? document.createElementNS(SVG_NS, tag)
    : document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null) continue;
    if (key === 'text') node.textContent = value;
    else if (key === 'class') node.setAttribute('class', value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      node.setAttribute(key, value === true ? '' : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function asNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function valueText(value) {
  return value == null ? 'Not recorded' : String(value);
}

function readout(text = 'Hover or focus a chart mark for exact values.') {
  return ui.el('p', {
    class: 'chart-readout',
    role: 'status',
    'aria-live': 'polite',
    text,
  });
}

function hiddenTable(caption, headers, rows) {
  return ui.el('table', { class: 'visually-hidden' },
    ui.el('caption', { text: caption }),
    ui.el('thead', {},
      ui.el('tr', {}, headers.map((header) => ui.el('th', { scope: 'col', text: String(header) }))),
    ),
    ui.el('tbody', {}, rows.map((cells) =>
      ui.el('tr', {}, cells.map((cell) => ui.el('td', { text: String(cell) }))),
    )),
  );
}

function attachMarkInteractivity(mark, label, readoutNode, marks, index) {
  const show = () => {
    readoutNode.textContent = label;
    mark.classList.add('is-active');
  };
  const hide = () => {
    mark.classList.remove('is-active');
    readoutNode.textContent = 'Hover or focus a chart mark for exact values.';
  };
  mark.setAttribute('tabindex', '0');
  mark.setAttribute('role', 'button');
  mark.setAttribute('aria-label', label);
  mark.addEventListener('mouseenter', show);
  mark.addEventListener('mouseleave', hide);
  mark.addEventListener('focus', show);
  mark.addEventListener('blur', hide);
  mark.addEventListener('click', show);
  mark.addEventListener('keydown', (event) => {
    const key = event && event.key;
    if (key === 'ArrowRight' || key === 'ArrowDown') {
      event.preventDefault();
      marks[(index + 1) % marks.length].focus();
    } else if (key === 'ArrowLeft' || key === 'ArrowUp') {
      event.preventDefault();
      marks[(index - 1 + marks.length) % marks.length].focus();
    } else if (key === 'Home') {
      event.preventDefault();
      marks[0].focus();
    } else if (key === 'End') {
      event.preventDefault();
      marks[marks.length - 1].focus();
    }
  });
}

function legendToggle(series, seriesGroups, onToggle) {
  return ui.el('li', { class: 'chart-legend__item' },
    ui.el('button', {
      class: 'chart-toggle',
      type: 'button',
      'aria-pressed': 'true',
      onClick: () => {
        const group = seriesGroups[series.key];
        if (!group) return;
        const hidden = group.hasAttribute('hidden');
        if (hidden) group.removeAttribute('hidden');
        else group.setAttribute('hidden', '');
        onToggle(series.key, hidden);
      },
    },
      ui.el('span', {
        class: `chart-legend__swatch ${series.swatchClass || ''}`.trim(),
        'aria-hidden': 'true',
      }),
      ui.el('span', { text: series.label }),
    ),
  );
}

export function renderGroupedBarChart(container, {
  categories,
  series,
  ariaLabel,
  tableCaption,
  valueFormatter = valueText,
}) {
  ui.clear(container);
  if (!Array.isArray(categories) || categories.length === 0 || !Array.isArray(series) || series.length === 0) {
    container.append(ui.el('p', { class: 'text-muted', text: 'No chart data yet.' }));
    return;
  }

  const width = 620;
  const height = 230;
  const baseline = height - 34;
  const groupWidth = width / categories.length;
  const barWidth = Math.max(3, Math.floor((groupWidth - 8) / series.length));
  const maxValue = Math.max(1, ...categories.flatMap((_, index) => series.map((entry) => asNumber(entry.values[index]))));
  const readoutNode = readout();
  const seriesGroups = {};
  const marks = [];

  const axis = svgEl('line', {
    class: 'chart-axis',
    x1: '0',
    y1: String(baseline),
    x2: String(width),
    y2: String(baseline),
    'stroke-width': '1',
  });

  const svg = svgEl('svg', {
    class: 'chart graphify-chart',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': ariaLabel,
    focusable: 'false',
  }, axis);

  series.forEach((entry) => {
    const group = svgEl('g', { class: `graphify-series graphify-series--${entry.key}` });
    seriesGroups[entry.key] = group;
    categories.forEach((category, index) => {
      const value = asNumber(entry.values[index]);
      const barHeight = value > 0 ? Math.max(2, Math.round((value / maxValue) * (baseline - 12))) : 0;
      const mark = svgEl('rect', {
        class: `chart-bar ${entry.className || ''}`.trim(),
        x: String(Math.round(index * groupWidth + 4 + series.indexOf(entry) * barWidth)),
        y: String(baseline - barHeight),
        width: String(barWidth),
        height: String(barHeight),
        rx: '2',
      });
      attachMarkInteractivity(
        mark,
        `${entry.label}, ${category}: ${valueFormatter(entry.values[index])}`,
        readoutNode,
        marks,
        marks.length,
      );
      marks.push(mark);
      group.append(mark);
    });
    svg.append(group);
  });

  categories.forEach((category, index) => {
    if (index % 2 === 0) {
      svg.append(svgEl('text', {
        class: 'chart-day',
        x: String(Math.round(index * groupWidth + groupWidth / 2)),
        y: String(baseline + 18),
        'text-anchor': 'middle',
        text: String(category).slice(5),
      }));
    }
  });

  container.append(
    ui.el('ul', { class: 'chart-legend' }, series.map((entry) =>
      legendToggle(entry, seriesGroups, (key, shown) => {
        readoutNode.textContent = shown
          ? `${series.find((item) => item.key === key)?.label || key} shown.`
          : `${series.find((item) => item.key === key)?.label || key} hidden.`;
      }),
    )),
    svg,
    readoutNode,
    hiddenTable(
      tableCaption,
      ['Category', ...series.map((entry) => entry.label)],
      categories.map((category, index) => [category, ...series.map((entry) => entry.values[index])]),
    ),
  );
}

export function renderLineChart(container, {
  series,
  ariaLabel,
  tableCaption,
  valueFormatter = valueText,
  dateFormatter = (value) => valueText(value),
}) {
  ui.clear(container);
  const validSeries = (series || []).filter((entry) => Array.isArray(entry.points) && entry.points.length > 0);
  if (validSeries.length === 0) {
    container.append(ui.el('p', { class: 'text-muted', text: 'No chart data yet.' }));
    return;
  }

  const xValues = [...new Set(validSeries.flatMap((entry) => entry.points.map((point) => String(point.x))))].sort();
  const width = 620;
  const height = 270;
  const left = 48;
  const right = 18;
  const top = 16;
  const bottom = 42;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const values = validSeries.flatMap((entry) => entry.points.map((point) => asNumber(point.y)));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || 1;
  const xFor = (x) => left + (xValues.indexOf(String(x)) / Math.max(1, xValues.length - 1)) * plotWidth;
  const yFor = (y) => top + (1 - (asNumber(y) - minValue) / span) * plotHeight;
  const readoutNode = readout();
  const seriesGroups = {};
  const marks = [];

  const svg = svgEl('svg', {
    class: 'chart graphify-chart graphify-line',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': ariaLabel,
    focusable: 'false',
  });

  for (const tick of [minValue, minValue + span / 2, maxValue]) {
    svg.append(svgEl('line', {
      class: 'chart-axis',
      x1: String(left),
      y1: String(yFor(tick)),
      x2: String(width - right),
      y2: String(yFor(tick)),
      'stroke-width': '1',
    }));
    svg.append(svgEl('text', {
      class: 'chart-day',
      x: String(left - 8),
      y: String(yFor(tick) + 4),
      'text-anchor': 'end',
      text: valueFormatter(tick),
    }));
  }

  validSeries.forEach((entry) => {
    const group = svgEl('g', { class: `graphify-series graphify-series--${entry.key}` });
    seriesGroups[entry.key] = group;
    const ordered = [...entry.points].sort((a, b) => String(a.x).localeCompare(String(b.x)));
    group.append(svgEl('polyline', {
      class: `graphify-line__path ${entry.className || ''}`.trim(),
      points: ordered.map((point) => `${xFor(point.x)},${yFor(point.y)}`).join(' '),
      fill: 'none',
      'stroke-width': '2',
    }));
    ordered.forEach((point) => {
      const mark = svgEl('circle', {
        class: `graphify-line__point ${entry.className || ''}`.trim(),
        cx: String(xFor(point.x)),
        cy: String(yFor(point.y)),
        r: '4',
      });
      attachMarkInteractivity(
        mark,
        `${entry.label}, ${dateFormatter(point.x)}: ${valueFormatter(point.y)}${point.unit ? ` ${point.unit}` : ''}${point.flag ? ` (${point.flag})` : ''}`,
        readoutNode,
        marks,
        marks.length,
      );
      marks.push(mark);
      group.append(mark);
    });
    svg.append(group);
  });

  const labelIndexes = [0, Math.floor((xValues.length - 1) / 2), xValues.length - 1];
  for (const index of labelIndexes) {
    if (index < 0 || index >= xValues.length) continue;
    svg.append(svgEl('text', {
      class: 'chart-day',
      x: String(xFor(xValues[index])),
      y: String(height - 12),
      'text-anchor': index === 0 ? 'start' : (index === xValues.length - 1 ? 'end' : 'middle'),
      text: dateFormatter(xValues[index]),
    }));
  }

  container.append(
    ui.el('ul', { class: 'chart-legend' }, validSeries.map((entry) =>
      legendToggle(entry, seriesGroups, (key, shown) => {
        readoutNode.textContent = shown
          ? `${validSeries.find((item) => item.key === key)?.label || key} shown.`
          : `${validSeries.find((item) => item.key === key)?.label || key} hidden.`;
      }),
    )),
    svg,
    readoutNode,
    hiddenTable(
      tableCaption,
      ['Collected', ...validSeries.map((entry) => entry.label)],
      xValues.map((x) => [
        dateFormatter(x),
        ...validSeries.map((entry) => {
          const point = entry.points.find((item) => String(item.x) === x);
          return point ? valueFormatter(point.y) : '';
        }),
      ]),
    ),
  );
}

function formatLabValue(value) {
  if (value == null) return 'Not recorded';
  const text = String(value).trim();
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) return text;
  return text.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
}

function formatAxisTick(value) {
  const abs = Math.abs(value);
  if (abs >= 100) return String(Math.round(value));
  if (abs >= 10) return String(Math.round(value * 10) / 10);
  if (abs >= 1) return String(Math.round(value * 100) / 100);
  return String(Math.round(value * 1000) / 1000);
}

function parseDateValue(value) {
  const time = Date.parse(String(value));
  return Number.isFinite(time) ? time : null;
}

function compactDate(value, includeDay) {
  const time = parseDateValue(value);
  if (time == null) return valueText(value);
  const options = includeDay
    ? { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }
    : { month: 'short', year: 'numeric', timeZone: 'UTC' };
  try {
    return new Intl.DateTimeFormat('en-GB', options).format(new Date(time));
  } catch {
    return String(value);
  }
}

function targetLabel(series) {
  const low = series.refLow != null ? asNumber(series.refLow) : null;
  const high = series.refHigh != null ? asNumber(series.refHigh) : null;
  const unit = series.unit ? ` ${series.unit}` : '';
  if (low != null && high != null && low > 0) {
    return `Target: ${formatLabValue(series.refLow)}\u2013${formatLabValue(series.refHigh)}${unit}`;
  }
  if (high != null) return `Target: < ${formatLabValue(series.refHigh)}${unit}`;
  if (low != null) return `Target: > ${formatLabValue(series.refLow)}${unit}`;
  return 'Reference range not recorded';
}

function labYDomain(series) {
  const values = series.points.map((point) => asNumber(point.y));
  const bounds = [...values];
  if (series.refLow != null) bounds.push(asNumber(series.refLow));
  if (series.refHigh != null) bounds.push(asNumber(series.refHigh));
  let min = Math.min(...bounds);
  let max = Math.max(...bounds);
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    min = 0;
    max = 1;
  }
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  } else {
    const pad = (max - min) * 0.12;
    min -= pad;
    max += pad;
  }
  if (bounds.every((value) => value >= 0) && min < 0) min = 0;
  return { min, max };
}

function renderLabChart(series) {
  const width = 460;
  const height = 280;
  const left = 56;
  const right = 18;
  const top = 44;
  const bottom = 42;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const domain = labYDomain(series);
  const span = domain.max - domain.min || 1;
  const yFor = (value) => top + (1 - (asNumber(value) - domain.min) / span) * plotHeight;

  const ordered = [...series.points].sort((a, b) => String(a.x).localeCompare(String(b.x)));
  const parsedTimes = ordered.map((point) => parseDateValue(point.x));
  const hasTime = parsedTimes.every((time) => time != null);
  const timeMin = hasTime ? Math.min(...parsedTimes) : 0;
  const timeMax = hasTime ? Math.max(...parsedTimes) : Math.max(1, ordered.length - 1);
  const xFor = (point, index) => {
    if (!hasTime) {
      if (ordered.length === 1) return left + plotWidth / 2;
      return left + (index / (ordered.length - 1)) * plotWidth;
    }
    if (timeMax === timeMin) return left + plotWidth / 2;
    return left + ((parsedTimes[index] - timeMin) / (timeMax - timeMin)) * plotWidth;
  };

  const readoutNode = readout();
  const marks = [];
  const svg = svgEl('svg', {
    class: 'chart graphify-chart lab-chart__plot',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': `${series.testName} (${series.testCode}) trend. ${targetLabel(series)}.`,
    focusable: 'false',
  });

  // Reference band (green) from the recorded bounds, clipped to the plot.
  const bandLow = series.refLow != null ? asNumber(series.refLow) : domain.min;
  const bandHigh = series.refHigh != null ? asNumber(series.refHigh) : domain.max;
  if (series.refLow != null || series.refHigh != null) {
    const bandTop = Math.max(top, yFor(Math.max(bandLow, bandHigh)));
    const bandBottom = Math.min(top + plotHeight, yFor(Math.min(bandLow, bandHigh)));
    if (bandBottom > bandTop) {
      svg.append(svgEl('rect', {
        class: 'chart-range-band',
        x: String(left),
        y: String(bandTop),
        width: String(plotWidth),
        height: String(bandBottom - bandTop),
      }));
    }
  }

  for (let index = 0; index <= 4; index += 1) {
    const value = domain.min + (span * index) / 4;
    const y = yFor(value);
    svg.append(svgEl('line', {
      class: 'chart-grid',
      x1: String(left),
      y1: String(y),
      x2: String(width - right),
      y2: String(y),
    }));
    svg.append(svgEl('text', {
      class: 'chart-tick',
      x: String(left - 8),
      y: String(y + 4),
      'text-anchor': 'end',
      text: formatAxisTick(value),
    }));
  }

  const target = targetLabel(series);
  svg.append(svgEl('text', {
    class: series.refLow != null || series.refHigh != null ? 'lab-chart__target' : 'chart-tick',
    x: String(left),
    y: '22',
    text: target,
  }));

  if (series.unit) {
    svg.append(svgEl('text', {
      class: 'chart-tick',
      x: '14',
      y: String(top + plotHeight / 2),
      'text-anchor': 'middle',
      transform: `rotate(-90 14 ${top + plotHeight / 2})`,
      text: series.unit,
    }));
  }

  svg.append(svgEl('line', {
    class: 'chart-axis',
    x1: String(left),
    y1: String(top + plotHeight),
    x2: String(width - right),
    y2: String(top + plotHeight),
  }));

  if (ordered.length > 1) {
    svg.append(svgEl('polyline', {
      class: 'lab-chart__line',
      points: ordered.map((point, index) => `${xFor(point, index)},${yFor(point.y)}`).join(' '),
      fill: 'none',
    }));
  }

  const includeDay = hasTime && (timeMax - timeMin) < 1000 * 60 * 60 * 24 * 120;
  const tickIndexes = ordered.length === 1
    ? [0]
    : [...new Set([0, Math.round((ordered.length - 1) / 3), Math.round(((ordered.length - 1) * 2) / 3), ordered.length - 1])];
  const tickLimit = 4;
  tickIndexes.slice(0, tickLimit).forEach((index) => {
    const point = ordered[index];
    svg.append(svgEl('text', {
      class: 'chart-tick',
      x: String(xFor(point, index)),
      y: String(height - 14),
      'text-anchor': ordered.length === 1
        ? 'middle'
        : (index === 0 ? 'start' : (index === ordered.length - 1 ? 'end' : 'middle')),
      text: compactDate(point.x, includeDay),
    }));
  });

  const labelAll = ordered.length <= 8;
  ordered.forEach((point, index) => {
    const x = xFor(point, index);
    const y = yFor(point.y);
    const flag = point.flag || 'unclassified';
    const mark = svgEl('circle', {
      class: `lab-chart__point lab-chart__point--${flag}`,
      cx: String(x),
      cy: String(y),
      r: '5.5',
    });
    const label = `${series.testName}, ${compactDate(point.x, true)}: ${formatLabValue(point.y)}${series.unit ? ` ${series.unit}` : ''}${point.flag ? ` (${point.flag})` : ''}`;
    attachMarkInteractivity(mark, label, readoutNode, marks, marks.length);
    marks.push(mark);
    svg.append(mark);

    const showValue = labelAll || index === ordered.length - 1 || flag === 'high' || flag === 'low';
    if (showValue) {
      const nearTop = y - 11 < top + 8;
      const labelY = nearTop ? y + 20 : y - 11;
      svg.append(svgEl('text', {
        class: 'lab-chart__value',
        x: String(Math.min(width - right - 2, Math.max(left + 2, x))),
        y: String(labelY),
        'text-anchor': x < left + 24 ? 'start' : (x > width - right - 24 ? 'end' : 'middle'),
        text: formatLabValue(point.y),
      }));
    }
  });

  return ui.el('figure', { class: 'lab-chart' },
    ui.el('figcaption', { class: 'lab-chart__head' },
      ui.el('span', { class: 'lab-chart__title', text: series.testName }),
      ui.el('span', { class: 'lab-chart__code', text: series.testCode }),
      series.abnormalCount > 0
        ? ui.el('span', { class: 'badge badge--warn', text: `${series.abnormalCount} abnormal` })
        : null,
    ),
    svg,
    readoutNode,
    hiddenTable(
      `${series.testName} (${series.testCode}) trend values`,
      ['Collected', 'Value', 'Flag'],
      ordered.map((point) => [compactDate(point.x, true), formatLabValue(point.y), point.flag || 'unclassified']),
    ),
  );
}

export function renderLabTrendPanels(container, { series }) {
  ui.clear(container);
  const validSeries = (series || []).filter((entry) => entry && Array.isArray(entry.points) && entry.points.length > 0);
  if (validSeries.length === 0) {
    container.append(ui.el('p', { class: 'text-muted', text: 'No numeric lab trends recorded.' }));
    return;
  }
  const grid = ui.el('div', { class: 'lab-chart-grid' }, validSeries.map((entry) => renderLabChart(entry)));
  container.append(grid);
}

export function renderDonutChart(container, {
  label,
  value,
  total,
  percent,
  centerLabel,
  centerCaption,
  ariaLabel,
  tableCaption,
  rows,
}) {
  ui.clear(container);
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const arc = total > 0 ? (value / total) * circumference : 0;
  const donut = svgEl('svg', {
    class: 'donut graphify-chart',
    viewBox: '0 0 140 140',
    role: 'img',
    'aria-label': ariaLabel,
    focusable: 'false',
  },
    svgEl('circle', {
      class: 'donut__track', cx: '70', cy: '70', r: String(radius), fill: 'none', 'stroke-width': '18',
    }),
    svgEl('circle', {
      class: 'donut__value',
      cx: '70',
      cy: '70',
      r: String(radius),
      fill: 'none',
      'stroke-width': '18',
      'stroke-dasharray': `${arc} ${circumference}`,
      transform: 'rotate(-90 70 70)',
    }),
    svgEl('text', { class: 'donut__label', x: '70', y: '68', 'text-anchor': 'middle', text: centerLabel || `${percent}%` }),
    svgEl('text', { class: 'donut__caption', x: '70', y: '90', 'text-anchor': 'middle', text: centerCaption || label }),
  );
  container.append(donut, hiddenTable(tableCaption, ['Measure', 'Value'], rows || [[label, value], ['Total', total], ['Percent', percent]]));
}

export function renderHorizontalBars(container, {
  rows,
  ariaLabel,
  tableCaption,
  valueFormatter = valueText,
}) {
  ui.clear(container);
  const validRows = (rows || []).filter((row) => row && row.label != null);
  if (validRows.length === 0) {
    container.append(ui.el('p', { class: 'text-muted', text: 'No chart data yet.' }));
    return;
  }
  const max = Math.max(1, ...validRows.map((row) => asNumber(row.value)));
  const readoutNode = readout();
  const marks = [];
  const list = ui.el('ul', { class: 'bar-list' }, validRows.map((row, index) => {
    const width = Math.max(2, Math.round((asNumber(row.value) / max) * 100));
    const label = `${row.label}: ${valueFormatter(row.value)}`;
    const item = ui.el('li', { class: 'bar-row graphify-bar-row', tabindex: '0', role: 'button', 'aria-label': label },
      ui.el('p', { class: 'bar-row__label' }, row.label && row.label.nodeType ? row.label : String(row.label)),
      ui.el('p', { class: 'bar-row__value tabular-nums', text: valueFormatter(row.value) }),
      svgEl('svg', {
        class: 'bar-track',
        viewBox: '0 0 100 8',
        preserveAspectRatio: 'none',
        role: 'presentation',
        focusable: 'false',
      },
        svgEl('rect', { class: 'bar-track__bg', x: '0', y: '0', width: '100', height: '8', rx: '4' }),
        svgEl('rect', {
          class: `bar-track__fill ${row.className || ''}`.trim(),
          x: '0',
          y: '0',
          width: String(width),
          height: '8',
          rx: '4',
        }),
      ),
    );
    attachMarkInteractivity(item, label, readoutNode, marks, index);
    marks.push(item);
    return item;
  }));

  container.append(
    ui.el('p', { class: 'visually-hidden', text: ariaLabel }),
    list,
    readoutNode,
    hiddenTable(tableCaption, ['Label', 'Value'], validRows.map((row) => [row.label && row.label.nodeType ? row.label.textContent : row.label, row.value])),
  );
}
