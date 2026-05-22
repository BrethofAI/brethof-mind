-- Example domain content graph for brethof-mind. Apply once (see README).
-- Rename the tables/fields to your own domain — this is a template.

-- ── Nodes ────────────────────────────────────────────────────────────────
DEFINE TABLE IF NOT EXISTS episode TYPE ANY SCHEMALESS PERMISSIONS NONE;
--   number int, slug string, title string, summary string,
--   status string (draft|published), duration_minutes number

DEFINE TABLE IF NOT EXISTS topic TYPE ANY SCHEMALESS PERMISSIONS NONE;
--   name string, slug string, parent record<topic>

DEFINE TABLE IF NOT EXISTS claim TYPE ANY SCHEMALESS PERMISSIONS NONE;
--   claim string, topic string,
--   stance string (recommend|warn|deprecate|contrarian|rule_of_thumb),
--   hot_take bool, active bool

DEFINE INDEX IF NOT EXISTS idx_episode_number ON episode FIELDS number;
DEFINE INDEX IF NOT EXISTS idx_topic_slug     ON topic   FIELDS slug;
DEFINE INDEX IF NOT EXISTS idx_claim_active   ON claim   FIELDS active;

-- ── Edges (graph relations) ───────────────────────────────────────────────
-- episode ->covers->   topic
-- episode ->states->   claim
-- episode ->callback-> episode
-- claim   ->supersedes->claim   (reuse the core 'supersedes' relation)
DEFINE TABLE IF NOT EXISTS covers   TYPE RELATION SCHEMALESS PERMISSIONS NONE;
DEFINE TABLE IF NOT EXISTS states   TYPE RELATION SCHEMALESS PERMISSIONS NONE;
DEFINE TABLE IF NOT EXISTS callback TYPE RELATION SCHEMALESS PERMISSIONS NONE;

DEFINE INDEX IF NOT EXISTS idx_covers_in ON covers FIELDS in;
DEFINE INDEX IF NOT EXISTS idx_states_in ON states FIELDS in;
DEFINE INDEX IF NOT EXISTS idx_callback_in ON callback FIELDS in;
