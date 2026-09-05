# Architectural decisions

## 2026-08-28 hybrid semantic memory (AURA 2.0 contract Phase 2)

- Semantic recall is added BESIDE the existing lexical retrieval, at the
  `Retriever` seam `memory/retrieval.py` already documents for exactly this
  purpose. `KeywordRetriever` and `RankedRetriever` are untouched; lexical
  retrieval remains the behavior when anything semantic fails or is off.
- The semantic half indexes EPISODIC memories only. Episodic rows carry the
  provenance fields the contract requires (source, importance, confidence,
  occurred_at, created_at, stable row id). The raw transcript stays
  lexical-only, as today.
- Storage is one new `SemanticVector` table in the existing SQLite database
  (memory_id, provider, model, dimensions, version, float32 vector BLOB).
  `create_all` is additive and idempotent, so no migration is needed and no
  destructive change occurs. No vector database, no numpy; vectors are
  stored via the stdlib `array` module and cosine similarity runs in
  process over the same bounded candidate pool the lexical ranker uses
  (`retrieval_scope`). Reasons: smallest reliable storage, Android/Render
  portability, offline operation, zero new dependencies.
- Embedding providers sit behind `memory/embeddings.py`:
  `HashingEmbeddingProvider` (LOCAL, deterministic n-gram feature hashing,
  stdlib only - honest n-gram generalization, NOT deep semantics),
  `OllamaEmbeddingProvider` (LOCAL), `RemoteEmbeddingProvider` (REMOTE,
  OpenAI-compatible). Provider metadata (provider, model, dimensions,
  version) is stored with every vector; vectors from incompatible
  metadata are never compared - they are stale, reported as such, and
  semantic retrieval is unavailable until reindex.
- PRIVACY BOUNDARY: a REMOTE provider refuses to embed unless
  `memory.semantic.allow_remote` is explicitly true. Fails closed.
  Diagnostics record ids, counts, latency and reasons - never memory
  content, never vectors, never credentials.
- Hybrid fusion is Reciprocal Rank Fusion (k=60): deterministic,
  scale-free, and it avoids inventing a score normalization across two
  different score spaces (lexical overlap vs cosine). The reranker is
  isolated behind a single method so a real reranker can replace it;
  confidence and importance act only as deterministic tie-breakers and
  can never reorder a fusion result into a contradiction override.
  Conflict handling is NOT bypassed: retrieval returns evidence lines;
  correction/confirmation remains the user model's job, unchanged.
- Degradation contract: provider unavailable/timeout/malformed/stale ->
  semantic mode returns nothing and the hybrid retriever degrades to
  lexical results with `last_mode` recording what happened; retrieval
  failures never raise out of memory. Deleted memories cannot be
  resurrected: semantic candidates are fetched by inner join to the live
  episodic row, so an orphaned vector is structurally unreturnable.
- Indexing never blocks persistence: `EpisodicStore.remember` commits
  first; embedding happens after, failure-tolerant, with statuses
  indexed / failed / skipped / stale observable via
  `SemanticIndexer.status()` and the Phase 1 diagnostics trace.
- Configuration lives in the existing `memory` section of
  `core/config.py` DEFAULT_CONFIG and `config.yaml`
  (`memory.semantic.*`). Default OFF. The phone's Control Hub ALLOWED
  settings allow-list is deliberately untouched; semantic settings are
  config-file scope for now. Startup NEVER fails because embeddings are
  unavailable: no provider -> lexical-only, exactly today's behavior.

### Amended 2026-08-29 (completing Phase 2)

- `memory.semantic.weight` is semantic's share of the fused score
  (lexical takes 1 - weight), clamped to [0, 1] rather than validated,
  because a bad config value must not be able to break recall. Default
  0.5 scales both halves equally and therefore orders results exactly
  as the unweighted RRF did - the knob ships without moving behaviour.
  It tilts fusion, it does not gate it: at weight 0.0 semantic
  candidates still appear, ranked behind every lexical hit.
- The reported `lexical_score` and `semantic_score` stay each
  retriever's OWN score (reciprocal rank, real cosine); the weight is
  applied only where they are combined. Provenance has to say what each
  half actually claimed, not what fusion did with it.
- The cosine floor belongs to the PROVIDER
  (`recommended_min_similarity`), not to `SemanticRetriever`, because
  the distribution of similarities is a property of the embedding
  space. Hashed n-grams collide and score arbitrary text well above
  zero; a trained model separates cleanly. One shared constant would be
  wrong for at least one provider. `memory.semantic.min_similarity`
  (default null = ask the provider) overrides it.
- The hashing provider declares 0.24, MEASURED as the knee of the
  benchmark sweep, not chosen: at 0.05 the hashing space returned 3.0
  memories for every query with no correct answer (lexical returns
  none); at 0.24 that drops to 1.0 while recall stays 0.708 vs
  lexical's 0.583; at 0.26 recall collapses to lexical's and semantic
  stops earning its place. Ollama and remote declare 0.05 explicitly
  labelled UNMEASURED - no model-backed provider has been benchmarked
  here, and inventing a floor would silently discard real recall.
- The sweep stays in `scripts/benchmark_semantic.py` rather than being
  deleted once a value was picked, so the number can be re-derived on a
  real corpus instead of trusted.
- Benchmark methodology, after the original was found to be measuring
  the wrong thing (it compared CORPUS positions against SQLite primary
  keys - every expected id was off by one): recall and precision average
  over the queries that HAVE a relevant memory, precision divides by
  results actually returned rather than by K, and the deliberately
  unanswerable queries are reported separately as `noise@K` instead of
  being averaged into the column that hid them.
- The new `memory.semantic` config block made the Android fixture
  `android/app/src/test/resources/live/settings.json` stale, which
  `tests/test_settings_fixture.py` catches by design. It was patched
  with exactly the semantic block; the regenerate-everything path also
  writes host-dependent values (an active provider chain, a masked API
  key, the local model name) into two sibling fixtures, so those were
  reverted. Safe because both the app and its tests parse with
  `ignoreUnknownKeys = true` - verified by forcing the Kotlin contract
  test to re-run rather than accepting an UP-TO-DATE pass.

## 2026-08-27 capability audit

- Keep the existing polling gateway and Android dispatcher as the transport/execution backend.
- Make each executable Android tool map to its own registered capability; do not use `dummy`, tool-name fallback, or coarse-only capability records.
- Device transport requests must pass through `ToolExecutor`; the Android dispatcher remains the final device-side executor and returns structured reports.
- Runtime Android permission/health state must come from a live device heartbeat rather than unconditional startup grants.
- A missing companion heartbeat is `UNAVAILABLE`, not a missing Android
  permission, because permission facts are unknowable until the companion
  reports them. A screen-capture user switch reported by a live companion is
  `BLOCKED_PERMISSION`.
- The HTTP harness mirrors the server capability endpoint before its local
  ToolExecutor gate; it must not invent device availability merely because a
  relay URL exists.
- The phone's accessibility permission is not changed by the audit. A missing
  AURA service entry in Android secure settings is a manual user action and
  must remain an explicit prerequisite for live heartbeat and execution.

## 2026-08-27 physical integration

- Use the bundled JDK 21 with explicit `TEMP`/`TMP=C:\\Windows\\Temp` for
  Gradle on this Windows desktop. The repository wrapper and Android build
  files remain unchanged; the default JDK 17/desktop environment reproduces
  the loopback `Invalid argument` failure.
- Device result reports may legitimately have no observation on an execution
  failure, so `observation_id` is optional at the HTTP boundary. Successful
  observation/action reports still carry authoritative observation IDs.
- The active screenshot path is `AgentRunDriver`/`AccessibilityToolDispatcher`
  plus `AccessibilityScreenshotCapture`; the old `AuraAccessibilityService`
  agent-step loop is disabled and must not be revived to satisfy old tests.
- Local physical testing may temporarily use the app's normal configured URL
  field, but the phone must be restored to the production URL before final
  verification; the token remains encrypted/masked.

## Ranking is separate from selection in SkillDiscovery

`discover()` ranks candidates and attaches live capability state; only
`select_best_executable()` decides executability, and it returns a candidate
only when its state is `AVAILABLE`. Keeping these separate is what lets the
runtime show the model an UNAVAILABLE capability *with its reason* (honest
evidence) without ever presenting it as callable.

## select_best_executable may fall through to a lower-scoring domain

Walking the ranked list means that when every Android candidate is
UNAVAILABLE, an unrelated but AVAILABLE capability further down can be
returned - observed: "What's the current app?" -> `system.time`, "Launch the
AURA companion app" -> `desktop.applications`.

Deliberately NOT changed. It is documented behaviour ("walks down the ranked
list"), it is called only from tests - the runtime uses `discover()` at
`agent/runtime.py:462` and `:572` - and the execution path is protected by
three independent layers: `_live_capability_context` states each capability's
live state and that only AVAILABLE ones may be called,
`_observation_gap_correction` filters strictly by AVAILABLE, and
`ToolExecutor` refuses a non-AVAILABLE capability with
`execution="not_attempted"`. Suppressing the fall-through would require
inventing a score-distance or domain-affinity threshold that no requirement
asks for. Revisit only if this helper is ever wired into the execution path.

## Session ids use core.ids, not raw uuid4

`/api/agent/intent` originally minted `session_{uuid4().hex[:12]}`, which is
12 hex characters and so fails the project contract
`^[a-z]+_[0-9a-f]{16}$` that `core/ids.py` enforces. `new_session_id()`
already existed; the raw uuid was a duplicated abstraction and is now removed.

## Evidence rule applied to scripts/aura_android.py

That CLI defaults to `--bridge loopback`, a deterministic in-process fake.
Output from a default invocation is NOT real-device evidence and must never be
reported as verification; only `--bridge http` reaches the phone.
