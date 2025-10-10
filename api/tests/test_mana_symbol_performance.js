// Test file to verify mana symbol replacement functionality and performance

// OLD IMPLEMENTATION (forEach loops)
function convertManaSymbols_Old(manaCost, isModal = false) {
  if (!manaCost) return '';

  const manaMap = {
    '{R}': 'ms ms-r ms-cost',
    '{G}': 'ms ms-g ms-cost',
    '{W}': 'ms ms-w ms-cost',
    '{U}': 'ms ms-u ms-cost',
    '{B}': 'ms ms-b ms-cost',
    '{C}': 'ms ms-c ms-cost',
    '{0}': 'ms ms-0 ms-cost',
    '{1}': 'ms ms-1 ms-cost',
    '{2}': 'ms ms-2 ms-cost',
    '{3}': 'ms ms-3 ms-cost',
    '{4}': 'ms ms-4 ms-cost',
    '{5}': 'ms ms-5 ms-cost',
    '{6}': 'ms ms-6 ms-cost',
    '{7}': 'ms ms-7 ms-cost',
    '{8}': 'ms ms-8 ms-cost',
    '{9}': 'ms ms-9 ms-cost',
    '{10}': 'ms ms-10 ms-cost',
    '{11}': 'ms ms-11 ms-cost',
    '{12}': 'ms ms-12 ms-cost',
    '{13}': 'ms ms-13 ms-cost',
    '{14}': 'ms ms-14 ms-cost',
    '{15}': 'ms ms-15 ms-cost',
    '{16}': 'ms ms-16 ms-cost',
    '{X}': 'ms ms-x ms-cost',
    '{Y}': 'ms ms-y ms-cost',
    '{Z}': 'ms ms-z ms-cost',
    '{T}': 'ms ms-tap',
    '{Q}': 'ms ms-untap',
    '{E}': 'ms ms-energy',
    '{P}': 'ms ms-phyrexian ms-cost',
    '{S}': 'ms ms-snow ms-cost',
    '{CHAOS}': 'ms ms-chaos',
    '{PW}': 'ms ms-pw',
    '{∞}': 'ms ms-infinity',
  };

  const hybridMap = {
    '{W/U}': 'ms ms-wu ms-cost',
    '{U/B}': 'ms ms-ub ms-cost',
    '{B/R}': 'ms ms-br ms-cost',
    '{R/G}': 'ms ms-rg ms-cost',
    '{G/W}': 'ms ms-gw ms-cost',
    '{W/B}': 'ms ms-wb ms-cost',
    '{U/R}': 'ms ms-ur ms-cost',
    '{B/G}': 'ms ms-bg ms-cost',
    '{R/W}': 'ms ms-rw ms-cost',
    '{G/U}': 'ms ms-gu ms-cost',
    '{2/W}': 'ms ms-2w ms-cost',
    '{2/U}': 'ms ms-2u ms-cost',
    '{2/B}': 'ms ms-2b ms-cost',
    '{2/R}': 'ms ms-2r ms-cost',
    '{2/G}': 'ms ms-2g ms-cost',
    '{W/P}': 'ms ms-wp ms-cost',
    '{U/P}': 'ms ms-up ms-cost',
    '{B/P}': 'ms ms-bp ms-cost',
    '{R/P}': 'ms ms-rp ms-cost',
    '{G/P}': 'ms ms-gp ms-cost',
    '{W/U/P}': 'ms ms-wup ms-cost',
    '{W/B/P}': 'ms ms-wbp ms-cost',
    '{U/B/P}': 'ms ms-ubp ms-cost',
    '{U/R/P}': 'ms ms-urp ms-cost',
    '{B/R/P}': 'ms ms-brp ms-cost',
    '{B/G/P}': 'ms ms-bgp ms-cost',
    '{R/W/P}': 'ms ms-rwp ms-cost',
    '{R/G/P}': 'ms ms-rgp ms-cost',
    '{G/W/P}': 'ms ms-gwp ms-cost',
    '{G/U/P}': 'ms ms-gup ms-cost',
  };

  let converted = manaCost;
  const symbolClass = isModal ? 'modal-mana-symbol' : 'mana-symbol';

  // Process hybrid symbols first
  Object.keys(hybridMap).forEach(symbol => {
    const regex = new RegExp(symbol.replace(/[{}]/g, '\\$&'), 'g');
    converted = converted.replace(regex, `<span class="${symbolClass} ${hybridMap[symbol]}"></span>`);
  });

  // Process regular mana symbols
  Object.keys(manaMap).forEach(symbol => {
    const regex = new RegExp(symbol.replace(/[{}]/g, '\\$&'), 'g');
    converted = converted.replace(regex, `<span class="${symbolClass} ${manaMap[symbol]}"></span>`);
  });

  return converted;
}

// NEW IMPLEMENTATION (simple pattern with map lookup - final optimized version)
class ManaConverter {
  constructor() {
    const manaMap = {
      '{R}': 'ms ms-r ms-cost',
      '{G}': 'ms ms-g ms-cost',
      '{W}': 'ms ms-w ms-cost',
      '{U}': 'ms ms-u ms-cost',
      '{B}': 'ms ms-b ms-cost',
      '{C}': 'ms ms-c ms-cost',
      '{0}': 'ms ms-0 ms-cost',
      '{1}': 'ms ms-1 ms-cost',
      '{2}': 'ms ms-2 ms-cost',
      '{3}': 'ms ms-3 ms-cost',
      '{4}': 'ms ms-4 ms-cost',
      '{5}': 'ms ms-5 ms-cost',
      '{6}': 'ms ms-6 ms-cost',
      '{7}': 'ms ms-7 ms-cost',
      '{8}': 'ms ms-8 ms-cost',
      '{9}': 'ms ms-9 ms-cost',
      '{10}': 'ms ms-10 ms-cost',
      '{11}': 'ms ms-11 ms-cost',
      '{12}': 'ms ms-12 ms-cost',
      '{13}': 'ms ms-13 ms-cost',
      '{14}': 'ms ms-14 ms-cost',
      '{15}': 'ms ms-15 ms-cost',
      '{16}': 'ms ms-16 ms-cost',
      '{X}': 'ms ms-x ms-cost',
      '{Y}': 'ms ms-y ms-cost',
      '{Z}': 'ms ms-z ms-cost',
      '{T}': 'ms ms-tap',
      '{Q}': 'ms ms-untap',
      '{E}': 'ms ms-energy',
      '{P}': 'ms ms-phyrexian ms-cost',
      '{S}': 'ms ms-snow ms-cost',
      '{CHAOS}': 'ms ms-chaos',
      '{PW}': 'ms ms-pw',
      '{∞}': 'ms ms-infinity',
    };

    const hybridMap = {
      '{W/U}': 'ms ms-wu ms-cost',
      '{U/B}': 'ms ms-ub ms-cost',
      '{B/R}': 'ms ms-br ms-cost',
      '{R/G}': 'ms ms-rg ms-cost',
      '{G/W}': 'ms ms-gw ms-cost',
      '{W/B}': 'ms ms-wb ms-cost',
      '{U/R}': 'ms ms-ur ms-cost',
      '{B/G}': 'ms ms-bg ms-cost',
      '{R/W}': 'ms ms-rw ms-cost',
      '{G/U}': 'ms ms-gu ms-cost',
      '{2/W}': 'ms ms-2w ms-cost',
      '{2/U}': 'ms ms-2u ms-cost',
      '{2/B}': 'ms ms-2b ms-cost',
      '{2/R}': 'ms ms-2r ms-cost',
      '{2/G}': 'ms ms-2g ms-cost',
      '{W/P}': 'ms ms-wp ms-cost',
      '{U/P}': 'ms ms-up ms-cost',
      '{B/P}': 'ms ms-bp ms-cost',
      '{R/P}': 'ms ms-rp ms-cost',
      '{G/P}': 'ms ms-gp ms-cost',
      '{W/U/P}': 'ms ms-wup ms-cost',
      '{W/B/P}': 'ms ms-wbp ms-cost',
      '{U/B/P}': 'ms ms-ubp ms-cost',
      '{U/R/P}': 'ms ms-urp ms-cost',
      '{B/R/P}': 'ms ms-brp ms-cost',
      '{B/G/P}': 'ms ms-bgp ms-cost',
      '{R/W/P}': 'ms ms-rwp ms-cost',
      '{R/G/P}': 'ms ms-rgp ms-cost',
      '{G/W/P}': 'ms ms-gwp ms-cost',
      '{G/U/P}': 'ms ms-gup ms-cost',
    };

    // Cache the merged symbol map
    // Use simple pattern that matches any content between braces (1-5 chars)
    // Use Map for O(1) lookup with single get() operation
    this.allSymbols = new Map(Object.entries({ ...hybridMap, ...manaMap }));
    this.regex = /\{[^}]{1,5}\}/g;
  }

  convert(manaCost, isModal = false) {
    if (!manaCost) return '';
    const symbolClass = isModal ? 'modal-mana-symbol' : 'mana-symbol';
    this.regex.lastIndex = 0; // Reset regex state
    return manaCost.replace(this.regex, (match) => {
      const replacement = this.allSymbols.get(match);
      if (replacement === undefined) {
        return match;
      }
      return `<span class="${symbolClass} ${replacement}"></span>`;
    });
  }
}

const manaConverter = new ManaConverter();
function convertManaSymbols_New(manaCost, isModal = false) {
  return manaConverter.convert(manaCost, isModal);
}

// Test cases
const testCases = [
  '{W}{U}{B}{R}{G}',
  '{1}{R}{R}{R}',
  '{X}{X}{W}{U}',
  '{2}{W/U}{B/R}',
  '{3}{W/U/P}{G}',
  '{W}{W}{W}{W}{W}',
  '{1}{2}{3}{4}{5}',
  '{W/U}{U/B}{B/R}{R/G}{G/W}',
  '{T}{Q}{E}{P}{S}',
  '{CHAOS}{PW}{∞}',
  '{16}{G}{G}{G}',
  '{2/W}{2/U}{2/B}{2/R}{2/G}',
  '{W/P}{U/P}{B/P}{R/P}{G/P}',
  '{W/U/P}{B/R/P}{G/U/P}',
];

console.log('=== Mana Symbol Replacement Test ===\n');

// Verify correctness
console.log('1. Verifying correctness...');
let allCorrect = true;
for (const testCase of testCases) {
  const oldResult = convertManaSymbols_Old(testCase);
  const newResult = convertManaSymbols_New(testCase);
  if (oldResult !== newResult) {
    allCorrect = false;
    console.log(`❌ FAILED for: ${testCase}`);
    console.log(`   Old: ${oldResult.substring(0, 100)}...`);
    console.log(`   New: ${newResult.substring(0, 100)}...`);
  }
}

if (allCorrect) {
  console.log('✅ All test cases produce identical results\n');
} else {
  console.log('❌ Some test cases differ\n');
  process.exit(1);
}

// Benchmark
console.log('2. Running performance benchmarks...');

const iterations = 10000;

// Warm up
for (let i = 0; i < 100; i++) {
  for (const testCase of testCases) {
    convertManaSymbols_Old(testCase);
    convertManaSymbols_New(testCase);
  }
}

// Benchmark old implementation
const startOld = process.hrtime.bigint();
for (let i = 0; i < iterations; i++) {
  for (const testCase of testCases) {
    convertManaSymbols_Old(testCase);
  }
}
const endOld = process.hrtime.bigint();
const oldTimeMs = Number(endOld - startOld) / 1_000_000;

// Benchmark new implementation
const startNew = process.hrtime.bigint();
for (let i = 0; i < iterations; i++) {
  for (const testCase of testCases) {
    convertManaSymbols_New(testCase);
  }
}
const endNew = process.hrtime.bigint();
const newTimeMs = Number(endNew - startNew) / 1_000_000;

const improvement = ((oldTimeMs - newTimeMs) / oldTimeMs * 100).toFixed(2);
const speedup = (oldTimeMs / newTimeMs).toFixed(2);

console.log(`\nResults (${iterations} iterations × ${testCases.length} test cases):`);
console.log(`  Old implementation (forEach): ${oldTimeMs.toFixed(2)}ms`);
console.log(`  New implementation (single regex): ${newTimeMs.toFixed(2)}ms`);
console.log(`  \x1b[32mPerformance improvement: ${improvement}%\x1b[0m`);
console.log(`  \x1b[32mSpeedup: ${speedup}x faster\x1b[0m`);

// Show sample output
console.log('\n3. Sample outputs:');
testCases.slice(0, 3).forEach(testCase => {
  const result = convertManaSymbols_New(testCase);
  console.log(`  Input:  ${testCase}`);
  console.log(`  Output: ${result.substring(0, 150)}...`);
  console.log('');
});

console.log('✅ All tests passed!');
