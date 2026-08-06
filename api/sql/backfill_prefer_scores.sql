-- Backfill prefer_score and prefer_score_components for all cards
-- This script recalculates the prefer score for all existing cards based on multiple attributes

WITH computed_components AS (
    SELECT
        scryfall_id,
        JSONB_BUILD_OBJECT(
            -- How heavily this ARTWORK has been reprinted, as a proxy for how canonical it
            -- is. The numerator counts rows sharing the illustration, so it must count only
            -- real printing events -- three kinds of row are not:
            --
            --   non-English  the same printing already counted in its English row. Mark
            --                Poole's Birds of Paradise art picked up 4bb (Spanish) and fbb
            --                (French) on top of the English 4th Edition printing.
            --   memorabilia  World Championship decks, Collectors' Edition and 30th
            --                Anniversary: not tournament-legal, not real sets. Four of the
            --                eight on that same artwork are BLACK-bordered (30a x2, ced,
            --                cei), so a border test alone misses half of them.
            --   gold/yellow  any remaining non-standard product Scryfall types as something
            --                other than memorabilia.
            --
            -- Together these took Poole's art from 21 counted printings to 11, against
            -- Marcelo Vignali's 12 -- reversing which artwork the site shows for that card.
            --
            -- White borders are deliberately NOT filtered: 2ed-6ed are white-bordered and
            -- perfectly real, and dropping them would penalise exactly the core-set reprints
            -- this component should reward.
            --
            -- Evidence (docs/issues/done/00720-prefer-score-artwork-tuning.md): a 47-card blind
            -- swap review returned 11 better, 36 same, 0 worse, and a later 378-card review
            -- against production added 2 more with no regressions. This changes only the
            -- numerator -- it does not stop such printings being displayed.
            'illustration_count', (
                SELECT
                    ROUND((23 * LN(1 + COUNT(*)) / LN(40))::numeric, 4)
                FROM magic.cards query_target_cards
                WHERE (
                    query_target_cards.illustration_id = source.illustration_id AND
                    query_target_cards.illustration_id IS NOT NULL AND
                    query_target_cards.card_name = source.card_name AND
                    query_target_cards.raw_card_blob ->> 'lang' = 'en' AND
                    COALESCE(query_target_cards.raw_card_blob ->> 'set_type', '') <> 'memorabilia' AND
                    COALESCE(query_target_cards.card_border, '') NOT IN ('gold', 'yellow')
                )
            ),
            'rarity', (
                CASE
                    WHEN card_rarity_int = 0 THEN 16  -- common
                    WHEN card_rarity_int = 1 THEN 16  -- uncommon
                    WHEN card_rarity_int = 2 THEN 11  -- rare
                    WHEN card_rarity_int = 3 THEN 0   -- mythic
                    ELSE 0
                END
            ),
            'border', (
                CASE
                    WHEN card_border = 'black' THEN 14
                    WHEN card_border = 'white' THEN 0
                    WHEN card_border = 'borderless' THEN 0
                    ELSE 0
                END
            ),
            'frame', (
                CASE
                    WHEN card_frame_data ? '2015' THEN 42
                    WHEN card_frame_data ? '2003' THEN 30
                    WHEN card_frame_data ? '1997' THEN 25
                    WHEN card_frame_data ? '1993' THEN 10
                    ELSE 0
                END
            ),
            'extended_art', (
                CASE
                    WHEN card_frame_data ? 'Extendedart' THEN 12
                    ELSE 0
                END
            ),
            'highres_scan', (
                CASE
                    WHEN raw_card_blob ->> 'image_status' = 'highres_scan' THEN 16
                    ELSE 0
                END
            ),
            'has_paper', (
                CASE
                    WHEN raw_card_blob -> 'games' ? 'paper' THEN 6
                    ELSE 0
                END
            ),
            'language', (
                CASE
                    WHEN raw_card_blob ->> 'lang' = 'en' THEN 40
                    ELSE 0
                END
            ),
            'legendary_frame', (
                CASE
                    WHEN raw_card_blob -> 'frame_effects' ? 'legendary' THEN 5
                    ELSE 0
                END
            ),
            'non_showcase', (
                CASE
                    WHEN NOT (COALESCE(raw_card_blob -> 'frame_effects', '[]'::jsonb) ? 'showcase') THEN 10
                    ELSE 0
                END
            ),
            'finish', (
                CASE
                    WHEN raw_card_blob -> 'finishes' ? 'nonfoil' THEN 10
                    WHEN raw_card_blob -> 'finishes' ? 'foil' THEN 5
                    WHEN raw_card_blob -> 'finishes' ? 'etched' THEN 0
                    ELSE 0
                END
            ),
            'artwork_set', (
                CASE
                    WHEN card_set_code IS NULL OR card_set_code NOT IN ('dbl') THEN 20
                    ELSE 0
                END
            ),
            -- Bonus for artwork in Magic's core style, i.e. NOT a licensed crossover and
            -- not a stylistic departure. Written as a bonus for being on-style rather than
            -- a penalty for being off-style so every component stays non-negative, like the
            -- rest of this table.
            --
            -- `external-ip` is the Scryfall tagger's parent tag over ~57 licensed
            -- franchises (Fallout, Warhammer, Marvel, Doctor Who, Fortnite, ...).
            -- `dungeons-and-dragons` and `the-lord-of-the-rings` are deliberately exempt:
            -- external IP whose art matches Magic's high-fantasy look. Verified complete --
            -- no artwork carries a sibling tag (arda, hobbit, abeir-toril, dnd-multiverse)
            -- without also carrying its parent, so no Middle-earth or Forgotten Realms art
            -- is demoted by accident.
            --
            -- The second clause covers stylistic departures that are not licensed universes:
            -- anime, comic-style, line-art and word-art-title. Note it applies even to the
            -- exempt IPs -- Drizzt Do'Urden (afr #338) is tagged dungeons-and-dragons AND
            -- line-art, and is demoted, because a line-art rendering is a departure from the
            -- painted core style whoever owns the IP. Confirmed against the artwork.
            --
            -- Evidence (docs/issues/done/00720-prefer-score-artwork-tuning.md): in 177 labelled
            -- artwork comparisons where exactly one side was off-style, the on-style side
            -- was chosen 177 times. Blind swap review across weights 6, 9 and 14 gave 78
            -- "better" and 0 "worse" over 114 changed cards. Fixes both cards that opened
            -- the issue -- Puresteel Paladin was showing its Fallout printing and Sword of
            -- the Animist its Marvel one.
            --
            -- A year/era term was the obvious alternative and was rejected: it would
            -- permanently penalise all future art, whereas a tag on the artwork generalises.
            'art_style', (
                CASE
                    WHEN (
                        card_art_tags ? 'external-ip'
                        AND NOT (card_art_tags ?| ARRAY['dungeons-and-dragons', 'the-lord-of-the-rings'])
                    ) OR card_art_tags ?| ARRAY['anime', 'comic-style', 'line-art', 'word-art-title'] THEN 0
                    ELSE 14
                END
            )
        ) AS new_components
    FROM magic.cards source
),
computed_scores AS (
    SELECT
        scryfall_id,
        new_components,
        (
            SELECT
                SUM(value::numeric)
            FROM
                jsonb_each(new_components)
        )::real AS new_score
    FROM computed_components
)
UPDATE magic.cards
SET
    prefer_score_components = computed_scores.new_components,
    prefer_score = computed_scores.new_score
FROM computed_scores
WHERE magic.cards.scryfall_id = computed_scores.scryfall_id
  AND (
      magic.cards.prefer_score_components IS DISTINCT FROM computed_scores.new_components
      OR magic.cards.prefer_score IS DISTINCT FROM computed_scores.new_score
  );
