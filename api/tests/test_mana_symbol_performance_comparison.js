// Test file comparing three mana symbol replacement implementations

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

// Shared mana maps
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

// OPTION 1: forEach loops (original implementation)
function convertManaSymbols_ForEach(manaCost, isModal = false) {
  if (!manaCost) return '';

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

// OPTION 2: Cached regex with alternation (current optimized implementation)
class ManaConverterCached {
  constructor() {
    const allSymbols = { ...hybridMap, ...manaMap };
    const sortedSymbols = Object.keys(allSymbols).sort((a, b) => b.length - a.length);
    const pattern = sortedSymbols.map(s => s.replace(/[{}]/g, '\\$&')).join('|');
    this.allSymbols = allSymbols;
    this.regex = new RegExp(pattern, 'g');
  }

  convert(manaCost, isModal = false) {
    if (!manaCost) return '';
    const symbolClass = isModal ? 'modal-mana-symbol' : 'mana-symbol';
    this.regex.lastIndex = 0;
    return manaCost.replace(this.regex, match => {
      return `<span class="${symbolClass} ${this.allSymbols[match]}"></span>`;
    });
  }
}

const cachedConverter = new ManaConverterCached();
function convertManaSymbols_Cached(manaCost, isModal = false) {
  return cachedConverter.convert(manaCost, isModal);
}

// OPTION 3: Simple regex pattern with map lookup
class ManaConverterSimple {
  constructor() {
    // Use Map for O(1) lookup with single get() operation
    this.allSymbols = new Map(Object.entries({ ...hybridMap, ...manaMap }));
    // Simple pattern: match anything between braces with 1-5 characters
    this.regex = /\{[^}]{1,5}\}/g;
  }

  convert(manaCost, isModal = false) {
    if (!manaCost) return '';
    const symbolClass = isModal ? 'modal-mana-symbol' : 'mana-symbol';
    this.regex.lastIndex = 0;
    return manaCost.replace(this.regex, match => {
      const replacement = this.allSymbols.get(match);
      if (replacement === undefined) {
        return match;
      }
      return `<span class="${symbolClass} ${replacement}"></span>`;
    });
  }
}

const simpleConverter = new ManaConverterSimple();
function convertManaSymbols_Simple(manaCost, isModal = false) {
  return simpleConverter.convert(manaCost, isModal);
}

console.log('=== Mana Symbol Replacement Comparison ===\n');

// 1. Verify correctness
console.log('1. Verifying correctness...');
let allCorrect = true;
const forEachResults = [];
const cachedResults = [];
const simpleResults = [];

for (const testCase of testCases) {
  const forEachResult = convertManaSymbols_ForEach(testCase);
  const cachedResult = convertManaSymbols_Cached(testCase);
  const simpleResult = convertManaSymbols_Simple(testCase);

  forEachResults.push(forEachResult);
  cachedResults.push(cachedResult);
  simpleResults.push(simpleResult);

  if (forEachResult !== cachedResult || forEachResult !== simpleResult) {
    allCorrect = false;
    console.log(`❌ FAILED for: ${testCase}`);
    console.log(`   ForEach: ${forEachResult.substring(0, 100)}...`);
    console.log(`   Cached:  ${cachedResult.substring(0, 100)}...`);
    console.log(`   Simple:  ${simpleResult.substring(0, 100)}...`);
  }
}

if (allCorrect) {
  console.log('✅ All implementations produce identical results\n');
} else {
  console.log('❌ Some implementations differ\n');
}

// 2. Benchmark
console.log('2. Running performance benchmarks...');

const iterations = 10000;

// Warm up
for (let i = 0; i < 100; i++) {
  for (const testCase of testCases) {
    convertManaSymbols_ForEach(testCase);
    convertManaSymbols_Cached(testCase);
    convertManaSymbols_Simple(testCase);
  }
}

// Benchmark forEach
const startForEach = process.hrtime.bigint();
for (let i = 0; i < iterations; i++) {
  for (const testCase of testCases) {
    convertManaSymbols_ForEach(testCase);
  }
}
const endForEach = process.hrtime.bigint();
const forEachTime = Number(endForEach - startForEach) / 1_000_000;

// Benchmark cached
const startCached = process.hrtime.bigint();
for (let i = 0; i < iterations; i++) {
  for (const testCase of testCases) {
    convertManaSymbols_Cached(testCase);
  }
}
const endCached = process.hrtime.bigint();
const cachedTime = Number(endCached - startCached) / 1_000_000;

// Benchmark simple
const startSimple = process.hrtime.bigint();
for (let i = 0; i < iterations; i++) {
  for (const testCase of testCases) {
    convertManaSymbols_Simple(testCase);
  }
}
const endSimple = process.hrtime.bigint();
const simpleTime = Number(endSimple - startSimple) / 1_000_000;

console.log(`\nResults (${iterations} iterations × ${testCases.length} test cases):\n`);

// Calculate improvements
const cachedVsForEach = (((forEachTime - cachedTime) / forEachTime) * 100).toFixed(2);
const cachedSpeedup = (forEachTime / cachedTime).toFixed(2);

const simpleVsForEach = (((forEachTime - simpleTime) / forEachTime) * 100).toFixed(2);
const simpleSpeedup = (forEachTime / simpleTime).toFixed(2);

const cachedVsSimple = (((simpleTime - cachedTime) / simpleTime) * 100).toFixed(2);
const cachedVsSimpleSpeedup = (simpleTime / cachedTime).toFixed(2);

console.log(`Option 1 - forEach loops (original):          ${forEachTime.toFixed(2)}ms`);
console.log(`Option 2 - Cached alternation (current):      ${cachedTime.toFixed(2)}ms`);
console.log(`Option 3 - Simple pattern with map lookup:    ${simpleTime.toFixed(2)}ms`);
console.log();
console.log('Comparison to original (Option 1):');
console.log(`  Option 2: \x1b[32m${cachedVsForEach}% faster (${cachedSpeedup}x speedup)\x1b[0m`);
console.log(`  Option 3: \x1b[32m${simpleVsForEach}% faster (${simpleSpeedup}x speedup)\x1b[0m`);
console.log();
console.log('Comparison between optimized versions:');
if (cachedTime < simpleTime) {
  console.log(`  Option 2 is \x1b[32m${cachedVsSimple}% faster than Option 3 (${cachedVsSimpleSpeedup}x)\x1b[0m`);
} else {
  const simpleVsCached = (((cachedTime - simpleTime) / cachedTime) * 100).toFixed(2);
  const simpleVsCachedSpeedup = (cachedTime / simpleTime).toFixed(2);
  console.log(`  Option 3 is \x1b[32m${simpleVsCached}% faster than Option 2 (${simpleVsCachedSpeedup}x)\x1b[0m`);
}

// 3. Code complexity comparison
console.log('\n3. Code complexity analysis:\n');

console.log('Option 1 (forEach loops):');
console.log('  - Lines of code: ~25 (including map definitions)');
console.log('  - Regex objects created: 70+ per call');
console.log('  - String.replace calls: 70+ per call');
console.log('  - Complexity: Medium (nested loops, repeated regex creation)');
console.log();

console.log('Option 2 (Cached alternation):');
console.log('  - Lines of code: ~10 (excluding initialization)');
console.log('  - Regex objects created: 1 (cached, created once)');
console.log('  - String.replace calls: 1 per call');
console.log('  - Pattern length: ~1000+ characters (all symbols joined with |)');
console.log('  - Complexity: Medium (requires sorting, complex pattern building)');
console.log();

console.log('Option 3 (Simple pattern):');
console.log('  - Lines of code: ~8 (excluding initialization)');
console.log('  - Regex objects created: 1 (cached, created once)');
console.log('  - String.replace calls: 1 per call');
console.log('  - Pattern length: 12 characters (\\{[^}]{1,5}\\})');
console.log('  - Complexity: Low (simple pattern, straightforward map lookup)');
console.log();

console.log('4. Sample outputs:');
testCases.slice(0, 3).forEach(testCase => {
  const result = convertManaSymbols_Simple(testCase);
  console.log(`  Input:  ${testCase}`);
  console.log(`  Output: ${result.substring(0, 150)}...`);
  console.log('');
});

console.log('✅ All tests completed!');
