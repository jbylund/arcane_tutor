use std::fs;
use std::path::PathBuf;
use std::time::Instant;

use fancy_regex::{Error as FancyError, RegexBuilder, RuntimeError};
use regex_syntax::hir::{Hir, HirKind, Repetition};
use regex_syntax::ParserBuilder;
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct PatternRow {
    source: String,
    label: String,
    pattern: String,
}

#[derive(Debug, Default)]
struct PatternMetrics {
    bytes: usize,
    lookarounds: usize,
    alternations: usize,
    groups: usize,
    quantifiers: usize,
}

#[derive(Debug, Default)]
struct HirMetrics {
    hir_nodes: usize,
    hir_depth: usize,
    repetition_count: usize,
    max_repeat_min: u64,
    max_repeat_max: u64,
    max_repeat_span: u64,
    parse_ok: bool,
}

fn hir_node_count(hir: &Hir) -> usize {
    1 + match hir.kind() {
        HirKind::Capture(c) => hir_node_count(&c.sub),
        HirKind::Concat(subs) | HirKind::Alternation(subs) => subs.iter().map(hir_node_count).sum(),
        HirKind::Repetition(r) => hir_node_count(&r.sub),
        _ => 0,
    }
}

fn hir_depth(hir: &Hir) -> usize {
    1 + match hir.kind() {
        HirKind::Capture(c) => hir_depth(&c.sub),
        HirKind::Concat(subs) | HirKind::Alternation(subs) => {
            subs.iter().map(hir_depth).max().unwrap_or(0)
        }
        HirKind::Repetition(r) => hir_depth(&r.sub),
        _ => 0,
    }
}

fn walk_repetitions(hir: &Hir, m: &mut HirMetrics) {
    if let HirKind::Repetition(Repetition { min, max, sub, .. }) = hir.kind() {
        m.repetition_count += 1;
        let min = u64::from(*min);
        m.max_repeat_min = m.max_repeat_min.max(min);
        let hi = max.map_or(u64::MAX, u64::from);
        m.max_repeat_max = m.max_repeat_max.max(hi);
        let span = hi.saturating_sub(min);
        m.max_repeat_span = m.max_repeat_span.max(span);
        walk_repetitions(sub, m);
        return;
    }
    match hir.kind() {
        HirKind::Capture(c) => walk_repetitions(&c.sub, m),
        HirKind::Concat(subs) | HirKind::Alternation(subs) => {
            for sub in subs {
                walk_repetitions(sub, m);
            }
        }
        _ => {}
    }
}

fn hir_metrics(raw: &str) -> HirMetrics {
    let mut m = HirMetrics::default();
    let Ok(hir) = ParserBuilder::new().nest_limit(200).build().parse(raw) else {
        return m;
    };
    m.parse_ok = true;
    m.hir_nodes = hir_node_count(&hir);
    m.hir_depth = hir_depth(&hir);
    walk_repetitions(&hir, &mut m);
    m
}

fn count_metrics(raw: &str) -> PatternMetrics {
    let mut m = PatternMetrics {
        bytes: raw.len(),
        ..Default::default()
    };
    for token in ["(?=", "(?!", "(?<=", "(?<!"] {
        m.lookarounds += raw.matches(token).count();
    }
    m.alternations = raw.matches('|').count();
    m.groups = raw.matches('(').count();
    for ch in ['*', '+', '?', '{'] {
        m.quantifiers += raw.matches(ch).count();
    }
    m
}

fn load_corpus(path: &PathBuf) -> Vec<String> {
    let text = fs::read_to_string(path).unwrap_or_default();
    text.lines()
        .flat_map(|line| line.split('\u{001f}').map(str::to_string))
        .filter(|s| !s.is_empty())
        .collect()
}

fn percentile(sorted: &[u64], p: f64) -> u64 {
    if sorted.is_empty() {
        return 0;
    }
    let idx = ((sorted.len() - 1) as f64 * p).round() as usize;
    sorted[idx]
}

fn is_backtrack_limit(err: &FancyError) -> bool {
    matches!(err, FancyError::RuntimeError(RuntimeError::BacktrackLimitExceeded))
}

fn corpus_succeeds(full: &str, limit: usize, corpus: &[String]) -> bool {
    let re = match RegexBuilder::new(full).backtrack_limit(limit).build() {
        Ok(r) => r,
        Err(_) => return false,
    };
    for hay in corpus {
        match re.is_match(hay) {
            Ok(_) => {}
            Err(e) if is_backtrack_limit(&e) => return false,
            Err(_) => return false,
        }
    }
    true
}

/// Smallest backtrack limit that completes every `is_match` over the corpus, else None if > hi.
fn bisect_min_backtrack(full: &str, corpus: &[String], hi: usize) -> Option<usize> {
    if !corpus_succeeds(full, hi, corpus) {
        return None;
    }
    if corpus_succeeds(full, 0, corpus) {
        return Some(0);
    }
    let (mut lo, mut hi_ok) = (1usize, hi);
    while lo < hi_ok {
        let mid = lo + (hi_ok - lo) / 2;
        if corpus_succeeds(full, mid, corpus) {
            hi_ok = mid;
        } else {
            lo = mid + 1;
        }
    }
    Some(hi_ok)
}

fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .expect("repo root")
        .to_path_buf();
    let patterns_path = repo.join("scripts/regex_limit_survey/patterns.json");
    let corpus_path = repo.join("card_engine/testdata/text_corpus.txt");

    let rows: Vec<PatternRow> =
        serde_json::from_str(&fs::read_to_string(&patterns_path).expect("patterns.json")).expect("json");
    let corpus = load_corpus(&corpus_path);
    eprintln!("Loaded {} patterns, {} corpus strings", rows.len(), corpus.len());

    let mut results = Vec::new();
    for row in &rows {
        let full = format!("(?i){}", row.pattern);
        let metrics = count_metrics(&row.pattern);
        let hir = hir_metrics(&row.pattern);

        let compile_start = Instant::now();
        let re = match RegexBuilder::new(&full).build() {
            Ok(r) => r,
            Err(e) => {
                results.push(serde_json::json!({
                    "source": row.source,
                    "label": row.label,
                    "pattern": row.pattern,
                    "bytes": metrics.bytes,
                    "lookarounds": metrics.lookarounds,
                    "alternations": metrics.alternations,
                    "compile_ok": false,
                    "compile_error": format!("{e}"),
                }));
                continue;
            }
        };
        let compile_ms = compile_start.elapsed().as_secs_f64() * 1000.0;
        drop(re);

        const BISECT_HI: usize = 1_000_000;
        let bisect_start = Instant::now();
        let min_backtrack = bisect_min_backtrack(&full, &corpus, BISECT_HI);
        let bisect_ms = bisect_start.elapsed().as_secs_f64() * 1000.0;

        let (match_count, match_ms, backtrack_fail_default) = if min_backtrack.is_some() {
            let re = RegexBuilder::new(&full).backtrack_limit(BISECT_HI).build().expect("rebuild");
            let match_start = Instant::now();
            let mut match_count = 0u64;
            let mut backtrack_fail_default = false;
            for hay in &corpus {
                match re.is_match(hay) {
                    Ok(true) => match_count += 1,
                    Ok(false) => {}
                    Err(e) if is_backtrack_limit(&e) => {
                        backtrack_fail_default = true;
                        break;
                    }
                    Err(e) => {
                        results.push(serde_json::json!({
                            "source": row.source,
                            "label": row.label,
                            "pattern": row.pattern,
                            "bytes": metrics.bytes,
                            "lookarounds": metrics.lookarounds,
                            "compile_ok": true,
                            "match_error": format!("{e}"),
                        }));
                        backtrack_fail_default = true;
                        break;
                    }
                }
            }
            (match_count, match_start.elapsed().as_secs_f64() * 1000.0, backtrack_fail_default)
        } else {
            (0, 0.0, true)
        };

        results.push(serde_json::json!({
            "source": row.source,
            "label": row.label,
            "pattern": row.pattern,
            "bytes": metrics.bytes,
            "lookarounds": metrics.lookarounds,
            "alternations": metrics.alternations,
            "groups": metrics.groups,
            "quantifiers": metrics.quantifiers,
            "hir_parse_ok": hir.parse_ok,
            "hir_nodes": hir.hir_nodes,
            "hir_depth": hir.hir_depth,
            "repetition_count": hir.repetition_count,
            "max_repeat_min": hir.max_repeat_min,
            "max_repeat_max": if hir.max_repeat_max == u64::MAX { serde_json::Value::Null } else { hir.max_repeat_max.into() },
            "max_repeat_span": if hir.max_repeat_span == u64::MAX { serde_json::Value::Null } else { hir.max_repeat_span.into() },
            "compile_ok": true,
            "compile_ms": compile_ms,
            "bisect_ms": bisect_ms,
            "match_ms_default_limit": match_ms,
            "matches_in_corpus": match_count,
            "backtrack_fail_at_default": backtrack_fail_default,
            "min_backtrack_limit": min_backtrack,
            "uses_fancy": metrics.lookarounds > 0,
        }));
    }

    let legit: Vec<_> = results
        .iter()
        .filter(|r| r["source"] != "measured_hostile" && r["compile_ok"] == true)
        .collect();
    let fancy_legit: Vec<_> = legit.iter().filter(|r| r["uses_fancy"] == true).collect();

    let mut compile_ms: Vec<u64> = legit.iter().filter_map(|r| r["compile_ms"].as_f64().map(|x| x.ceil() as u64)).collect();
    compile_ms.sort_unstable();
    let mut match_ms: Vec<u64> = legit.iter().filter_map(|r| r["match_ms_default_limit"].as_f64().map(|x| x.ceil() as u64)).collect();
    match_ms.sort_unstable();
    let mut bytes: Vec<u64> = legit.iter().filter_map(|r| r["bytes"].as_u64()).collect();
    bytes.sort_unstable();
    let mut lookarounds: Vec<u64> = legit.iter().filter_map(|r| r["lookarounds"].as_u64()).collect();
    lookarounds.sort_unstable();
    let mut min_bt: Vec<u64> = legit.iter().filter_map(|r| r["min_backtrack_limit"].as_u64()).collect();
    min_bt.sort_unstable();
    let hir_legit: Vec<_> = legit.iter().filter(|r| r["hir_parse_ok"] == true).collect();
    let mut hir_nodes: Vec<u64> = hir_legit.iter().filter_map(|r| r["hir_nodes"].as_u64()).collect();
    hir_nodes.sort_unstable();
    let mut hir_depth: Vec<u64> = hir_legit.iter().filter_map(|r| r["hir_depth"].as_u64()).collect();
    hir_depth.sort_unstable();
    let mut rep_count: Vec<u64> = hir_legit.iter().filter_map(|r| r["repetition_count"].as_u64()).collect();
    rep_count.sort_unstable();
    let mut rep_max: Vec<u64> = hir_legit
        .iter()
        .filter_map(|r| r["max_repeat_max"].as_u64())
        .collect();
    rep_max.sort_unstable();
    let legit_fancy_min: Vec<u64> = fancy_legit.iter().filter_map(|r| r["min_backtrack_limit"].as_u64()).collect();

    let mut fancy_min_sorted = legit_fancy_min.clone();
    fancy_min_sorted.sort_unstable();
    let all_min_max = results.iter().filter_map(|r| r["min_backtrack_limit"].as_u64()).max().unwrap_or(0);

    let summary = serde_json::json!({
        "corpus_strings": corpus.len(),
        "pattern_count": rows.len(),
        "legitimate_compile_ok": legit.len(),
        "legitimate_fancy_feature_count": fancy_legit.len(),
        "bytes_p50": percentile(&bytes, 0.50),
        "bytes_p95": percentile(&bytes, 0.95),
        "bytes_max": bytes.last().copied().unwrap_or(0),
        "lookarounds_p95": percentile(&lookarounds, 0.95),
        "lookarounds_max": lookarounds.last().copied().unwrap_or(0),
        "compile_ms_p50": percentile(&compile_ms, 0.50),
        "compile_ms_p95": percentile(&compile_ms, 0.95),
        "compile_ms_max": compile_ms.last().copied().unwrap_or(0),
        "match_ms_p50": percentile(&match_ms, 0.50),
        "match_ms_p95": percentile(&match_ms, 0.95),
        "match_ms_max": match_ms.last().copied().unwrap_or(0),
        "min_backtrack_limit_p50": percentile(&min_bt, 0.50),
        "min_backtrack_limit_p95": percentile(&min_bt, 0.95),
        "min_backtrack_limit_max": min_bt.last().copied().unwrap_or(0),
        "fancy_min_backtrack_limit_max": legit_fancy_min.iter().max().copied().unwrap_or(0),
        "fancy_min_backtrack_limit_p95": percentile(&fancy_min_sorted, 0.95),
        "all_min_backtrack_limit_max": all_min_max,
        "hir_parse_ok_legit": hir_legit.len(),
        "hir_nodes_p50": percentile(&hir_nodes, 0.50),
        "hir_nodes_p95": percentile(&hir_nodes, 0.95),
        "hir_nodes_max": hir_nodes.last().copied().unwrap_or(0),
        "hir_depth_p95": percentile(&hir_depth, 0.95),
        "hir_depth_max": hir_depth.last().copied().unwrap_or(0),
        "repetition_count_p95": percentile(&rep_count, 0.95),
        "repetition_count_max": rep_count.last().copied().unwrap_or(0),
        "max_repeat_max_p95": percentile(&rep_max, 0.95),
        "max_repeat_max_max": rep_max.last().copied().unwrap_or(0),
    });

    println!("{}", serde_json::to_string_pretty(&serde_json::json!({ "summary": summary, "patterns": results })).unwrap());
}
