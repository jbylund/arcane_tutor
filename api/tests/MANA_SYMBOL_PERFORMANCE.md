# Mana Symbol Replacement Performance Optimization

## Summary

The mana symbol replacement logic in `api/index.html` has been optimized from using forEach loops with repeated RegExp creation to using cached regex patterns. This resulted in a **51.63x speedup** (98.06% performance improvement).

## Problem

The original implementation had two major performance issues:

1. **Repeated RegExp Creation**: For each mana symbol in the map (70+ symbols), a new RegExp object was created on every function call
2. **Multiple String Replacements**: The `replace()` method was called multiple times (once per symbol), requiring multiple passes through the input string

### Original Implementation (forEach loops)

```javascript
convertManaSymbols(manaCost, isModal = false) {
  // ... manaMap and hybridMap definitions ...
  
  let converted = manaCost;
  
  // Process hybrid symbols first
  Object.keys(hybridMap).forEach(symbol => {
    const regex = new RegExp(symbol.replace(/[{}]/g, '\\$&'), 'g');  // RegExp created 30 times
    converted = converted.replace(regex, ...);  // 30 replace calls
  });
  
  // Process regular mana symbols
  Object.keys(manaMap).forEach(symbol => {
    const regex = new RegExp(symbol.replace(/[{}]/g, '\\$&'), 'g');  // RegExp created 40 times
    converted = converted.replace(regex, ...);  // 40 replace calls
  });
  
  return converted;
}
```

## Solution

The optimized implementation:

1. **Caches RegExp Patterns**: The regex pattern is created once during initialization and stored as an instance variable
2. **Single Pass Replacement**: All symbols are matched and replaced in a single `replace()` call using alternation (`|`)
3. **Maintains Correctness**: Symbols are sorted by length (longest first) to avoid partial matches

### Optimized Implementation (cached regex)

```javascript
// In constructor:
initManaSymbolPatterns() {
  const manaMap = { /* ... */ };
  const hybridMap = { /* ... */ };
  
  const allSymbols = { ...hybridMap, ...manaMap };
  const sortedSymbols = Object.keys(allSymbols).sort((a, b) => b.length - a.length);
  const pattern = sortedSymbols.map(s => s.replace(/[{}]/g, '\\$&')).join('|');
  
  this.manaSymbolsMap = allSymbols;
  this.manaSymbolsRegex = new RegExp(pattern, 'g');  // Created once
}

// In the method:
convertManaSymbols(manaCost, isModal = false) {
  if (!manaCost) return '';
  
  const symbolClass = isModal ? 'modal-mana-symbol' : 'mana-symbol';
  this.manaSymbolsRegex.lastIndex = 0;  // Reset regex state
  
  return manaCost.replace(this.manaSymbolsRegex, match => {  // Single replace call
    return `<span class="${symbolClass} ${this.manaSymbolsMap[match]}"></span>`;
  });
}
```

## Performance Results

Test performed with 10,000 iterations × 14 test cases (140,000 conversions total):

| Implementation | Time (ms) | Speedup |
|---------------|-----------|---------|
| Old (forEach loops) | 7,147.83 | 1.0x (baseline) |
| New (cached regex) | 138.44 | **51.63x** |

**Performance Improvement: 98.06%**

### Test Cases

The test included various mana cost combinations:
- Simple costs: `{W}{U}{B}{R}{G}`
- Repeated symbols: `{1}{R}{R}{R}`
- Variable costs: `{X}{X}{W}{U}`
- Hybrid mana: `{2}{W/U}{B/R}`
- Phyrexian mana: `{3}{W/U/P}{G}`
- Special symbols: `{T}{Q}{E}{P}{S}`
- High costs: `{16}{G}{G}{G}`

## Running the Performance Test

To run the performance test yourself:

```bash
cd api/tests
node test_mana_symbol_performance.js
```

Expected output:
```
=== Mana Symbol Replacement Test ===

1. Verifying correctness...
✅ All test cases produce identical results

2. Running performance benchmarks...

Results (10000 iterations × 14 test cases):
  Old implementation (forEach): 7147.83ms
  New implementation (single regex): 138.44ms
  Performance improvement: 98.06%
  Speedup: 51.63x faster

✅ All tests passed!
```

## Benefits

1. **Faster Page Load**: Reduced CPU time for rendering card mana costs
2. **Better UX**: Smoother scrolling and interactions when displaying many cards
3. **Reduced Energy Usage**: Less CPU cycles means better battery life on mobile devices
4. **Scalability**: Performance improvement is more pronounced with larger card lists

## Implementation Notes

- The regex pattern uses alternation (`symbol1|symbol2|...`) to match any symbol in one pass
- Symbols are sorted by length (longest first) to ensure `{W/U/P}` is matched before `{W/U}` or `{W}`
- The `lastIndex` property is reset before each use to ensure the regex with the global flag works correctly
- The same optimization was applied to both `convertManaSymbols()` and `convertManaSymbolsToText()`

## Related Files

- `api/index.html` - Main implementation
- `api/tests/test_mana_symbol_performance.js` - Performance test script
