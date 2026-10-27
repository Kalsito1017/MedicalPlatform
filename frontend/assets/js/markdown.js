// ---------------------------------------------------------------------------
// markdown.js — tiny, dependency-free Markdown-subset to DOM renderer for AI
// answers. The model output is UNTRUSTED (an XSS boundary), so this module
// NEVER parses HTML: it never uses innerHTML / insertAdjacentHTML /
// outerHTML. Every piece of answer content becomes a text node
// (createTextNode / textContent), so any HTML or script in the answer is
// INERT TEXT. Attributes are only ever app-authored constants (class names,
// scope="col"); no attribute NAME or VALUE is ever taken from the answer.
// No side effects at module load (import-safe outside the browser); it uses
// the ambient `document` at call time.
//
// Supported subset (everything else renders as literal plain text):
//   - Headings: "# ", "## ", "### ", "#### "... map to h3 (1-2 hashes) and
//     h4 (3+ hashes) inside answers, so page heading order never skips
//     levels (no h1/h2 is ever created here).
//   - Inline: **bold** -> <strong>, *italic* -> <em>, `code` -> <code>.
//     Unknown or unclosed inline syntax (underscore emphasis, links, raw
//     HTML, HTML entities) stays literal text. Single newlines inside a
//     paragraph stay literal (they render as spaces, exactly like the
//     previous plain-paragraph path).
//   - Lists: "- " / "* " unordered and "1. " ordered, with one nesting level
//     (indent 2+ spaces) when present. List continuation lines render as
//     separate paragraphs.
//   - Tables: pipe tables with a header row AND a "|---|" separator row of
//     the same cell count -> real <table> with <thead>/<tbody>. Columns
//     whose body cells all look numeric (or whose separator marks right
//     alignment with "---:") get the class "ai-md-num" (right-aligned,
//     tabular-nums via CSS; class-based, never a style write). Ragged rows
//     are padded/truncated to the header width. Escaped pipes are not
//     supported.
//   - Trailing "### Records consulted" (also "### Прегледани записи") tail
//     section -> a native <details><summary> disclosure (built via DOM APIs
//     with NO attributes at all; CSS styles it generically).
//   - Literal <details> in the answer: recognised ONLY as the exact
//     whitelisted pattern <details ...> optional <summary ...>text</summary>
//     body </details>. All tags/attributes are DROPPED (never copied, never
//     parsed as HTML); the summary text and body are re-rendered through
//     this same Markdown subset; any other HTML inside stays inert text.
//     An unclosed <details> is plain text.
//   - Horizontal rules: a line of "---" / "***" / "___" -> <hr>.
//   - Blockquotes: "> " lines -> <blockquote>.
//   - Fallback: anything unrecognised renders as plain-text paragraphs,
//     exactly like the previous answer rendering.
//
// Public API:
//   markdownBlocks(text) -> array of block-level nodes
//   answerBody(text)     -> div.ai-answer node, or null when there is no
//                           content (callers keep their empty-answer note).
// ---------------------------------------------------------------------------

/* ------------------------------------------------------------- DOM helpers */

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function appendText(parent, value) {
  parent.append(document.createTextNode(String(value)));
}

/* ----------------------------------------------------------- inline subset */

// **bold** -> strong, *italic* -> em, `code` -> code. Delimiters run
// CommonMark-flavoured rules (no whitespace just inside the delimiters) so
// ordinary prose like "2 * 3 * 4" stays literal. Unclosed markers stay
// literal. Unknown syntax is never interpreted.
function appendInline(parent, source) {
  let index = 0;
  let text = '';
  const flush = () => {
    if (text !== '') {
      appendText(parent, text);
      text = '';
    }
  };
  while (index < source.length) {
    const char = source[index];
    const two = source.slice(index, index + 2);
    if (two === '**') {
      const end = source.indexOf('**', index + 2);
      if (end > index + 2 && source[index + 2] !== ' ' && source[end - 1] !== ' ') {
        flush();
        const strong = el('strong');
        appendInline(strong, source.slice(index + 2, end));
        parent.append(strong);
        index = end + 2;
        continue;
      }
    }
    if (char === '*' || char === '`') {
      const end = source.indexOf(char, index + 1);
      const content = end > index + 1 ? source.slice(index + 1, end) : '';
      const flanking = char === '`'
        || (source[index + 1] !== '*' && source[index + 1] !== ' ' && source[end - 1] !== ' ');
      if (end > index + 1 && flanking) {
        flush();
        const node = el(char === '`' ? 'code' : 'em');
        if (char === '`') appendText(node, content);
        else appendInline(node, content);
        parent.append(node);
        index = end + 1;
        continue;
      }
    }
    text += char;
    index += 1;
  }
  flush();
}

function appendParagraphText(parent, source) {
  const lines = String(source).split('\n');
  lines.forEach((line, position) => {
    if (position > 0) appendText(parent, '\n');
    appendInline(parent, line);
  });
}

/* ------------------------------------------------------------- block lines */

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR_LINE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const QUOTE_LINE = /^\s*>\s?(.*)$/;
const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(\S.*)$/;
const DETAILS_OPEN = /^<details\b[^>]*>/i;
const DETAILS_CLOSE_LINE = /<\/details\s*>/i;
const SUMMARY_BLOCK = /^\s*<summary\b[^>]*>([\s\S]*?)<\/summary\s*>/i;
// Word-boundary suffixes are avoided on purpose: JS \b is ASCII-only and
// would never match after Cyrillic text.
const RECORDS_LABEL = /^(records consulted|прегледан\S*\s+запис\S*)/i;

function splitRow(line) {
  let source = String(line).trim();
  if (source.startsWith('|')) source = source.slice(1);
  if (source.endsWith('|')) source = source.slice(0, -1);
  return source.split('|').map((cell) => cell.trim());
}

function isSeparatorRow(cells) {
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell));
}

// Numeric-looking for column alignment: an optional flag arrow, a number,
// then end or a non-alphanumeric suffix (unit, "%", " (високо)"). Dates
// (2026-10-27, 16.08.2026) and mixed text do not look numeric: "-", ".",
// and letters right after the number reject the match.
const NUMERICISH = /^[↑↓±]?\s*[-+]?[0-9]+(?:[.,][0-9]+)?(?:[eE][-+]?[0-9]+)?(?![0-9A-Za-zА-Яа-я.-])/;

function looksNumeric(value) {
  return NUMERICISH.test(value);
}

// Strip inline markers before the numeric test so **6.1** still counts.
function plainCell(value) {
  return String(value === undefined || value === null ? '' : value).replace(/[*`]/g, '').trim();
}

function buildTable(headerCells, separatorCells, bodyRows) {
  const columns = headerCells.length;
  const numeric = [];
  for (let column = 0; column < columns; column += 1) {
    const marked = /^:?-+:$/.test(separatorCells[column]) && separatorCells[column].endsWith(':');
    const values = bodyRows
      .map((row) => plainCell(row[column]))
      .filter((value) => value !== '');
    numeric.push(marked || (values.length > 0 && values.every(looksNumeric)));
  }

  const table = el('table', 'ai-md-table');
  const head = el('thead');
  const headRow = el('tr');
  headerCells.forEach((cell, column) => {
    const th = el('th', numeric[column] ? 'ai-md-num' : '');
    th.setAttribute('scope', 'col');
    appendInline(th, cell);
    headRow.append(th);
  });
  head.append(headRow);
  table.append(head);

  const body = el('tbody');
  for (const row of bodyRows) {
    const tr = el('tr');
    for (let column = 0; column < columns; column += 1) {
      const td = el('td', numeric[column] ? 'ai-md-num' : '');
      appendInline(td, row[column] === undefined ? '' : row[column]);
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);

  const scroller = el('div', 'ai-md-table-scroll');
  scroller.append(table);
  return scroller;
}

function buildList(lines) {
  // lines: [{ indent, ordered, text }] — one nesting level maximum.
  const first = lines[0];
  const topTag = first.ordered ? 'ol' : 'ul';
  const top = el(topTag);
  let topItem = null;
  let nested = null;
  for (const item of lines) {
    if (item.indent >= 2 && topItem) {
      const wantTag = item.ordered ? 'ol' : 'ul';
      if (!nested || nested.tagName !== wantTag.toUpperCase()) {
        nested = el(wantTag);
        topItem.append(nested);
      }
      const li = el('li');
      appendInline(li, item.text);
      nested.append(li);
    } else {
      nested = null;
      topItem = el('li');
      appendInline(topItem, item.text);
      top.append(topItem);
    }
  }
  return top;
}

function stripTags(text) {
  return String(text).replace(/<[^>]*>/g, '');
}

// Whitelisted literal <details> case: tags and ALL attributes are dropped;
// content is re-rendered through the same Markdown subset (any other HTML in
// it stays inert text because nothing here ever parses HTML).
function buildLiteralDetails(lines) {
  const raw = lines.join('\n');
  const rest = raw.slice(raw.indexOf('>') + 1);
  const details = el('details');
  const summary = el('summary');
  const summaryMatch = rest.match(SUMMARY_BLOCK);
  let bodySource = summaryMatch ? rest.slice(summaryMatch[0].length) : rest;
  const summaryText = summaryMatch ? stripTags(summaryMatch[1]).trim() : '';
  appendInline(summary, summaryText || 'Records consulted');
  details.append(summary);
  const closeIndex = bodySource.search(DETAILS_CLOSE_LINE);
  const trailing = closeIndex === -1 ? '' : bodySource.slice(closeIndex).replace(DETAILS_CLOSE_LINE, '');
  bodySource = closeIndex === -1 ? bodySource : bodySource.slice(0, closeIndex);
  for (const block of parseBlockLines(bodySource.split('\n'))) details.append(block);
  return { details, trailing };
}

function parseBlockLines(lines) {
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();
    if (trimmed === '') {
      index += 1;
      continue;
    }

    if (DETAILS_OPEN.test(trimmed)) {
      let end = index;
      while (end < lines.length && !DETAILS_CLOSE_LINE.test(lines[end])) end += 1;
      if (end < lines.length) {
        const { details, trailing } = buildLiteralDetails(lines.slice(index, end + 1));
        blocks.push(details);
        index = end + 1;
        const rest = trailing.trim();
        if (rest !== '') {
          const p = el('p');
          appendParagraphText(p, rest);
          blocks.push(p);
        }
        continue;
      }
      // Unclosed <details>: fall through and render the line as plain text.
    }

    const heading = trimmed.match(HEADING);
    if (heading) {
      const level = heading[1].length <= 2 ? 3 : 4;
      const node = el(`h${level}`);
      appendInline(node, heading[2]);
      blocks.push(node);
      index += 1;
      continue;
    }

    if (HR_LINE.test(line)) {
      blocks.push(el('hr'));
      index += 1;
      continue;
    }

    const quote = line.match(QUOTE_LINE);
    if (quote) {
      const quoted = [];
      while (index < lines.length) {
        const next = lines[index].match(QUOTE_LINE);
        if (!next) break;
        quoted.push(next[1]);
        index += 1;
      }
      const node = el('blockquote');
      const p = el('p');
      appendParagraphText(p, quoted.join('\n'));
      node.append(p);
      blocks.push(node);
      continue;
    }

    if (line.includes('|') && index + 1 < lines.length) {
      const headerCells = splitRow(line);
      const separatorCells = splitRow(lines[index + 1]);
      if (
        headerCells.length > 0
        && separatorCells.length === headerCells.length
        && isSeparatorRow(separatorCells)
      ) {
        const rows = [];
        let cursor = index + 2;
        while (cursor < lines.length && lines[cursor].trim() !== '' && lines[cursor].includes('|')) {
          rows.push(splitRow(lines[cursor]));
          cursor += 1;
        }
        blocks.push(buildTable(headerCells, separatorCells, rows));
        index = cursor;
        continue;
      }
    }

    const listItem = line.match(LIST_ITEM);
    if (listItem) {
      const items = [];
      while (index < lines.length) {
        const next = lines[index].match(LIST_ITEM);
        if (!next) break;
        items.push({
          indent: next[1].replace(/\t/g, '    ').length,
          ordered: /[0-9]/.test(next[2][0]),
          text: next[3],
        });
        index += 1;
      }
      blocks.push(buildList(items));
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (
      index < lines.length
      && lines[index].trim() !== ''
      && !HEADING.test(lines[index].trim())
      && !HR_LINE.test(lines[index])
      && !QUOTE_LINE.test(lines[index])
      && !LIST_ITEM.test(lines[index])
      && !DETAILS_OPEN.test(lines[index].trim())
      && !(lines[index].includes('|') && index + 1 < lines.length && isSeparatorRow(splitRow(lines[index + 1])))
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    const node = el('p');
    appendParagraphText(node, paragraph.join('\n'));
    blocks.push(node);
  }
  return blocks;
}

/* ------------------------------------------- trailing records-consulted fold */

// A trailing "### Records consulted" (or Bulgarian "### Прегледани записи")
// tail section becomes a native <details><summary> disclosure — the only
// element pair this module builds with semantic open/close behaviour, and
// built with NO attributes (CSS styles it generically).
function foldRecordsTail(blocks) {
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const node = blocks[i];
    if (node.nodeType !== 1 || (node.tagName !== 'H3' && node.tagName !== 'H4')) continue;
    const label = node.textContent.trim();
    if (!RECORDS_LABEL.test(label)) return blocks;
    const summary = el('summary');
    appendInline(summary, label);
    const details = el('details');
    details.append(summary);
    for (const block of blocks.slice(i + 1)) details.append(block);
    return [...blocks.slice(0, i), details];
  }
  return blocks;
}

/* ------------------------------------------------------------- public API */

/** The answer text as an array of block-level DOM nodes (see module header). */
export function markdownBlocks(source) {
  const text = String(source === null || source === undefined ? '' : source).replace(/\r\n?/g, '\n');
  return foldRecordsTail(parseBlockLines(text.split('\n')));
}

/** The answer body div (class ai-answer), or null when there is no content. */
export function answerBody(source) {
  const blocks = markdownBlocks(source);
  if (blocks.length === 0) return null;
  const wrapper = el('div', 'ai-answer');
  for (const block of blocks) wrapper.append(block);
  return wrapper;
}
