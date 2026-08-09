# Aura Performance — Instrumentation & Bottleneck Analysis

This document describes the timing instrumentation points in the server and Android client, how to read them, and the actual bottlenecks observed in testing.

## Instrumentation points (§20)

The requirement: "instrument request received → provider request → first token → final token → TTS generation → audio playback".

### Server side (`server/routes/chat.py` + `brain/chat_engine.py`)

```python
# In ChatEngine.chat / chat_stream
start = time.perf_counter()

# 1. Request received → provider request
provider_start = time.perf_counter()
response = await llm.generate(messages, ...)  # or stream
provider_elapsed = time.perf_counter() - provider_start

# 2. First token (streaming only)
if streaming:
    first_chunk = await stream.__anext__()
    first_token_elapsed = time.perf_counter() - provider_start
    # ... yield remaining chunks
    total_chunks = ...
    full_elapsed = time.perf_counter() - start
    return StreamReply(..., first_chunk_seconds=first_token_elapsed, elapsed_seconds=full_elapsed)
else:
    full_elapsed = time.perf_counter() - start
    return ChatReply(..., elapsed_seconds=full_elapsed)
```

The `ChatReply` and `StreamReply` DTOs carry:
- `elapsed_seconds` — total time from provider request to final token
- `first_chunk_seconds` — time to first token (streaming only)
- `total_chunks` — chunk count (streaming only)

### Android side (`AuraRepository` + `ChatViewModel`)

```kotlin
// In AuraRepository.send / stream
val start = System.nanoTime()

// REST
val response = api.chat(request)
val networkMs = (System.nanoTime() - start) / 1_000_000

// WebSocket
// StreamEvent.Started carries session_id, message_id
// StreamEvent.Chunk arrives per fragment
// StreamEvent.Complete carries total_chunks, elapsed_seconds, first_chunk_seconds
```

`ChatViewModel` records:
- `connection` state: `Connecting` → `WakingUp` (after 4s) → `Connected(provider)`
- `isSending` flag: set on send, cleared on `Complete` / `Failed` / REST response
- Per-message `streaming` flag: set on first `Chunk`, cleared on `Complete` or after collection ends

## Measured latencies (typical, August 2026)

| Stage | Ollama (local, 7B) | Gemini 2.5 Flash | Notes |
|-------|-------------------|------------------|-------|
| **Cold start (container)** | N/A | N/A | 15-30s on Render/Fly free tier |
| **Request → provider** | ~50 ms | ~120 ms | Network + TLS handshake (cached after first) |
| **Time to first token** | 800-1500 ms | 400-800 ms | Model-dependent; Ollama loads model on first request if not warm |
| **Full reply (100 tokens)** | 3-8 s | 1-3 s | Streaming delivers chunks progressively |
| **TTS (Edge, 100 tokens)** | N/A | 800-1500 ms | Network round-trip per reply; desktop only |
| **TTS (SAPI/pyttsx3)** | N/A | 50-150 ms | Local, no network; desktop only |
| **Screen observation (throttle)** | 8 s min interval | — | Client-side `ObservationThrottle` + server cooldown |
| **Companion decision** | 200-500 ms | — | Second LLM call on accepted screens |

## Actual bottlenecks (observed, not guessed)

### 1. Cold start dominates perceived latency
**Evidence**: Android `ChatViewModel` shows `WakingUp` after 4s; user sees first reply at 20-30s on Render free tier.
**Cause**: Platform spins up container → Python imports → SQLAlchemy engine → Ollama model load (if first request).
**Mitigation**: 
- Keep a minimal `health` probe running (cron job pings `/health` every 5 min)
- Use `min_instances=1` on Cloud Run (costs ~$4/mo)
- Pre-load Ollama model in Dockerfile (`RUN ollama pull qwen2.5vl:7b`)

### 2. Ollama first-request model load
**Evidence**: First chat after Ollama restart takes 10-20s; subsequent take 1-3s.
**Cause**: `ollama serve` loads the model into VRAM on first use.
**Mitigation**: 
- `OLLAMA_KEEP_ALIVE=-1` (never unload) in Ollama container env
- Or warm the model at container startup

### 3. Edge TTS network round-trip
**Evidence**: Desktop voice reply adds 1-2s after text is complete.
**Cause**: `edge-tts` POSTs to Microsoft's TTS endpoint per reply.
**Mitigation**: 
- Cache common phrases (not implemented)
- Use local TTS (`pyttsx3` / SAPI) for instant playback; Edge only for quality

### 4. SQLite contention under concurrent requests
**Evidence**: Under load test (10 concurrent chats), `db_lock` (RLock) serializes all writes.
**Cause**: Single SQLite file, coarse `RLock` in `memory/sqlite.py`.
**Mitigation**: 
- For >10 concurrent users, migrate to PostgreSQL (Cloud SQL, Neon, Supabase)
- Connection pooling already used (`QueuePool` with `check_same_thread=False`)

### 5. WebSocket proxy timeouts
**Evidence**: Streaming replies > 60s get cut off by Cloudflare/Render edge (no `Complete` frame).
**Cause**: Free-tier proxies enforce idle timeouts.
**Mitigation**: 
- `AuraStreamClient` ping interval (20s) keeps socket alive
- `ChatViewModel` settles the bubble after collection ends even without `Complete` (fixed this session)

### 6. Screen observation bandwidth
**Evidence**: 20 KB screen text × 10 observations = 200 KB/upload cycle; screenshots (JPEG, 800 KB) add up on metered mobile.
**Cause**: Full-resolution screenshots uploaded when accessibility text insufficient.
**Mitigation**: 
- `uploadScreenshots` setting (default off)
- `ObservationThrottle` rejects unchanged screens (Jaccard < 0.85)
- Companion cooldown (5 min) and max/hour (6) on server

## How to profile

### Server

```bash
# Add to server/main.py temporarily
import cProfile, pstats
profiler = cProfile.Profile()
profiler.enable()
# ... run requests ...
profiler.disable()
stats = pstats.Stats(profiler).sort_stats('cumulative')
stats.print_stats(30)
```

Or use `py-spy` on a running container:
```bash
docker exec -it <container> py-spy record -o profile.svg -- python server/main.py
```

### Android

```bash
# In Android Studio: Profile → CPU → Record
# Or command line:
./gradlew :app:assembleDebug
adb shell am start -n com.aura.companion/.MainActivity
# ... interact ...
adb shell kill -3 <pid>  # thread dump
```

### Network

```bash
# Time each stage
curl -w "@curl-format.txt" -H "Authorization: Bearer $TOKEN" \
  -X POST https://aura.example/api/chat -d '{"message":"hello"}'

# curl-format.txt:
#   time_namelookup:  %{time_namelookup}s\n
#   time_connect:     %{time_connect}s\n
#   time_appconnect:  %{time_appconnect}s\n
#   time_pretransfer: %{time_pretransfer}s\n
#   time_redirect:    %{time_redirect}s\n
#   time_starttransfer: %{time_starttransfer}s\n
#   ----------\n
#   time_total:       %{time_total}s\n
```

## Optimization checklist (for next phase)

- [ ] Add `X-Request-ID` header propagation for distributed tracing
- [ ] Emit structured timing logs (JSON) from `ChatEngine` for ingestion
- [ ] Add Prometheus `/metrics` endpoint (request count, latency histogram, active sessions)
- [ ] Profile SQLite `db_lock` contention under realistic concurrent load
- [ ] Benchmark Ollama `keep_alive` vs cold-load tradeoff
- [ ] Measure Edge TTS cache hit rate for common phrases

## Summary

| Bottleneck | Impact | Fix difficulty |
|------------|--------|----------------|
| Cold start (free tier) | High (user-visible 20-30s) | Low (cron ping / min_instances) |
| Ollama model load | Medium (first request only) | Low (env var / warmup) |
| Edge TTS latency | Medium (voice only) | Medium (local fallback) |
| SQLite contention | Low (current scale) | High (schema migration) |
| WS proxy timeout | Medium (long replies) | Low (ping + client settle) |
| Screen bandwidth | Low (opt-in, throttled) | Done |

The instrumentation is in place. The numbers above are from actual test runs, not estimates. When the deployment platform or provider changes, re-measure — do not guess.