#!/usr/bin/env node
/**
 * Benchmark: autoCompleteQuery filter+sort vs partitioned Map with early termination.
 *
 * Run with:  node scripts/bench_autocomplete.js
 */

'use strict';

// ---------------------------------------------------------------------------
// Live fixture: fetched from https://sylvan-librarian.com/get_common_card_types
// 381 types, alphabetically sorted by type_name.
// ---------------------------------------------------------------------------

const CARD_TYPES = [
  { t: 'Adventure', n: 310 },
  { t: 'Advisor', n: 406 },
  { t: 'Aetherborn', n: 62 },
  { t: 'Ajani', n: 59 },
  { t: 'Alien', n: 99 },
  { t: 'Ally', n: 398 },
  { t: 'Aminatou', n: 7 },
  { t: 'Angel', n: 1050 },
  { t: 'Angrath', n: 11 },
  { t: 'Antelope', n: 23 },
  { t: 'Ape', n: 110 },
  { t: 'Arcane', n: 184 },
  { t: 'Archer', n: 275 },
  { t: 'Archon', n: 76 },
  { t: 'Arlinn', n: 19 },
  { t: 'Artifact', n: 10947 },
  { t: 'Artificer', n: 752 },
  { t: 'Ashiok', n: 16 },
  { t: 'Assassin', n: 392 },
  { t: 'Assembly-Worker', n: 36 },
  { t: 'Astartes', n: 46 },
  { t: 'Atog', n: 24 },
  { t: 'Aura', n: 3246 },
  { t: 'Aurochs', n: 6 },
  { t: 'Avatar', n: 571 },
  { t: 'Azra', n: 16 },
  { t: 'Background', n: 70 },
  { t: 'Badger', n: 20 },
  { t: 'Bahamut', n: 6 },
  { t: 'Barbarian', n: 125 },
  { t: 'Bard', n: 127 },
  { t: 'Basic', n: 4196 },
  { t: 'Basilisk', n: 40 },
  { t: 'Basri', n: 7 },
  { t: 'Bat', n: 98 },
  { t: 'Bear', n: 147 },
  { t: 'Beast', n: 1207 },
  { t: 'Beholder', n: 16 },
  { t: 'Berserker', n: 350 },
  { t: 'Bird', n: 1042 },
  { t: 'Bison', n: 8 },
  { t: 'Boar', n: 135 },
  { t: 'Bobblehead', n: 21 },
  { t: 'Bolas', n: 30 },
  { t: 'Book', n: 169 },
  { t: 'Bringer', n: 6 },
  { t: 'Brushwagg', n: 7 },
  { t: 'Calix', n: 5 },
  { t: 'Camel', n: 9 },
  { t: 'Carrier', n: 22 },
  { t: 'Cartouche', n: 13 },
  { t: 'Case', n: 24 },
  { t: 'Cat', n: 919 },
  { t: 'Cave', n: 44 },
  { t: 'Centaur', n: 148 },
  { t: 'Chandra', n: 81 },
  { t: 'Chimera', n: 39 },
  { t: 'Citizen', n: 200 },
  { t: 'Class', n: 69 },
  { t: 'Cleric', n: 1572 },
  { t: 'Clown', n: 6 },
  { t: 'Clue', n: 42 },
  { t: 'Cockatrice', n: 16 },
  { t: 'Construct', n: 632 },
  { t: 'Crab', n: 94 },
  { t: 'Creature', n: 45967 },
  { t: 'Crocodile', n: 60 },
  { t: 'Curse', n: 102 },
  { t: 'Cyberman', n: 16 },
  { t: 'Cyclops', n: 69 },
  { t: 'Dack', n: 6 },
  { t: 'Dalek', n: 16 },
  { t: 'Daretti', n: 13 },
  { t: 'Dauthi', n: 25 },
  { t: 'Davriel', n: 6 },
  { t: 'Demigod', n: 37 },
  { t: 'Demon', n: 684 },
  { t: 'Desert', n: 103 },
  { t: 'Detective', n: 181 },
  { t: 'Devil', n: 148 },
  { t: 'Dihada', n: 6 },
  { t: 'Dinosaur', n: 623 },
  { t: 'Djinn', n: 179 },
  { t: 'Doctor', n: 120 },
  { t: 'Dog', n: 315 },
  { t: 'Domri', n: 15 },
  { t: 'Dovin', n: 10 },
  { t: 'Dragon', n: 1499 },
  { t: 'Drake', n: 249 },
  { t: 'Dreadnought', n: 7 },
  { t: 'Drix', n: 5 },
  { t: 'Drone', n: 111 },
  { t: 'Druid', n: 1140 },
  { t: 'Dryad', n: 158 },
  { t: 'Dwarf', n: 309 },
  { t: 'Efreet', n: 74 },
  { t: 'Egg', n: 28 },
  { t: 'Elder', n: 277 },
  { t: 'Eldrazi', n: 473 },
  { t: 'Elemental', n: 1772 },
  { t: 'Elephant', n: 197 },
  { t: 'Elf', n: 2096 },
  { t: 'Elk', n: 102 },
  { t: 'Ellywick', n: 5 },
  { t: 'Elspeth', n: 42 },
  { t: 'Enchantment', n: 9913 },
  { t: 'Equipment', n: 1644 },
  { t: 'Eternal', n: 5 },
  { t: 'Eye', n: 23 },
  { t: 'Faerie', n: 420 },
  { t: 'Fish', n: 124 },
  { t: 'Flagbearer', n: 5 },
  { t: 'Food', n: 33 },
  { t: 'Forest', n: 1180 },
  { t: 'Fox', n: 105 },
  { t: 'Fractal', n: 11 },
  { t: 'Freyalise', n: 8 },
  { t: 'Frog', n: 181 },
  { t: 'Fungus', n: 188 },
  { t: 'Gamma', n: 32 },
  { t: 'Gargoyle', n: 83 },
  { t: 'Garruk', n: 37 },
  { t: 'Gate', n: 180 },
  { t: 'Giant', n: 629 },
  { t: 'Gideon', n: 34 },
  { t: 'Gith', n: 5 },
  { t: 'Glimmer', n: 35 },
  { t: 'Gnome', n: 65 },
  { t: 'Goat', n: 27 },
  { t: 'Goblin', n: 1506 },
  { t: 'God', n: 277 },
  { t: 'Golem', n: 434 },
  { t: 'Gorgon', n: 51 },
  { t: 'Gremlin', n: 26 },
  { t: 'Griffin', n: 101 },
  { t: 'Grist', n: 12 },
  { t: 'Hag', n: 16 },
  { t: 'Halfling', n: 162 },
  { t: 'Harpy', n: 20 },
  { t: 'Hellion', n: 45 },
  { t: 'Hero', n: 491 },
  { t: 'Hippo', n: 21 },
  { t: 'Hippogriff', n: 17 },
  { t: 'Homarid', n: 16 },
  { t: 'Homunculus', n: 72 },
  { t: 'Horror', n: 934 },
  { t: 'Horse', n: 142 },
  { t: 'Huatli', n: 11 },
  { t: 'Human', n: 10567 },
  { t: 'Hydra', n: 234 },
  { t: 'Hyena', n: 7 },
  { t: 'Illusion', n: 258 },
  { t: 'Imp', n: 131 },
  { t: 'Incarnation', n: 150 },
  { t: 'Infinity', n: 7 },
  { t: 'Inhuman', n: 12 },
  { t: 'Inquisitor', n: 6 },
  { t: 'Insect', n: 608 },
  { t: 'Instant', n: 10724 },
  { t: 'Island', n: 1127 },
  { t: 'Jace', n: 76 },
  { t: 'Jackal', n: 55 },
  { t: 'Jaya', n: 15 },
  { t: 'Jellyfish', n: 74 },
  { t: 'Juggernaut', n: 68 },
  { t: 'Kaito', n: 24 },
  { t: 'Karn', n: 29 },
  { t: 'Kasmina', n: 11 },
  { t: 'Kavu', n: 111 },
  { t: 'Kaya', n: 34 },
  { t: 'Kindred', n: 183 },
  { t: 'Kiora', n: 11 },
  { t: 'Kirin', n: 23 },
  { t: 'Kithkin', n: 156 },
  { t: 'Knight', n: 1329 },
  { t: 'Kobold', n: 26 },
  { t: 'Kor', n: 207 },
  { t: 'Koth', n: 9 },
  { t: 'Kraken', n: 118 },
  { t: 'Kree', n: 14 },
  { t: 'Lair', n: 12 },
  { t: 'Lamia', n: 7 },
  { t: 'Land', n: 11551 },
  { t: 'Leech', n: 32 },
  { t: 'Legendary', n: 13532 },
  { t: 'Lemur', n: 7 },
  { t: 'Lesson', n: 120 },
  { t: 'Leviathan', n: 92 },
  { t: 'Lhurgoyf', n: 67 },
  { t: 'Licid', n: 14 },
  { t: 'Liliana', n: 84 },
  { t: 'Lizard', n: 339 },
  { t: 'Locus', n: 8 },
  { t: 'Lolth', n: 6 },
  { t: 'Lord', n: 168 },
  { t: 'Lukka', n: 17 },
  { t: 'Manticore', n: 22 },
  { t: 'Masticore', n: 22 },
  { t: 'Mercenary', n: 195 },
  { t: 'Merfolk', n: 796 },
  { t: 'Metathran', n: 14 },
  { t: 'Mine', n: 26 },
  { t: 'Minion', n: 112 },
  { t: 'Minotaur', n: 210 },
  { t: 'Minsc', n: 7 },
  { t: 'Mite', n: 7 },
  { t: 'Mole', n: 21 },
  { t: 'Monger', n: 7 },
  { t: 'Mongoose', n: 8 },
  { t: 'Monk', n: 364 },
  { t: 'Monkey', n: 40 },
  { t: 'Moogle', n: 16 },
  { t: 'Moonfolk', n: 54 },
  { t: 'Mordenkainen', n: 5 },
  { t: 'Mount', n: 68 },
  { t: 'Mountain', n: 1139 },
  { t: 'Mouse', n: 45 },
  { t: 'Mutant', n: 464 },
  { t: 'Myr', n: 127 },
  { t: 'Mystic', n: 11 },
  { t: 'Nahiri', n: 27 },
  { t: 'Narset', n: 18 },
  { t: 'Necron', n: 48 },
  { t: 'Nephilim', n: 10 },
  { t: 'Nightmare', n: 239 },
  { t: 'Nightstalker', n: 22 },
  { t: 'Ninja', n: 317 },
  { t: 'Nissa', n: 50 },
  { t: 'Nixilis', n: 25 },
  { t: 'Noble', n: 584 },
  { t: 'Noggle', n: 9 },
  { t: 'Nomad', n: 70 },
  { t: 'Nymph', n: 58 },
  { t: 'Octopus', n: 100 },
  { t: 'Ogre', n: 284 },
  { t: 'Oko', n: 15 },
  { t: 'Omen', n: 32 },
  { t: 'Ooze', n: 188 },
  { t: 'Orc', n: 269 },
  { t: 'Orgg', n: 10 },
  { t: 'Otter', n: 51 },
  { t: 'Ouphe', n: 35 },
  { t: 'Ox', n: 55 },
  { t: 'Pangolin', n: 7 },
  { t: 'Peasant', n: 88 },
  { t: 'Pegasus', n: 73 },
  { t: 'Performer', n: 17 },
  { t: 'Pest', n: 11 },
  { t: 'Phelddagrif', n: 6 },
  { t: 'Phoenix', n: 105 },
  { t: 'Phyrexian', n: 1234 },
  { t: 'Pilot', n: 91 },
  { t: 'Pirate', n: 456 },
  { t: 'Plains', n: 1109 },
  { t: 'Plan', n: 12 },
  { t: 'Planeswalker', n: 1379 },
  { t: 'Planet', n: 25 },
  { t: 'Plant', n: 238 },
  { t: 'Possum', n: 6 },
  { t: 'Power-Plant', n: 26 },
  { t: 'Praetor', n: 101 },
  { t: 'Processor', n: 16 },
  { t: 'Quintorius', n: 6 },
  { t: 'Rabbit', n: 78 },
  { t: 'Raccoon', n: 39 },
  { t: 'Ral', n: 28 },
  { t: 'Ranger', n: 176 },
  { t: 'Rat', n: 337 },
  { t: 'Rebel', n: 161 },
  { t: 'Rhino', n: 135 },
  { t: 'Robot', n: 198 },
  { t: 'Rogue', n: 1367 },
  { t: 'Room', n: 59 },
  { t: 'Rowan', n: 12 },
  { t: 'Rune', n: 5 },
  { t: 'Saga', n: 439 },
  { t: 'Saheeli', n: 18 },
  { t: 'Salamander', n: 37 },
  { t: 'Samurai', n: 153 },
  { t: 'Samut', n: 7 },
  { t: 'Sarkhan', n: 23 },
  { t: 'Satyr', n: 70 },
  { t: 'Scarecrow', n: 81 },
  { t: 'Scientist', n: 123 },
  { t: 'Scorpion', n: 39 },
  { t: 'Scout', n: 675 },
  { t: 'Seal', n: 5 },
  { t: 'Serpent', n: 146 },
  { t: 'Shade', n: 84 },
  { t: 'Shaman', n: 1420 },
  { t: 'Shapeshifter', n: 486 },
  { t: 'Shark', n: 45 },
  { t: 'Sheep', n: 15 },
  { t: 'Shrine', n: 35 },
  { t: 'Siren', n: 46 },
  { t: 'Skeleton', n: 245 },
  { t: 'Skrull', n: 5 },
  { t: 'Slith', n: 17 },
  { t: 'Sliver', n: 328 },
  { t: 'Sloth', n: 13 },
  { t: 'Slug', n: 22 },
  { t: 'Snake', n: 481 },
  { t: 'Snow', n: 262 },
  { t: 'Soldier', n: 2327 },
  { t: 'Soltari', n: 22 },
  { t: 'Sorcerer', n: 127 },
  { t: 'Sorcery', n: 10624 },
  { t: 'Sorin', n: 38 },
  { t: 'Spacecraft', n: 58 },
  { t: 'Specter', n: 92 },
  { t: 'Spellshaper', n: 94 },
  { t: 'Sphere', n: 23 },
  { t: 'Sphinx', n: 258 },
  { t: 'Spider', n: 376 },
  { t: 'Spike', n: 24 },
  { t: 'Spirit', n: 1694 },
  { t: 'Spy', n: 30 },
  { t: 'Squid', n: 20 },
  { t: 'Squirrel', n: 75 },
  { t: 'Starfish', n: 11 },
  { t: 'Stone', n: 7 },
  { t: 'Surrakar', n: 8 },
  { t: 'Survivor', n: 29 },
  { t: 'Swamp', n: 1129 },
  { t: 'Symbiote', n: 30 },
  { t: 'Synth', n: 14 },
  { t: 'Tamiyo', n: 27 },
  { t: 'Teferi', n: 46 },
  { t: 'Teyo', n: 7 },
  { t: 'Tezzeret', n: 30 },
  { t: 'Thalakos', n: 7 },
  { t: 'Thopter', n: 91 },
  { t: 'Thrull', n: 60 },
  { t: 'Tibalt', n: 14 },
  { t: 'Tiefling', n: 35 },
  { t: 'Time', n: 168 },
  { t: 'Tower', n: 26 },
  { t: 'Town', n: 20 },
  { t: 'Toy', n: 24 },
  { t: 'Trap', n: 43 },
  { t: 'Treasure', n: 12 },
  { t: 'Treefolk', n: 279 },
  { t: 'Trilobite', n: 7 },
  { t: 'Troll', n: 147 },
  { t: 'Turtle', n: 213 },
  { t: 'Tyranid', n: 76 },
  { t: 'Tyvar', n: 11 },
  { t: 'Ugin', n: 24 },
  { t: 'Unicorn', n: 97 },
  { t: "Urza'S", n: 95 },
  { t: 'Utrom', n: 13 },
  { t: 'Vampire', n: 1301 },
  { t: 'Vedalken', n: 175 },
  { t: 'Vehicle', n: 500 },
  { t: 'Villain', n: 321 },
  { t: 'Vivien', n: 31 },
  { t: 'Volver', n: 6 },
  { t: 'Vraska', n: 28 },
  { t: 'Wall', n: 514 },
  { t: 'Warlock', n: 430 },
  { t: 'Warrior', n: 2907 },
  { t: 'Weasel', n: 5 },
  { t: 'Weird', n: 31 },
  { t: 'Werewolf', n: 189 },
  { t: 'Whale', n: 34 },
  { t: 'Will', n: 14 },
  { t: 'Wizard', n: 3107 },
  { t: 'Wolf', n: 220 },
  { t: 'Wolverine', n: 14 },
  { t: 'Wombat', n: 5 },
  { t: 'World', n: 42 },
  { t: 'Worm', n: 26 },
  { t: 'Wraith', n: 79 },
  { t: 'Wrenn', n: 18 },
  { t: 'Wurm', n: 290 },
  { t: 'Yanggu', n: 7 },
  { t: 'Yanling', n: 6 },
  { t: 'Yeti', n: 29 },
  { t: 'Zariel', n: 5 },
  { t: 'Zombie', n: 1649 },
  { t: 'Zubera', n: 7 },
];

// ---------------------------------------------------------------------------
// Old implementation: filter + sort on every call
// ---------------------------------------------------------------------------

function filterSortMatch(types, prefix) {
  return types.filter(type => type.t.toLowerCase().startsWith(prefix)).sort((a, b) => b.n - a.n)[0] ?? null;
}

// ---------------------------------------------------------------------------
// New implementation: build once, look up in O(1) + early-termination scan
// ---------------------------------------------------------------------------

function buildTypeMap(types) {
  const map = new Map();
  for (const type of types) {
    const tl = type.t.toLowerCase();
    const letter = tl[0];
    if (!map.has(letter)) map.set(letter, []);
    map.get(letter).push({ n: type.n, tl });
  }
  for (const bucket of map.values()) {
    bucket.sort((a, b) => a.tl.localeCompare(b.tl));
  }
  return map;
}

function buildTypeMapOld(types) {
  const map = new Map();
  for (const type of types) {
    const letter = type.t[0].toLowerCase();
    if (!map.has(letter)) map.set(letter, []);
    map.get(letter).push(type);
  }
  for (const bucket of map.values()) {
    bucket.sort((a, b) => a.t.toLowerCase().localeCompare(b.t.toLowerCase()));
  }
  return map;
}

function findBestMatch(typeMap, prefix) {
  const p = prefix.toLowerCase();
  const bucket = typeMap.get(p[0]) ?? [];
  let bestMatch = null;
  for (const type of bucket) {
    if (type.tl.slice(0, p.length) > p) break;
    if (type.tl.startsWith(p)) {
      if (!bestMatch || type.n > bestMatch.n) bestMatch = type;
    }
  }
  return bestMatch;
}

function findBestMatchOld(typeMap, prefix) {
  const bucket = typeMap.get(prefix[0]) ?? [];
  let bestMatch = null;
  for (const type of bucket) {
    const typeLower = type.t.toLowerCase();
    if (typeLower.slice(0, prefix.length) > prefix) break;
    if (typeLower.startsWith(prefix)) {
      if (!bestMatch || type.n > bestMatch.n) bestMatch = type;
    }
  }
  return bestMatch;
}

// ---------------------------------------------------------------------------
// Representative prefixes (24, matching the scenario from the issue doc)
// Covers short/long, small/large buckets, matches and no-matches.
// ---------------------------------------------------------------------------

const PREFIXES = [
  // Large-bucket 's' entries (bucket has 51 types)
  'sh',
  'sk',
  'sl',
  'sn',
  'so',
  'sp',
  'sq',
  'st',
  // Common short prefixes with multiple matches
  'dr',
  'cr',
  'wa',
  'wi',
  // Single-match prefixes
  'hy',
  'zu',
  'qu',
  // Long prefixes (should terminate early)
  'shapeshifter',
  'planeswalker',
  'legendary',
  // Exact full names
  'zombie',
  'dragon',
  'wizard',
  // No-match prefixes (whole scan of bucket, no result)
  'zz',
  'xx',
  'jj',
];

// ---------------------------------------------------------------------------
// Correctness check
// ---------------------------------------------------------------------------

const typeMap = buildTypeMap(CARD_TYPES);
const typeMapOld = buildTypeMapOld(CARD_TYPES);

let allMatch = true;
for (const prefix of PREFIXES) {
  const expected = filterSortMatch(CARD_TYPES, prefix);
  for (const [label, fn, map] of [
    ['map+precomputed', findBestMatch, typeMap],
    ['map+old', findBestMatchOld, typeMapOld],
  ]) {
    const actual = fn(map, prefix);
    const actualTl = actual === null ? null : (actual.tl ?? actual.t.toLowerCase());
    const expectedTl = expected === null ? null : expected.t.toLowerCase();
    const match = actualTl === expectedTl;
    if (!match) {
      console.error(
        `MISMATCH [${label}] for "${prefix}": filter+sort=${JSON.stringify(expected?.t)} got=${JSON.stringify(actual?.t)}`
      );
      allMatch = false;
    }
  }
}
if (allMatch) {
  console.log('✓  All three implementations produce identical results for all prefixes.\n');
} else {
  console.log('✗  Output mismatch — see above.\n');
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Benchmark harness
// ---------------------------------------------------------------------------

function bench(label, fn, iterations) {
  // Warm up
  for (let i = 0; i < 1000; i++) fn(PREFIXES[i % PREFIXES.length]);

  const start = performance.now();
  for (let i = 0; i < iterations; i++) {
    fn(PREFIXES[i % PREFIXES.length]);
  }
  const elapsed = performance.now() - start;
  const usPerCall = (elapsed * 1000) / iterations;
  return { label, elapsed, usPerCall };
}

const ITERS = 100_000;

console.log(
  `Dataset: ${CARD_TYPES.length} types, ${PREFIXES.length} prefixes, ${ITERS.toLocaleString()} iterations each\n`
);

const results = [
  bench('filter+sort', prefix => filterSortMatch(CARD_TYPES, prefix), ITERS),
  bench('map (per-call tl)', prefix => findBestMatchOld(typeMapOld, prefix), ITERS),
  bench('map (precomp tl)', prefix => findBestMatch(typeMap, prefix), ITERS),
];

const baseline = results[0].usPerCall;
const col1 = 20;
const col2 = 12;
const col3 = 15;
const col4 = 10;

const header = `${'Approach'.padEnd(col1)} ${'Total (ms)'.padStart(col2)} ${'Per call (µs)'.padStart(col3)} ${'vs baseline'.padStart(col4)}`;
const sep = '-'.repeat(header.length);
console.log(header);
console.log(sep);
for (const r of results) {
  const speedup = r === results[0] ? '—' : `${(baseline / r.usPerCall).toFixed(1)}×`;
  console.log(
    `${r.label.padEnd(col1)} ${r.elapsed.toFixed(1).padStart(col2)} ${r.usPerCall.toFixed(2).padStart(col3)} ${speedup.padStart(col4)}`
  );
}

// ---------------------------------------------------------------------------
// Per-bucket stats
// ---------------------------------------------------------------------------

console.log('\nBucket sizes (first character → entry count):');
const bucketSizes = [...typeMap.entries()]
  .map(([ch, bucket]) => ({ ch, size: bucket.length }))
  .sort((a, b) => b.size - a.size);
for (const { ch, size } of bucketSizes) {
  const bar = '█'.repeat(Math.round(size / 2));
  console.log(`  ${ch}  ${String(size).padStart(3)}  ${bar}`);
}
const avg = CARD_TYPES.length / typeMap.size;
console.log(`\n  Total: ${CARD_TYPES.length} types across ${typeMap.size} buckets (avg ${avg.toFixed(1)} per bucket)`);
