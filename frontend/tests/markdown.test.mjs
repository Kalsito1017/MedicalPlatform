#!/usr/bin/env node
/* ---------------------------------------------------------------------------
 * frontend/tests/markdown.test.mjs — unit tests for the safe Markdown-subset
 * renderer (frontend/assets/js/markdown.js).
 *
 * Run with:  node tests/markdown.test.mjs   (from the frontend/ directory)
 *
 * The renderer turns untrusted LLM answer text into DOM nodes via
 * document.createElement / createTextNode only (see the module header of
 * markdown.js for the exact supported subset). These tests cover the subset
 * (headings, bold/italic/code, lists incl. one nesting level, pipe tables
 * with numeric-column alignment, the records-consulted details tail,
 * hr/blockquote, plain-text fallback) and an XSS suite proving that script
 * tags, event-handler attributes, javascript: links, attribute-carrying
 * <details>, and HTML entities all end up as inert text or safely-built
 * attribute-free nodes. The DOM is the same minimal shim render-smoke.mjs
 * uses (./domshim.mjs) - this is structure-level verification, not a real
 * browser.
 * ------------------------------------------------------------------------- */

import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { installDom, textOf } from './domshim.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const markdownSource = await readFile(path.join(here, '..', 'assets', 'js', 'markdown.js'), 'utf8');
const markdown = await import(path.join(here, '..', 'assets', 'js', 'markdown.js'));

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

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`);
  }
}

function assertEqualList(actual, expected, message) {
  const same = actual.length === expected.length && actual.every((value, i) => value === expected[i]);
  if (!same) {
    throw new Error(`${message} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`);
  }
}

const out = (text) => process.stdout.write(text);

/** Strip block comments and full-line // comments before source matching. */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n');
}

installDom();

/** Render helper: block nodes for one answer text. */
function blocks(text) {
  return markdown.markdownBlocks(text);
}

function first(text) {
  const nodes = blocks(text);
  assertEqual(nodes.length, 1, 'expected exactly one block');
  return nodes[0];
}

/** All element nodes in a subtree (depth-first). */
function elements(root) {
  const found = [];
  const walk = (node) => {
    for (const child of node.children || []) {
      if (child.nodeType !== 1) continue;
      found.push(child);
      walk(child);
    }
  };
  walk(root);
  return found;
}

function tagNames(root) {
  return elements(root).map((node) => node.tagName);
}

function attributeNames(root) {
  return elements(root).flatMap((node) => Object.keys(node.attributes || {}));
}

const DANGEROUS_TAGS = new Set([
  'SCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META', 'STYLE', 'BASE',
  'FORM', 'INPUT', 'BUTTON', 'IMG', 'A', 'VIDEO', 'AUDIO', 'SVG', 'MATH',
]);
const DANGEROUS_ATTRIBUTES = new Set(['href', 'src', 'style', 'srcdoc', 'formaction', 'xlink:href', 'data', 'action']);

function assertInert(root, label) {
  for (const node of elements(root)) {
    assert(!DANGEROUS_TAGS.has(node.tagName), `${label}: dangerous element <${node.tagName.toLowerCase()}> created`);
    for (const name of Object.keys(node.attributes || {})) {
      assert(!name.toLowerCase().startsWith('on'), `${label}: event-handler attribute ${name} survived`);
      assert(!DANGEROUS_ATTRIBUTES.has(name.toLowerCase()), `${label}: dangerous attribute ${name} survived`);
    }
  }
}

/* ------------------------------------------------------- source-level XSS ban */

await check('markdown.js builds DOM nodes only (no HTML-parsing APIs)', () => {
  const code = stripComments(markdownSource);
  for (const banned of ['innerHTML', 'insertAdjacentHTML', 'outerHTML', 'DOMParser', 'parseFromString', 'createContextualFragment', 'document.write']) {
    assert(!code.includes(banned), `markdown.js must never use ${banned}`);
  }
});

/* ------------------------------------------------------------- block subset */

await check('headings map to h3/h4 and never create h1/h2', () => {
  const h3 = first('## Кратко обобщение');
  assertEqual(h3.tagName, 'H3', '## must render as h3');
  assertEqual(h3.textContent, 'Кратко обобщение', 'heading text');
  assertEqual(first('# Top').tagName, 'H3', '# must render as h3 too (no level skip)');
  const h4 = first('### Детайли');
  assertEqual(h4.tagName, 'H4', '### must render as h4');
  assertEqual(first('#### Дълбоко').tagName, 'H4', '#### must render as h4');
  const withHashes = first('## Заглавие ##');
  assertEqual(withHashes.textContent, 'Заглавие', 'trailing hashes are stripped');
  const hashInText = first('## C# basics');
  assertEqual(hashInText.textContent, 'C# basics', 'inner hashes stay literal');
  for (const text of ['## a', '### b', '#### c', '# d']) {
    const names = tagNames({ children: blocks(text) });
    assert(!names.includes('H1') && !names.includes('H2'), 'h1/h2 must never be created');
  }
});

await check('inline bold, italic, and code render as real elements', () => {
  const node = first('Стойност **6.1 mmol/L** (*високо*) в `K`.');
  assertEqual(node.tagName, 'P', 'plain line renders as a paragraph');
  const strong = node.querySelector('strong');
  assert(strong, 'bold must render as <strong>');
  assertEqual(strong.textContent, '6.1 mmol/L', 'bold content');
  const em = node.querySelector('em');
  assert(em, 'italic must render as <em>');
  assertEqual(em.textContent, 'високо', 'italic content');
  const code = node.querySelector('code');
  assert(code, 'inline code must render as <code>');
  assertEqual(code.textContent, 'K', 'code content');
  assert(textOf(node).includes('Стойност'), 'plain text around markup is preserved');

  // Bold containing italic/code nests instead of breaking.
  const nested = first('**има *отклонение* в `K`**');
  const outer = nested.querySelector('strong');
  assert(outer, 'nested bold renders');
  assert(outer.querySelector('em'), 'italic inside bold renders');
  assert(outer.querySelector('code'), 'code inside bold renders');
});

await check('unknown and unclosed inline syntax stays literal text', () => {
  for (const [input, expected] of [
    ['2 * 3 * 4 = 24', '2 * 3 * 4 = 24'],
    ['**незатворено', '**незатворено'],
    ['_undescore_ is not emphasis', '_undescore_ is not emphasis'],
    ['[link](https://example.invalid/x)', '[link](https://example.invalid/x)'],
    ['a ~ b | c', 'a ~ b | c'],
  ]) {
    const node = first(input);
    assertEqual(node.tagName, 'P', `input must fall back to a paragraph: ${input}`);
    assertEqual(textOf(node), expected, `literal text for: ${input}`);
    assertEqual(elements(node).length, 0, `no inline elements for: ${input}`);
  }
});

await check('unordered and ordered lists render, with one nesting level', () => {
  const ul = first('- един\n- два\n- три');
  assertEqual(ul.tagName, 'UL', 'dash list renders as ul');
  assertEqualList(ul.querySelectorAll('li').map((li) => li.textContent), ['един', 'два', 'три'], 'ul items');

  const ol = first('1. първи\n2. втори');
  assertEqual(ol.tagName, 'OL', 'numbered list renders as ol');
  assertEqualList(ol.querySelectorAll('li').map((li) => li.textContent), ['първи', 'втори'], 'ol items');

  const nested = first('- външен\n  - вътрешен\n- друг');
  assertEqual(nested.tagName, 'UL', 'nested list keeps the outer type');
  const outerItems = nested.children;
  assertEqual(outerItems.length, 2, 'two top-level items');
  const inner = outerItems[0].querySelector('ul');
  assert(inner, 'one nesting level renders inside the item');
  assertEqual(inner.querySelectorAll('li')[0].textContent, 'вътрешен', 'nested item text');
  assertEqual(outerItems[0].querySelectorAll('li').length, 1, 'nested item stays inside its parent');

  // Inline markup inside list items.
  const marked = first('- **6.1** високо ↑');
  assert(marked.querySelector('strong'), 'bold renders inside list items');
});

await check('pipe tables render as real thead/tbody tables', () => {
  const table = first([
    '| Пациент | Тест | Стойност |',
    '|---|---|---|',
    '| 5340c8c2-… | K | **6.1** |',
    '| 9f2c11aa-… | GLU | 5.2 |',
  ].join('\n'));
  assertEqual(table.tagName, 'DIV', 'the table is wrapped in a scroll container');
  assertEqual(table.className, 'ai-md-table-scroll', 'the wrapper is the horizontal scroll container');
  const real = table.querySelector('table');
  assert(real, 'a real <table> must be built');
  assertEqual(real.querySelectorAll('thead').length, 1, 'one head section');
  assertEqual(real.querySelectorAll('tbody').length, 1, 'one body section');
  assertEqualList(
    real.querySelectorAll('th').map((th) => th.textContent),
    ['Пациент', 'Тест', 'Стойност'],
    'header cells',
  );
  assertEqual(real.querySelectorAll('th')[0].getAttribute('scope'), 'col', 'headers are scoped columns');
  const rows = real.querySelector('tbody').querySelectorAll('tr');
  assertEqual(rows.length, 2, 'two body rows');
  assertEqual(rows[0].querySelectorAll('td')[2].querySelector('strong').textContent, '6.1', 'bold inside a cell');
  assertEqual(rows[0].querySelectorAll('td')[0].textContent, '5340c8c2-…', 'cell text is literal');

  // A table plus surrounding prose: prose stays paragraphs outside the table.
  const blocksOut = blocks(['**Обобщение:** 2-ма пациенти.', '', '| a | b |', '|---|---|', '| 1 | 2 |'].join('\n'));
  assertEqual(blocksOut.length, 2, 'paragraph + table');
  assertEqual(blocksOut[0].tagName, 'P', 'prose first');
  assertEqual(blocksOut[1].querySelector('table') ? 'table' : 'other', 'table', 'table second');

  // Raw pipe text without a separator row is NOT a table (renders literally).
  const raw = first('| само текст | още текст |');
  assertEqual(raw.tagName, 'P', 'pipe text without a separator row stays a paragraph');
  assertEqual(textOf(raw), '| само текст | още текст |', 'raw pipe text stays literal');
});

await check('table columns right-align when numeric (class-based, no style writes)', () => {
  const table = first([
    '| Пациент | Калий | Флаг |',
    '|---|---|---|',
    '| 5340c8c2-… | 6.1 | високо ↑ |',
    '| 9f2c11aa-… | 4.2 | ниско ↓ |',
  ].join('\n')).querySelector('table');
  const heads = table.querySelectorAll('th');
  assert(!heads[0].classList.contains('ai-md-num'), 'id column stays left');
  assert(heads[1].classList.contains('ai-md-num'), 'numeric column gets the alignment class');
  assert(!heads[2].classList.contains('ai-md-num'), 'flag column stays left');
  const bodyCells = table.querySelector('tbody').querySelectorAll('td');
  assert(bodyCells[1].classList.contains('ai-md-num'), 'numeric body cells carry the class');
  assert(!bodyCells[0].classList.contains('ai-md-num'), 'non-numeric body cells do not');

  // Explicit "---:" alignment markers force the class even on non-numeric data.
  const marked = first(['| име | дата |', '|---|---:|', '| а | 27 окт 2026 |'].join('\n')).querySelector('table');
  assert(marked.querySelectorAll('th')[1].classList.contains('ai-md-num'), '---: marks a right-aligned column');

  // Percentages and units still read as numeric; dates do not.
  const units = first(['| тест | стойност | дата | час |', '|---|---|---|---|', '| HCT | 45% | 2026-10-27 | 16.08.2026 11:00 |'].join('\n')).querySelector('table');
  assert(units.querySelectorAll('th')[1].classList.contains('ai-md-num'), 'percent column reads numeric');
  assert(!units.querySelectorAll('th')[2].classList.contains('ai-md-num'), 'ISO date column does not read numeric');
  assert(!units.querySelectorAll('th')[3].classList.contains('ai-md-num'), 'dotted date-time column does not read numeric');
});

await check('ragged table rows are padded to the header width', () => {
  const table = first([
    '| a | b | c |',
    '|---|---|---|',
    '| 1 |',
    '| 1 | 2 | 3 | 4 |',
  ].join('\n')).querySelector('table');
  const rows = table.querySelector('tbody').querySelectorAll('tr');
  assertEqual(rows[0].querySelectorAll('td').length, 3, 'short rows are padded');
  assertEqual(rows[0].querySelectorAll('td')[1].textContent, '', 'padded cells are empty text');
  assertEqual(rows[1].querySelectorAll('td').length, 3, 'long rows are truncated to the header width');
});

await check('records-consulted tail folds into a native details/summary', () => {
  const out = blocks([
    '**Обобщение:** 2-ма пациенти с отклонения в калия.',
    '',
    '| Пациент | Стойност |',
    '|---|---|',
    '| 5340c8c2-… | **6.1** |',
    '',
    '### Records consulted',
    '',
    '- 5340c8c2-…',
    '- 9f2c11aa-…',
  ].join('\n'));
  assertEqual(out.length, 3, 'paragraph, table, details');
  assertEqual(out[2].tagName, 'DETAILS', 'the tail section becomes <details>');
  assertEqual(out[2].children[0].tagName, 'SUMMARY', 'first child is the <summary>');
  assertEqual(out[2].children[0].textContent, 'Records consulted', 'summary carries the heading text');
  assertEqual(out[2].querySelectorAll('li').length, 2, 'the records list stays inside the disclosure');
  assertEqualList(tagNames({ children: out }).filter((name) => name === 'H4'), [], 'the folded heading is gone');
  assertEqualList(Object.keys(out[2].attributes), [], 'details is attribute-free');
  assertEqualList(Object.keys(out[2].children[0].attributes), [], 'summary is attribute-free');

  // A tail heading that is NOT last stays a normal heading.
  const mid = blocks('### Records consulted\n\n- p1\n\n## После\n\nтекст');
  assertEqual(mid[0].tagName, 'H4', 'mid-document records heading stays a heading');
  assertEqual(mid[mid.length - 1].tagName, 'P', 'content after it stays outside');
});

await check('Bulgarian records-consulted tail folds too', () => {
  const out = blocks(['**Обобщение:** един пациент.', '', '### Прегледани записи', '', '- p1'].join('\n'));
  assertEqual(out.length, 2, 'paragraph + details');
  assertEqual(out[1].tagName, 'DETAILS', 'Bulgarian tail folds into <details>');
  assertEqual(out[1].children[0].textContent, 'Прегледани записи', 'summary keeps the Bulgarian label');

  const labelled = blocks('### Прегледани записи (Records consulted)\n\n- p1');
  assertEqual(labelled[0].tagName, 'DETAILS', 'a bilingual tail label folds as well');
});

await check('literal details tags: whitelisted pattern only, all attributes dropped', () => {
  const out = blocks('<details open onmouseover="alert(1)"><summary onclick="x()">Мои записи</summary>\n- ред 1\n</details>');
  assertEqual(out.length, 1, 'one details block');
  const details = out[0];
  assertEqual(details.tagName, 'DETAILS', 'whitelisted literal details is recognised');
  assertEqualList(Object.keys(details.attributes), [], 'every attribute (including open/onmouseover) is dropped');
  assertEqual(details.children[0].tagName, 'SUMMARY', 'summary extracted');
  assertEqualList(Object.keys(details.children[0].attributes), [], 'summary attributes dropped too');
  assertEqual(details.children[0].textContent, 'Мои записи', 'summary text survives as text');
  assertEqual(details.querySelectorAll('li')[0].textContent, 'ред 1', 'body is re-rendered as Markdown');
  assertInert({ children: [details] }, 'literal details');

  // Other tags inside the body are never parsed: they stay inert text.
  const withScript = blocks('<details><summary>t</summary>\n<script>alert(1)</script>\n</details>');
  assertEqual(withScript[0].tagName, 'DETAILS', 'details still recognised');
  assertEqual(textOf(withScript[0]).includes('<script>alert(1)</script>'), true, 'inner HTML stays literal text');
  assertInert({ children: withScript }, 'details with inner script');

  // An unclosed <details> is plain text, not markup.
  const unclosed = first('<details><summary>t</summary>\nникога не се затваря');
  assertEqual(unclosed.tagName, 'P', 'unclosed details falls back to a paragraph');
  assertInert({ children: [unclosed] }, 'unclosed details');
});

await check('horizontal rules and blockquotes render', () => {
  assertEqual(first('---').tagName, 'HR', 'three dashes render as a rule');
  assertEqual(first('------').tagName, 'HR', 'longer rules render too');
  const quote = first('> Внимание: това е демонстрационен запис.');
  assertEqual(quote.tagName, 'BLOCKQUOTE', 'quote lines render as a blockquote');
  assertEqual(textOf(quote), 'Внимание: това е демонстрационен запис.', 'quote marker is stripped');
});

await check('unparseable lines fall back to plain paragraphs like before', () => {
  const out = blocks('Първа част.\nс мек нов ред\n\nВтора част.');
  assertEqual(out.length, 2, 'paragraphs split on blank lines exactly like the old renderer');
  assertEqual(out[0].tagName, 'P', 'first paragraph');
  assertEqual(textOf(out[0]), 'Първа част.\nс мек нов ред', 'soft newlines stay literal inside a paragraph');
  assertEqual(textOf(out[1]), 'Втора част.', 'second paragraph');

  assertEqual(blocks('').length, 0, 'empty input has no blocks');
  assertEqual(blocks('   \n\n  ').length, 0, 'blank input has no blocks');
});

await check('answerBody wraps in .ai-answer and returns null when empty', () => {
  const body = markdown.answerBody('## Обобщение\n\nтекст');
  assertEqual(body.tagName, 'DIV', 'wrapper div');
  assertEqual(body.className, 'ai-answer', 'wrapper class');
  assertEqual(body.querySelectorAll('h3').length, 1, 'blocks are inside the wrapper');
  assertEqual(markdown.answerBody(''), null, 'empty answer returns null (callers keep their fallback note)');
});

/* ----------------------------------------------------------------- XSS suite */

await check('XSS: script tags render as inert text (no script elements)', () => {
  const out = blocks('<script>alert(1)</script>');
  assertEqual(out.length, 1, 'one paragraph');
  assertEqual(textOf(out[0]), '<script>alert(1)</script>', 'the payload is literal text');
  assertEqualList(tagNames({ children: out }).filter((name) => name === 'SCRIPT'), [], 'no script element created');
  assertInert({ children: out }, 'script tag');

  const inline = first('преди <script>alert(1)</script> след');
  assertEqual(textOf(inline), 'преди <script>alert(1)</script> след', 'inline script stays literal');
  assertInert({ children: [inline] }, 'inline script');
});

await check('XSS: img onerror renders as inert text (no img element, no onerror)', () => {
  const out = blocks('<img src=x onerror=alert(1)>');
  assertEqual(textOf(out[0]), '<img src=x onerror=alert(1)>', 'the payload is literal text');
  assertEqualList(tagNames({ children: out }).filter((name) => name === 'IMG'), [], 'no img element created');
  assert(!attributeNames({ children: out }).includes('onerror'), 'no onerror attribute anywhere');
  assertInert({ children: out }, 'img onerror');

  const smuggled = first('**<img src=x onerror=alert(1)>**');
  assert(smuggled.querySelector('strong'), 'bold around a payload still renders as strong');
  assertEqual(textOf(smuggled.querySelector('strong')), '<img src=x onerror=alert(1)>', 'payload stays text inside');
  assertInert({ children: [smuggled] }, 'img onerror inside bold');
});

await check('XSS: javascript: links are never links', () => {
  for (const payload of [
    '[click](javascript:alert(1))',
    '[click](JaVaScRiPt:alert(1))',
    '[click](data:text/html,<script>alert(1)</script>)',
  ]) {
    const node = first(payload);
    assertEqual(node.tagName, 'P', `link syntax stays a paragraph: ${payload}`);
    assertEqualList(tagNames({ children: [node] }).filter((name) => name === 'A'), [], 'no <a> element created');
    assertEqual(textOf(node), payload, 'link syntax stays literal text');
    assert(!attributeNames({ children: [node] }).includes('href'), 'no href attribute anywhere');
    assertInert({ children: [node] }, 'javascript link');
  }
});

await check('XSS: details attributes never survive', () => {
  const out = blocks('<details open id="x" class="y" style="position:fixed" onfocus="alert(1)"><summary tabindex="1" onmouseover="alert(2)">t</summary>body</details>');
  const details = out[0];
  assertEqual(details.tagName, 'DETAILS', 'whitelisted details still renders');
  assertEqualList(Object.keys(details.attributes), [], 'details carries no attributes at all');
  assertEqualList(Object.keys(details.children[0].attributes), [], 'summary carries no attributes at all');
  assertInert({ children: out }, 'details attributes');
});

await check('XSS: HTML entities stay inert literal text', () => {
  for (const payload of [
    '&lt;script&gt;alert(1)&lt;/script&gt;',
    '&amp;lt;b&amp;gt;',
    '&#60;img src=x onerror=alert(1)&#62;',
  ]) {
    const node = first(payload);
    assertEqual(textOf(node), payload, 'entities are shown literally, never decoded');
    assertEqualList(elements(node), [], 'entities create no elements');
  }
});

await check('XSS: tree-wide sweep finds no handlers, URLs, or dangerous nodes', () => {
  const kitchenSink = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '[x](javascript:alert(1))',
    '<details open onmouseover="alert(1)"><summary onclick="alert(2)">t</summary><iframe src="x"></iframe></details>',
    '| **a** | <svg onload=alert(1)> |',
    '|---|---|',
    '| 1 | 2 |',
    '### Records consulted',
    '- <a href="x">y</a>',
    '> <style>body{display:none}</style>',
  ].join('\n');
  const out = blocks(kitchenSink);
  assert(out.length > 0, 'the input still renders');
  assertInert({ children: out }, 'kitchen sink');
  const names = attributeNames({ children: out });
  assertEqualList(names.filter((name) => name !== 'class' && name !== 'scope'), [], 'only class/scope attributes may exist');
  for (const name of names) {
    assert(!name.toLowerCase().startsWith('on'), `event-handler attribute ${name} must not survive`);
  }
});

/* ---------------------------------------------------------------- report */

const failed = results.filter((r) => !r.ok);
for (const result of results) {
  if (!result.ok) out(`FAIL ${result.name}: ${result.detail}\n`);
}
out(`\n${results.length - failed.length}/${results.length} markdown renderer checks passed\n`);
if (failed.length > 0) {
  out(`${failed.length} check(s) failed\n`);
  process.exitCode = 1;
}
