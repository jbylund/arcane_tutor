#!/usr/bin/env node
/**
 * Benchmark: CatalogMap (bucket scan) vs FanoutCatalogMap (prefix trie + binary search).
 *
 * Run with:  node --expose-gc scripts/bench_catalog_map.js
 * (--expose-gc enables accurate heap measurements; omit to skip memory section)
 */

'use strict';

// ---------------------------------------------------------------------------
// Implementations (copied from api/static/app.js — browser file, not a module)
// ---------------------------------------------------------------------------

class CatalogMap {
  constructor(mapping) {
    this._map = new Map();
    for (const [v, n] of Object.entries(mapping)) {
      const letter = v[0].toLowerCase();
      if (!this._map.has(letter)) this._map.set(letter, []);
      this._map.get(letter).push({ v, n });
    }
    for (const bucket of this._map.values()) {
      bucket.sort((a, b) => a.v.localeCompare(b.v));
    }
  }

  get bool() {
    return this._map.size > 0;
  }
  get size() {
    let n = 0;
    for (const b of this._map.values()) n += b.length;
    return n;
  }

  getBestMatch(prefix) {
    const bucket = this._map.get(prefix[0]) ?? [];
    let best = null;
    for (const entry of bucket) {
      const lower = entry.v.toLowerCase();
      if (lower.slice(0, prefix.length) > prefix) break;
      if (lower.startsWith(prefix) && (!best || entry.n > best.n)) best = entry;
    }
    return best?.v ?? null;
  }
}

class FanoutCatalogMap {
  constructor(mapping) {
    this._words = Object.entries(mapping)
      .map(([v, n]) => ({ v, n, lower: v.toLowerCase() }))
      .sort((a, b) => a.lower.localeCompare(b.lower));

    this._d1 = {};
    this._d2 = {};

    const words = this._words;
    let i = 0;
    while (i < words.length) {
      const c1 = words[i].lower[0];
      const d1start = i;
      while (i < words.length && words[i].lower[0] === c1) i++;
      const d1end = i;
      this._d1[c1] = { start: d1start, end: d1end, best: this._bestInRange(d1start, d1end) };

      let j = d1start;
      while (j < d1end) {
        const c2 = words[j].lower[1] ?? '';
        const key = c1 + c2;
        const d2start = j;
        while (j < d1end && (words[j].lower[1] ?? '') === c2) j++;
        const d2end = j;
        this._d2[key] = { start: d2start, end: d2end, best: this._bestInRange(d2start, d2end) };
      }
    }
  }

  _bestInRange(start, end) {
    let best = null;
    for (let i = start; i < end; i++) {
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }

  _lowerBound(prefix, start, end) {
    let lo = start,
      hi = end;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (this._words[mid].lower < prefix) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  get bool() {
    return this._words.length > 0;
  }
  get size() {
    return this._words.length;
  }

  getBestMatch(prefix) {
    if (!prefix) return null;
    const c1 = prefix[0];
    const node1 = this._d1[c1];
    if (!node1) return null;
    if (prefix.length === 1) return node1.best;

    const key = prefix.slice(0, 2);
    const node2 = this._d2[key];
    if (!node2) return null;
    if (prefix.length === 2) return node2.best;

    const { start, end } = node2;
    const pos = this._lowerBound(prefix, start, end);
    let best = null;
    for (let i = pos; i < end; i++) {
      if (!this._words[i].lower.startsWith(prefix)) break;
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }
}

class FlatSortedMap {
  constructor(mapping) {
    this._words = Object.entries(mapping)
      .map(([v, n]) => ({ v, n, lower: v.toLowerCase() }))
      .sort((a, b) => a.lower.localeCompare(b.lower));
  }

  get bool() {
    return this._words.length > 0;
  }
  get size() {
    return this._words.length;
  }

  _lowerBound(prefix) {
    let lo = 0,
      hi = this._words.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (this._words[mid].lower < prefix) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  getBestMatch(prefix) {
    if (!prefix) return null;
    const pos = this._lowerBound(prefix);
    let best = null;
    for (let i = pos; i < this._words.length; i++) {
      if (!this._words[i].lower.startsWith(prefix)) break;
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }
}

// ci(str, pos) — returns char index 0-25 for a-z, or -1 for anything else.
function ci(str, pos) {
  if (pos >= str.length) return -1;
  const c = str.charCodeAt(pos) - 97;
  return c >= 0 && c < 26 ? c : -1;
}

const A = 26;
const A2 = 26 * 26; //    676
const A3 = 26 * 26 * 26; // 17,576

class FanoutD2Array {
  constructor(mapping) {
    this._words = Object.entries(mapping)
      .map(([v, n]) => ({ v, n, lower: v.toLowerCase() }))
      .sort((a, b) => a.lower.localeCompare(b.lower));

    this._d1s = new Int32Array(A).fill(-1);
    this._d1e = new Int32Array(A).fill(-1);
    this._d1b = new Array(A).fill(null);
    this._d2s = new Int32Array(A2).fill(-1);
    this._d2e = new Int32Array(A2).fill(-1);
    this._d2b = new Array(A2).fill(null);

    const words = this._words;
    let i = 0;
    while (i < words.length) {
      const i1 = ci(words[i].lower, 0);
      if (i1 < 0) {
        i++;
        continue;
      }
      const d1s = i;
      while (i < words.length && ci(words[i].lower, 0) === i1) i++;
      const d1e = i;
      this._d1s[i1] = d1s;
      this._d1e[i1] = d1e;
      this._d1b[i1] = this._bestInRange(d1s, d1e);

      let j = d1s;
      while (j < d1e) {
        const i2 = ci(words[j].lower, 1);
        if (i2 < 0) {
          j++;
          continue;
        }
        const d2idx = i1 * A + i2;
        const d2s = j;
        while (j < d1e && ci(words[j].lower, 1) === i2) j++;
        const d2e = j;
        this._d2s[d2idx] = d2s;
        this._d2e[d2idx] = d2e;
        this._d2b[d2idx] = this._bestInRange(d2s, d2e);
      }
    }
  }

  _bestInRange(start, end) {
    let best = null;
    for (let i = start; i < end; i++) {
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }

  _lowerBound(prefix, start, end) {
    let lo = start,
      hi = end;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (this._words[mid].lower < prefix) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  get bool() {
    return this._words.length > 0;
  }
  get size() {
    return this._words.length;
  }

  getBestMatch(prefix) {
    if (!prefix) return null;
    const i1 = ci(prefix, 0);
    if (i1 < 0 || this._d1s[i1] < 0) return null;
    if (prefix.length === 1) return this._d1b[i1];

    const i2 = ci(prefix, 1);
    if (i2 < 0) return null;
    const d2idx = i1 * A + i2;
    if (this._d2s[d2idx] < 0) return null;
    if (prefix.length === 2) return this._d2b[d2idx];

    const start = this._d2s[d2idx],
      end = this._d2e[d2idx];
    const pos = this._lowerBound(prefix, start, end);
    let best = null;
    for (let i = pos; i < end; i++) {
      if (!this._words[i].lower.startsWith(prefix)) break;
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }
}

class FanoutD3Array extends FanoutD2Array {
  constructor(mapping) {
    super(mapping);
    this._d3s = new Int32Array(A3).fill(-1);
    this._d3e = new Int32Array(A3).fill(-1);
    this._d3b = new Array(A3).fill(null);

    const words = this._words;
    for (let i1 = 0; i1 < A; i1++) {
      for (let i2 = 0; i2 < A; i2++) {
        const d2idx = i1 * A + i2;
        const d2s = this._d2s[d2idx];
        if (d2s < 0) continue;
        const d2e = this._d2e[d2idx];
        let j = d2s;
        while (j < d2e) {
          const i3 = ci(words[j].lower, 2);
          if (i3 < 0) {
            j++;
            continue;
          }
          const d3idx = i1 * A2 + i2 * A + i3;
          const d3s = j;
          while (j < d2e && ci(words[j].lower, 2) === i3) j++;
          const d3e = j;
          this._d3s[d3idx] = d3s;
          this._d3e[d3idx] = d3e;
          this._d3b[d3idx] = this._bestInRange(d3s, d3e);
        }
      }
    }
  }

  getBestMatch(prefix) {
    if (!prefix) return null;
    const i1 = ci(prefix, 0);
    if (i1 < 0 || this._d1s[i1] < 0) return null;
    if (prefix.length === 1) return this._d1b[i1];

    const i2 = ci(prefix, 1);
    if (i2 < 0) return null;
    const d2idx = i1 * A + i2;
    if (this._d2s[d2idx] < 0) return null;
    if (prefix.length === 2) return this._d2b[d2idx];

    const i3 = ci(prefix, 2);
    if (i3 < 0) return null;
    const d3idx = i1 * A2 + i2 * A + i3;
    if (this._d3s[d3idx] < 0) return null;
    if (prefix.length === 3) return this._d3b[d3idx];

    const start = this._d3s[d3idx],
      end = this._d3e[d3idx];
    const pos = this._lowerBound(prefix, start, end);
    let best = null;
    for (let i = pos; i < end; i++) {
      if (!this._words[i].lower.startsWith(prefix)) break;
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }
}

class SparseTrieD3 {
  constructor(mapping) {
    this._words = Object.entries(mapping)
      .map(([v, n]) => ({ v, n, lower: v.toLowerCase() }))
      .sort((a, b) => a.lower.localeCompare(b.lower));

    // Sparse object trie: only allocates nodes that exist in the data.
    // Each node stores just the best word string — no start/end indices.
    // Sort by frequency descending so first-write-wins gives the best word.
    this._d1 = {};
    this._d2 = {};
    this._d3 = {};
    const byFreq = [...this._words].sort((a, b) => b.n - a.n);
    for (const w of byFreq) {
      const l = w.lower;
      if (l.length >= 1 && !(l[0] in this._d1)) this._d1[l[0]] = w.v;
      if (l.length >= 2 && !(l[0] + l[1] in this._d2)) this._d2[l[0] + l[1]] = w.v;
      if (l.length >= 3 && !(l[0] + l[1] + l[2] in this._d3)) this._d3[l[0] + l[1] + l[2]] = w.v;
    }
  }

  _lowerBound(prefix) {
    let lo = 0,
      hi = this._words.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (this._words[mid].lower < prefix) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  get bool() {
    return this._words.length > 0;
  }
  get size() {
    return this._words.length;
  }

  getBestMatch(prefix) {
    if (!prefix) return null;
    if (prefix.length === 1) return this._d1[prefix] ?? null;
    if (prefix.length === 2) return this._d2[prefix] ?? null;
    if (prefix.length === 3) return this._d3[prefix] ?? null;

    // Length 4+: binary search the full sorted array.
    const pos = this._lowerBound(prefix);
    let best = null;
    for (let i = pos; i < this._words.length; i++) {
      if (!this._words[i].lower.startsWith(prefix)) break;
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }
}

// Flat map from prefix string → best answer, for all prefixes up to maxDepth.
// O(1) lookup for short prefixes; binary search fallback for longer ones.
class PrefixMap {
  constructor(mapping, maxDepth = 5) {
    this._maxDepth = maxDepth;
    this._words = Object.entries(mapping)
      .map(([v, n]) => ({ v, n, lower: v.toLowerCase() }))
      .sort((a, b) => a.lower.localeCompare(b.lower));
    this._map = {};
    const byFreq = [...this._words].sort((a, b) => b.n - a.n);
    for (const w of byFreq) {
      const l = w.lower;
      for (let d = 1; d <= maxDepth && d <= l.length; d++) {
        const key = l.slice(0, d);
        if (!(key in this._map)) this._map[key] = w.v;
      }
    }
  }

  _lowerBound(prefix) {
    let lo = 0,
      hi = this._words.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (this._words[mid].lower < prefix) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  get bool() {
    return this._words.length > 0;
  }
  get size() {
    return this._words.length;
  }

  getBestMatch(prefix) {
    if (!prefix) return null;
    if (prefix.length <= this._maxDepth) return this._map[prefix] ?? null;
    const pos = this._lowerBound(prefix);
    let best = null;
    for (let i = pos; i < this._words.length; i++) {
      if (!this._words[i].lower.startsWith(prefix)) break;
      if (!best || this._words[i].n > best.n) best = this._words[i];
    }
    return best?.v ?? null;
  }
}

class FanoutCatalogMapScan extends FanoutCatalogMap {
  getBestMatch(prefix) {
    if (!prefix) return null;
    const c1 = prefix[0];
    const node1 = this._d1[c1];
    if (!node1) return null;
    if (prefix.length === 1) return node1.best;

    const key = prefix.slice(0, 2);
    const node2 = this._d2[key];
    if (!node2) return null;
    if (prefix.length === 2) return node2.best;

    // Skip binary search — scan the full depth-2 range directly.
    const { start, end } = node2;
    let best = null;
    for (let i = start; i < end; i++) {
      const w = this._words[i];
      if (w.lower > prefix && !w.lower.startsWith(prefix)) break;
      if (w.lower.startsWith(prefix) && (!best || w.n > best.n)) best = w;
    }
    return best?.v ?? null;
  }
}

// ---------------------------------------------------------------------------
// Real fixture — 381 types from https://sylvan-librarian.com/get_common_card_types
// Mapping shape: { TypeName: count }
// ---------------------------------------------------------------------------

const REAL_MAPPING = {
  Adventure: 310,
  Advisor: 406,
  Aetherborn: 62,
  Ajani: 59,
  Alien: 99,
  Ally: 398,
  Aminatou: 7,
  Angel: 1050,
  Angrath: 11,
  Antelope: 23,
  Ape: 110,
  Arcane: 184,
  Archer: 275,
  Archon: 76,
  Arlinn: 19,
  Artifact: 10947,
  Artificer: 752,
  Ashiok: 16,
  Assassin: 392,
  'Assembly-Worker': 36,
  Astartes: 46,
  Atog: 24,
  Aura: 3246,
  Aurochs: 6,
  Avatar: 571,
  Azra: 16,
  Background: 70,
  Badger: 20,
  Bahamut: 6,
  Barbarian: 125,
  Bard: 127,
  Basic: 4196,
  Basilisk: 40,
  Basri: 7,
  Bat: 98,
  Bear: 147,
  Beast: 1207,
  Beholder: 16,
  Berserker: 350,
  Bird: 1042,
  Bison: 8,
  Boar: 135,
  Bobblehead: 21,
  Bolas: 30,
  Book: 169,
  Bringer: 6,
  Brushwagg: 7,
  Calix: 5,
  Camel: 9,
  Carrier: 22,
  Cartouche: 13,
  Case: 24,
  Cat: 919,
  Cave: 44,
  Centaur: 148,
  Chandra: 81,
  Chimera: 39,
  Citizen: 200,
  Class: 69,
  Cleric: 1572,
  Clown: 6,
  Clue: 42,
  Cockatrice: 16,
  Construct: 632,
  Crab: 94,
  Creature: 45967,
  Crocodile: 60,
  Curse: 102,
  Cyberman: 16,
  Cyclops: 69,
  Dack: 6,
  Dalek: 16,
  Daretti: 13,
  Dauthi: 25,
  Davriel: 6,
  Demigod: 37,
  Demon: 684,
  Desert: 103,
  Detective: 181,
  Devil: 148,
  Dihada: 6,
  Dinosaur: 623,
  Djinn: 179,
  Doctor: 120,
  Dog: 315,
  Domri: 15,
  Dovin: 10,
  Dragon: 1499,
  Drake: 249,
  Dreadnought: 7,
  Drix: 5,
  Drone: 111,
  Druid: 1140,
  Dryad: 158,
  Dwarf: 309,
  Efreet: 74,
  Egg: 28,
  Elder: 277,
  Eldrazi: 473,
  Elemental: 1772,
  Elephant: 197,
  Elf: 2096,
  Elk: 102,
  Ellywick: 5,
  Elspeth: 42,
  Enchantment: 9913,
  Equipment: 1644,
  Eternal: 5,
  Eye: 23,
  Faerie: 420,
  Fish: 124,
  Flagbearer: 5,
  Food: 33,
  Forest: 1180,
  Fox: 105,
  Fractal: 11,
  Freyalise: 8,
  Frog: 181,
  Fungus: 188,
  Gamma: 32,
  Gargoyle: 83,
  Garruk: 37,
  Gate: 180,
  Giant: 629,
  Gideon: 34,
  Gith: 5,
  Glimmer: 35,
  Gnome: 65,
  Goat: 27,
  Goblin: 1506,
  God: 277,
  Golem: 434,
  Gorgon: 51,
  Gremlin: 26,
  Griffin: 101,
  Grist: 12,
  Hag: 16,
  Halfling: 162,
  Harpy: 20,
  Hellion: 45,
  Hero: 491,
  Hippo: 21,
  Hippogriff: 17,
  Homarid: 16,
  Homunculus: 72,
  Horror: 934,
  Horse: 142,
  Huatli: 11,
  Human: 10567,
  Hydra: 234,
  Hyena: 7,
  Illusion: 258,
  Imp: 131,
  Incarnation: 150,
  Infinity: 7,
  Inhuman: 12,
  Inquisitor: 6,
  Insect: 608,
  Instant: 10724,
  Island: 1127,
  Jace: 76,
  Jackal: 55,
  Jaya: 15,
  Jellyfish: 74,
  Juggernaut: 68,
  Kaito: 24,
  Karn: 29,
  Kasmina: 11,
  Kavu: 111,
  Kaya: 34,
  Kindred: 183,
  Kiora: 11,
  Kirin: 23,
  Kithkin: 156,
  Knight: 1329,
  Kobold: 26,
  Kor: 207,
  Koth: 9,
  Kraken: 118,
  Kree: 14,
  Lair: 12,
  Lamia: 7,
  Land: 11551,
  Leech: 32,
  Legendary: 13532,
  Lemur: 7,
  Lesson: 120,
  Leviathan: 92,
  Lhurgoyf: 67,
  Licid: 14,
  Liliana: 84,
  Lizard: 339,
  Locus: 8,
  Lolth: 6,
  Lord: 168,
  Lukka: 17,
  Manticore: 22,
  Masticore: 22,
  Mercenary: 195,
  Merfolk: 796,
  Metathran: 14,
  Mine: 26,
  Minion: 112,
  Minotaur: 210,
  Minsc: 7,
  Mite: 7,
  Mole: 21,
  Monger: 7,
  Mongoose: 8,
  Monk: 364,
  Monkey: 40,
  Moogle: 16,
  Moonfolk: 54,
  Mordenkainen: 5,
  Mount: 68,
  Mountain: 1139,
  Mouse: 45,
  Mutant: 464,
  Myr: 127,
  Mystic: 11,
  Nahiri: 27,
  Narset: 18,
  Necron: 48,
  Nephilim: 10,
  Nightmare: 239,
  Nightstalker: 22,
  Ninja: 317,
  Nissa: 50,
  Nixilis: 25,
  Noble: 584,
  Noggle: 9,
  Nomad: 70,
  Nymph: 58,
  Octopus: 100,
  Ogre: 284,
  Oko: 15,
  Omen: 32,
  Ooze: 188,
  Orc: 269,
  Orgg: 10,
  Otter: 51,
  Ouphe: 35,
  Ox: 55,
  Pangolin: 7,
  Peasant: 88,
  Pegasus: 73,
  Performer: 17,
  Pest: 11,
  Phelddagrif: 6,
  Phoenix: 105,
  Phyrexian: 1234,
  Pilot: 91,
  Pirate: 456,
  Plains: 1109,
  Plan: 12,
  Planeswalker: 1379,
  Planet: 25,
  Plant: 238,
  Possum: 6,
  'Power-Plant': 26,
  Praetor: 101,
  Processor: 16,
  Quintorius: 6,
  Rabbit: 78,
  Raccoon: 39,
  Ral: 28,
  Ranger: 176,
  Rat: 337,
  Rebel: 161,
  Rhino: 135,
  Robot: 198,
  Rogue: 1367,
  Room: 59,
  Rowan: 12,
  Rune: 5,
  Saga: 439,
  Saheeli: 18,
  Salamander: 37,
  Samurai: 153,
  Samut: 7,
  Sarkhan: 23,
  Satyr: 70,
  Scarecrow: 81,
  Scientist: 123,
  Scorpion: 39,
  Scout: 675,
  Seal: 5,
  Serpent: 146,
  Shade: 84,
  Shaman: 1420,
  Shapeshifter: 486,
  Shark: 45,
  Sheep: 15,
  Shrine: 35,
  Siren: 46,
  Skeleton: 245,
  Skrull: 5,
  Slith: 17,
  Sliver: 328,
  Sloth: 13,
  Slug: 22,
  Snake: 481,
  Snow: 262,
  Soldier: 2327,
  Soltari: 22,
  Sorcerer: 127,
  Sorcery: 10624,
  Sorin: 38,
  Spacecraft: 58,
  Specter: 92,
  Spellshaper: 94,
  Sphere: 23,
  Sphinx: 258,
  Spider: 376,
  Spike: 24,
  Spirit: 1694,
  Spy: 30,
  Squid: 20,
  Squirrel: 75,
  Starfish: 11,
  Stone: 7,
  Surrakar: 8,
  Survivor: 29,
  Swamp: 1129,
  Symbiote: 30,
  Synth: 14,
  Tamiyo: 27,
  Teferi: 46,
  Teyo: 7,
  Tezzeret: 30,
  Thalakos: 7,
  Thopter: 91,
  Thrull: 60,
  Tibalt: 14,
  Tiefling: 35,
  Time: 168,
  Tower: 26,
  Town: 20,
  Toy: 24,
  Trap: 43,
  Treasure: 12,
  Treefolk: 279,
  Trilobite: 7,
  Troll: 147,
  Turtle: 213,
  Tyranid: 76,
  Tyvar: 11,
  Ugin: 24,
  Unicorn: 97,
  "Urza'S": 95,
  Utrom: 13,
  Vampire: 1301,
  Vedalken: 175,
  Vehicle: 500,
  Villain: 321,
  Vivien: 31,
  Volver: 6,
  Vraska: 28,
  Wall: 514,
  Warlock: 430,
  Warrior: 2907,
  Weasel: 5,
  Weird: 31,
  Werewolf: 189,
  Whale: 34,
  Will: 14,
  Wizard: 3107,
  Wolf: 220,
  Wolverine: 14,
  Wombat: 5,
  World: 42,
  Worm: 26,
  Wraith: 79,
  Wrenn: 18,
  Wurm: 290,
  Yanggu: 7,
  Yanling: 6,
  Yeti: 29,
  Zariel: 5,
  Zombie: 1649,
  Zubera: 7,
};

// ---------------------------------------------------------------------------
// Synthetic dataset generator — scales the real dataset by repeating it with
// modified names to produce N unique entries with realistic frequency skew.
// ---------------------------------------------------------------------------

function syntheticMapping(n) {
  const base = Object.entries(REAL_MAPPING);
  const result = {};
  const suffixes = 'abcdefghijklmnopqrstuvwxyz';
  let count = 0;
  for (let i = 0; count < n; i++) {
    const [t, freq] = base[i % base.length];
    const suffix = suffixes[Math.floor(i / base.length) % 26] + suffixes[Math.floor(i / (base.length * 26)) % 26];
    const word = t + suffix;
    if (!(word in result)) count++;
    result[word] = Math.max(1, freq + i);
  }
  return result;
}

// ---------------------------------------------------------------------------
// Prefixes — cover length 1 (O(1) for FanoutCatalogMap), length 2 (O(1)),
// length 3+ (binary search path), and no-match cases.
// ---------------------------------------------------------------------------

const PREFIXES = [
  // Length 1 — O(1) in FanoutCatalogMap, full bucket scan in CatalogMap
  's',
  'c',
  'w',
  // Length 2 — O(1) in FanoutCatalogMap, partial bucket scan in CatalogMap
  'sh',
  'sk',
  'sl',
  'sn',
  'so',
  'sp',
  'st',
  'dr',
  'cr',
  'wa',
  'wi',
  // Length 3+ — binary search path in FanoutCatalogMap
  'sha',
  'spi',
  'war',
  'cre',
  'wiz',
  // Long / exact
  'shapeshifter',
  'planeswalker',
  'legendary',
  'zombie',
  'dragon',
  // No-match
  'zz',
  'xx',
  'jj',
];

const PREFIX_GROUPS = [
  { label: 'length 1  (trie stored best)', prefixes: ['s', 'c', 'w'] },
  {
    label: 'length 2  (trie stored best)',
    prefixes: ['sh', 'sk', 'sl', 'sn', 'so', 'sp', 'st', 'dr', 'cr', 'wa', 'wi'],
  },
  { label: 'length 3  (binary search path)', prefixes: ['sha', 'spi', 'war', 'cre', 'wiz'] },
  { label: 'length 6+ (exact/long)', prefixes: ['shapeshifter', 'planeswalker', 'legendary', 'zombie', 'dragon'] },
  { label: 'no match', prefixes: ['zz', 'xx', 'jj'] },
];

// ---------------------------------------------------------------------------
// Correctness check (real dataset only, filter+sort as ground truth)
// ---------------------------------------------------------------------------

function filterSortMatch(mapping, prefix) {
  let best = null;
  for (const [v, n] of Object.entries(mapping)) {
    if (v.toLowerCase().startsWith(prefix) && (!best || n > best.n)) best = { v, n };
  }
  return best?.v ?? null;
}

const catReal = new CatalogMap(REAL_MAPPING);
const fanReal = new FanoutCatalogMap(REAL_MAPPING);
const fanScanReal = new FanoutCatalogMapScan(REAL_MAPPING);
const flatReal = new FlatSortedMap(REAL_MAPPING);
const fd2Real = new FanoutD2Array(REAL_MAPPING);
const fd3Real = new FanoutD3Array(REAL_MAPPING);
const st3Real = new SparseTrieD3(REAL_MAPPING);

let allMatch = true;
for (const prefix of PREFIXES) {
  const expected = filterSortMatch(REAL_MAPPING, prefix);
  for (const [label, actual] of [
    ['CatalogMap', catReal.getBestMatch(prefix)],
    ['FanoutCatalogMap', fanReal.getBestMatch(prefix)],
    ['FanoutCatalogMapScan', fanScanReal.getBestMatch(prefix)],
    ['FlatSortedMap', flatReal.getBestMatch(prefix)],
    ['FanoutD2Array', fd2Real.getBestMatch(prefix)],
    ['FanoutD3Array', fd3Real.getBestMatch(prefix)],
    ['SparseTrieD3', st3Real.getBestMatch(prefix)],
  ]) {
    const match = (actual?.toLowerCase() ?? null) === (expected?.toLowerCase() ?? null);
    if (!match) {
      console.error(
        `MISMATCH [${label}] prefix="${prefix}": expected=${JSON.stringify(expected)} got=${JSON.stringify(actual)}`
      );
      allMatch = false;
    }
  }
}
if (allMatch) {
  console.log('✓  All seven implementations produce identical results for all prefixes.\n');
} else {
  console.log('✗  Output mismatch — see above.\n');
  process.exit(1);
}

// Count unique prefix nodes at each depth for a given mapping.
function prefixNodeCounts(mapping, maxDepth = 6) {
  const counts = new Array(maxDepth).fill(0);
  const seen = Array.from({ length: maxDepth }, () => new Set());
  for (const v of Object.keys(mapping)) {
    const lower = v.toLowerCase();
    for (let d = 0; d < maxDepth && d < lower.length; d++) {
      seen[d].add(lower.slice(0, d + 1));
    }
  }
  for (let d = 0; d < maxDepth; d++) counts[d] = seen[d].size;
  return counts;
}

// ---------------------------------------------------------------------------
// Benchmark harness
// ---------------------------------------------------------------------------

function bench(label, fn, iters, prefixes = PREFIXES) {
  for (let i = 0; i < 500; i++) fn(prefixes[i % prefixes.length]);
  const start = performance.now();
  for (let i = 0; i < iters; i++) fn(prefixes[i % prefixes.length]);
  const elapsed = performance.now() - start;
  return { label, elapsed, nsPerCall: (elapsed * 1e6) / iters };
}

function benchConstruct(label, fn, iters) {
  fn(); // warm up
  const start = performance.now();
  for (let i = 0; i < iters; i++) fn();
  const elapsed = performance.now() - start;
  return { label, elapsed, usPerCall: (elapsed * 1000) / iters };
}

// Iterations scale inversely with dataset size to keep total runtime reasonable.
function lookupIters(size) {
  if (size <= 1_000) return 500_000;
  if (size <= 10_000) return 50_000;
  return 5_000;
}
function constructIters(size) {
  if (size <= 1_000) return 1_000;
  if (size <= 10_000) return 100;
  return 10;
}

const col = [28, 14, 16, 12];
const header = `${''.padEnd(col[0])} ${'Total (ms)'.padStart(col[1])} ${'Per call (ns)'.padStart(col[2])} ${'vs CatalogMap'.padStart(col[3])}`;
const sep = '-'.repeat(header.length);

function printResults(results) {
  const baseline = results[0].nsPerCall ?? results[0].usPerCall;
  console.log(header);
  console.log(sep);
  for (const r of results) {
    const val = r.nsPerCall ?? r.usPerCall;
    const speedup = r === results[0] ? '—' : `${(baseline / val).toFixed(2)}×`;
    const unit = r.nsPerCall != null ? r.nsPerCall.toFixed(1) : r.usPerCall.toFixed(1);
    console.log(
      `${r.label.padEnd(col[0])} ${r.elapsed.toFixed(1).padStart(col[1])} ${unit.padStart(col[2])} ${speedup.padStart(col[3])}`
    );
  }
  console.log();
}

// ---------------------------------------------------------------------------
// Run at multiple scales
// ---------------------------------------------------------------------------

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

// Returns exact byte sizes of each internal typed array/array in a structure,
// using .byteLength for Int32Arrays and length*8 (pointer size) for JS Arrays.
function trieBytes(instance) {
  let total = 0;
  for (const key of Object.keys(instance)) {
    const v = instance[key];
    if (v instanceof Int32Array) total += v.byteLength;
    else if (Array.isArray(v)) total += v.length * 8; // 8-byte pointer per slot
  }
  // Exclude _words — shared cost across all implementations.
  const wordsBytes = instance._words ? instance._words.length * 8 : 0;
  return total - wordsBytes;
}

const scales = [
  { name: 'real (381 entries)', mapping: REAL_MAPPING },
  { name: '10K synthetic', mapping: syntheticMapping(10_000) },
  { name: '100K synthetic', mapping: syntheticMapping(100_000) },
];

for (const { name, mapping } of scales) {
  const cat = new CatalogMap(mapping);
  const fan = new FanoutCatalogMap(mapping);
  const fanScan = new FanoutCatalogMapScan(mapping);
  const size = Object.keys(mapping).length;

  console.log(`=== ${name} (${size.toLocaleString()} entries) ===`);

  const flat = new FlatSortedMap(mapping);
  const fd2 = new FanoutD2Array(mapping);
  const fd3 = new FanoutD3Array(mapping);
  const st3 = new SparseTrieD3(mapping);
  const pm3 = new PrefixMap(mapping, 3);
  const pm5 = new PrefixMap(mapping, 5);
  const liters = lookupIters(size);

  console.log(`Lookup — all prefixes mixed (${liters.toLocaleString()} iters, ${PREFIXES.length} prefixes):`);
  printResults([
    bench('  FanoutCatalogMap (obj d2)', prefix => fan.getBestMatch(prefix), liters),
    bench('  FanoutD2Array (arr d2)', prefix => fd2.getBestMatch(prefix), liters),
    bench('  FanoutD3Array (arr d3)', prefix => fd3.getBestMatch(prefix), liters),
    bench('  SparseTrieD3', prefix => st3.getBestMatch(prefix), liters),
    bench('  PrefixMap (d3)', prefix => pm3.getBestMatch(prefix), liters),
    bench('  PrefixMap (d5)', prefix => pm5.getBestMatch(prefix), liters),
    bench('  FlatSortedMap (baseline)', prefix => flat.getBestMatch(prefix), liters),
  ]);

  console.log(`Lookup — by prefix length (${liters.toLocaleString()} iters each):`);
  for (const { label, prefixes } of PREFIX_GROUPS) {
    console.log(`  [${label}]`);
    printResults([
      bench('    FanoutCatalogMap (obj d2)', prefix => fan.getBestMatch(prefix), liters, prefixes),
      bench('    FanoutD2Array (arr d2)', prefix => fd2.getBestMatch(prefix), liters, prefixes),
      bench('    FanoutD3Array (arr d3)', prefix => fd3.getBestMatch(prefix), liters, prefixes),
      bench('    SparseTrieD3', prefix => st3.getBestMatch(prefix), liters, prefixes),
      bench('    PrefixMap (d3)', prefix => pm3.getBestMatch(prefix), liters, prefixes),
      bench('    PrefixMap (d5)', prefix => pm5.getBestMatch(prefix), liters, prefixes),
      bench('    FlatSortedMap (baseline)', prefix => flat.getBestMatch(prefix), liters, prefixes),
    ]);
  }

  const citers = constructIters(size);
  console.log(`Construction (${citers.toLocaleString()} iterations):`);
  const constructResults = [
    benchConstruct('  CatalogMap', () => new CatalogMap(mapping), citers),
    benchConstruct('  FanoutCatalogMap', () => new FanoutCatalogMap(mapping), citers),
  ];
  for (const r of constructResults) {
    r.nsPerCall = undefined;
  }
  const baselineUs = constructResults[0].usPerCall;
  console.log(header.replace('Per call (ns)', 'Per call (µs)'));
  console.log(sep);
  for (const r of constructResults) {
    const speedup = r === constructResults[0] ? '—' : `${(baselineUs / r.usPerCall).toFixed(2)}×`;
    console.log(
      `${r.label.padEnd(col[0])} ${r.elapsed.toFixed(1).padStart(col[1])} ${r.usPerCall.toFixed(1).padStart(col[2])} ${speedup.padStart(col[3])}`
    );
  }
  console.log();

  console.log('Trie overhead (excl. shared _words array):');
  // FanoutCatalogMap uses plain objects — count populated nodes × ~80 bytes each (V8 object overhead).
  const fanD1Nodes = Object.keys(fan._d1).length;
  const fanD2Nodes = Object.keys(fan._d2).length;
  const fanObjBytes = (fanD1Nodes + fanD2Nodes) * 80;
  console.log(
    `  FanoutCatalogMap (obj d2)     ${fmtBytes(fanObjBytes).padStart(10)}  (${fanD1Nodes} d1 + ${fanD2Nodes} d2 nodes × ~80 B)`
  );
  for (const [label, instance] of [
    ['FanoutD2Array (arr d2)', fd2],
    ['FanoutD3Array (arr d3)', fd3],
  ]) {
    const bytes = trieBytes(instance);
    console.log(`  ${label.padEnd(28)} ${fmtBytes(bytes).padStart(10)}`);
  }
  // SparseTrieD3 uses plain objects — estimate from key count × ~60 bytes/entry.
  const st3d1 = Object.keys(st3._d1).length;
  const st3d2 = Object.keys(st3._d2).length;
  const st3d3 = Object.keys(st3._d3).length;
  const st3Bytes = (st3d1 + st3d2 + st3d3) * 60;
  console.log(
    `  SparseTrieD3                 ${fmtBytes(st3Bytes).padStart(10)}  (${st3d1} d1 + ${st3d2} d2 + ${st3d3} d3 nodes × ~60 B)`
  );

  // Show prefix node counts at each depth to estimate cost of deeper coverage.
  const nodeCounts = prefixNodeCounts(mapping, 6);
  const incr = nodeCounts.map((c, i) => (i === 0 ? c : c - nodeCounts[i - 1]));
  console.log(`  Unique prefix nodes by depth:`);
  for (let d = 0; d < 6; d++) {
    const cumBytes = nodeCounts[d] * 60;
    const incrNote = d === 0 ? '' : `  (+${incr[d]} new at d${d + 1})`;
    console.log(
      `    d${d + 1}: ${String(nodeCounts[d]).padStart(6)} nodes  ${fmtBytes(cumBytes).padStart(9)} cumulative${incrNote}`
    );
  }

  // PrefixMap — flat {prefix→best} hash: each entry ~60 B (key string + value string + object slot).
  const pm3Keys = Object.keys(pm3._map).length;
  const pm5Keys = Object.keys(pm5._map).length;
  console.log(`  PrefixMap (d3)               ${fmtBytes(pm3Keys * 60).padStart(10)}  (${pm3Keys} keys × ~60 B)`);
  console.log(`  PrefixMap (d5)               ${fmtBytes(pm5Keys * 60).padStart(10)}  (${pm5Keys} keys × ~60 B)`);
  console.log();
}
