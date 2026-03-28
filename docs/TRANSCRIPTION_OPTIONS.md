# Transcription Backend Options

Current setup: **AssemblyAI Universal-2** with built-in speaker diarization.

Pain points: accent handling (multiple nationalities), rally domain vocabulary, background noise (engine/intercom).

---

## Option A — Groq Whisper large-v3
**Type:** Cloud | **Diarization:** No (separate step needed)

Groq hosts Whisper large-v3 on fast inference chips. Significantly better accent handling than AssemblyAI. Very cheap — free tier likely covers typical stage lengths. Word-level timestamps available.

- **Pros:** Easy API swap, fast, cheap, excellent accent coverage
- **Cons:** No diarization — need pyannote or similar separately
- **Effort:** Low

---

## Option B — OpenAI Whisper API
**Type:** Cloud | **Diarization:** No

`whisper-1` via OpenAI API. Simple to call, ~$0.006/min. Middle-ground quality — better than AssemblyAI on accents, not as strong as large-v3.

- **Pros:** Simple integration, reliable
- **Cons:** No diarization, lower quality than large-v3, no word-level timestamps from API
- **Effort:** Low

---

## Option C — Deepgram Nova-2
**Type:** Cloud | **Diarization:** Yes (built-in)

Closest drop-in replacement for AssemblyAI. Has its own diarization, word-level timestamps, and **keyword boosting** — you can feed it the rally vocabulary CSV to bias transcription toward known terms. Accent handling noticeably better than AssemblyAI. ~$0.004/min.

- **Pros:** Drop-in replacement, keyword boosting for rally vocab, diarization included, cheaper than AssemblyAI
- **Cons:** Still cloud/paid
- **Effort:** Low–Medium

---

## Option D — faster-whisper + pyannote-audio (local)
**Type:** Local | **Diarization:** Yes (pyannote-audio, separate)

Self-hosted. `faster-whisper` runs Whisper large-v3 at 4–8× speed with word-level timestamps. `pyannote-audio` does best-in-class speaker diarization. `WhisperX` packages both together.

**Requires a GPU** — RTX 3070 or better handles 20–40 min stages well. CPU is usable but slow.

- **Pros:** Free, private, best accent handling available, no per-minute cost, can fine-tune
- **Cons:** GPU needed for speed, more complex setup
- **Effort:** Medium–High

---

## Diarization separation architecture

Any option above can feed into a clean separated pipeline:

```
audio ──► transcribe (Whisper / Deepgram)  ──► word timestamps
audio ──► diarize   (pyannote / Deepgram)  ──► speaker segments
           └── merge ──► filter to co-driver ──► Gemini conversion
```

`corecce.py::transcribe_and_diarize()` is already isolated — swapping backends is a contained change.

---

## Recommendation summary

| Option | Quality | Cost | Effort | Diarization |
|--------|---------|------|--------|-------------|
| A — Groq Whisper | ★★★★☆ | Free/cheap | Low | Separate |
| B — OpenAI Whisper | ★★★☆☆ | ~$0.006/min | Low | Separate |
| C — Deepgram Nova-2 | ★★★★☆ | ~$0.004/min | Low–Med | Built-in + keyword boost |
| D — faster-whisper local | ★★★★★ | Free | Med–High | pyannote (separate) |

**If GPU available → Option D.** Otherwise → **Option C** (Deepgram) for best cloud upgrade with keyword boosting being a meaningful win for rally vocabulary.
``