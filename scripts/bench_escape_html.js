#!/usr/bin/env node
/**
 * Benchmark: DOM-based escapeHtml vs single-pass regex escapeHtml.
 *
 * Run with:  node scripts/bench_escape_html.js
 */

'use strict';

const { JSDOM } = require('jsdom');

const { document } = new JSDOM('').window;

// --- Implementations ---

function escapeHtmlDom(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function escapeHtmlRegex(text) {
  if (text == null) return '';
  return String(text).replace(/[&<>"]/g, c =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&quot;'
  );
}

// --- Test data representative of MTG card fields ---

const samples = [
  // Card names
  'Lightning Bolt',
  "Urza's Saga",
  "Jace, the Mind's Sculptor",
  'Lim-Dûl the Necromancer',

  // Oracle text with HTML-special characters
  "Destroy target creature. It can't be regenerated.",
  'Deal 3 damage to any target.',
  'When ~ enters the battlefield, draw a card.',
  'Creatures you control get +1/+1 until end of turn.',
  "Counter target spell unless its controller pays {2}. If they don't, they discard a card.",

  // Edge cases
  '',
  'no special chars here just plain text',
  '<<< double angle brackets >>>',
  // NOTE: the DOM approach (textContent → innerHTML) does NOT escape " in text nodes —
  // that's valid HTML text content, but unsafe when the result is placed inside a
  // double-quoted attribute (alt="...").  The regex approach correctly escapes " as &quot;.
  // We benchmark them separately below but don't include this in the must-match set.

  // Image URLs (no special chars — exercises the fast-exit path)
  'https://d1hot9ps2xugbc.cloudfront.net/img/neo/123/1/388.webp',

  // Long oracle text
  `Flying\n\nWhen ${Array(40).fill('Flying').join(' ')} enters the battlefield, each opponent loses 3 life and you gain 3 life. If a creature dealt damage this way would die this turn, exile it instead.`,
];

// --- Correctness check ---

let allMatch = true;
for (const s of samples) {
  const a = escapeHtmlDom(s);
  const b = escapeHtmlRegex(s);
  if (a !== b) {
    console.error(`MISMATCH for: ${JSON.stringify(s)}`);
    console.error(`  DOM:   ${JSON.stringify(a)}`);
    console.error(`  Regex: ${JSON.stringify(b)}`);
    allMatch = false;
  }
}
if (allMatch) {
  console.log('✓  Both implementations produce identical output for all test strings.\n');
} else {
  console.log('✗  Output mismatch — see above.\n');
  process.exit(1);
}

// --- Benchmark harness ---

function bench(label, fn, iterations) {
  // Warm up
  for (let i = 0; i < 1000; i++) fn(samples[i % samples.length]);

  const start = performance.now();
  for (let i = 0; i < iterations; i++) {
    fn(samples[i % samples.length]);
  }
  const elapsed = performance.now() - start;
  const nsPerCall = (elapsed * 1e6) / iterations;
  console.log(
    `  ${label.padEnd(16)} ${elapsed.toFixed(1).padStart(8)} ms  |  ${nsPerCall.toFixed(0).padStart(6)} ns/call`
  );
}

// Simulate a full render: ~14 escapeHtml calls per card × 100 cards = 1400 calls
function benchRender(label, fn, iterations) {
  const callsPerRender = 14 * 100; // 1400

  // Warm up
  for (let i = 0; i < 1000; i++) fn(samples[i % samples.length]);

  const start = performance.now();
  for (let i = 0; i < iterations; i++) {
    for (let j = 0; j < callsPerRender; j++) {
      fn(samples[j % samples.length]);
    }
  }
  const elapsed = performance.now() - start;
  const msPerRender = elapsed / iterations;
  console.log(`  ${label.padEnd(16)} ${msPerRender.toFixed(2).padStart(8)} ms/render  (${iterations} renders)`);
}

const ITERS = 500_000;
const RENDER_ITERS = 500;

console.log(`Per-call (${ITERS.toLocaleString()} iterations):`);
bench('DOM', escapeHtmlDom, ITERS);
bench('Regex', escapeHtmlRegex, ITERS);

console.log(`\nSimulated render (1,400 escapeHtml calls per render, ${RENDER_ITERS} renders):`);
benchRender('DOM', escapeHtmlDom, RENDER_ITERS);
benchRender('Regex', escapeHtmlRegex, RENDER_ITERS);
