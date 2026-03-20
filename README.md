# GoRally

Turn rally onboard videos into printable, interactive pace notes — in minutes.

GoRally takes a YouTube link or local video file, isolates the co-driver's calls using speaker diarization, and uses an LLM to convert them into clean rally shorthand. The result is an interactive HTML pace note viewer you can use on a tablet in the car, or print and cut for a real stage.

---

## What it does

1. **Downloads or ingests** audio from a YouTube link or local video file
2. **Transcribes** the co-driver's voice using AssemblyAI (speaker diarization separates co-driver from driver)
3. **Converts** the transcription into rally shorthand using Gemini 2.5 Flash
4. **Outputs** an interactive HTML pace note viewer — paginated, editable, printable

The viewer supports:
- Arrow key / click navigation between pages
- Double-click to edit any note inline
- Insert and delete rows
- Ctrl+Z undo
- Configurable notes per page
- Print-ready layout

---

## Prerequisites

- Python 3.10+
- `ffmpeg` installed and on your PATH
- An [AssemblyAI](https://www.assemblyai.com/) API key (free tier available)
- A [Google Gemini](https://aistudio.google.com/) API key (free tier available)

---

## Setup

```bash
git clone https://github.com/yourname/gorally.git
cd gorally
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
ASSEMBLY_AI_API_KEY=your_assemblyai_key
GEMINI_API_KEY=your_gemini_key
```

---

## Usage

### Web app (recommended)

```bash
python app.py
```

Open `http://localhost:5000` in your browser. Four modes are available:

| Tab | Input | Output |
|-----|-------|--------|
| YouTube Link | Public YouTube URL | Pace notes HTML |
| Local Video | Any video file (MP4, MKV, etc.) | Pace notes HTML |
| Transcription File | Existing `transcription.txt` | Pace notes HTML |
| Re-render | Existing `pacenotes.txt` | Re-rendered HTML |

The transcription + LLM step typically takes **2–5 minutes** depending on video length. Progress is shown in real time.

### Command line

```bash
# From a YouTube link
python gorally.py --link "https://www.youtube.com/watch?v=..."

# From a local video file
python gorally.py --path "/path/to/video.mp4"

# From an existing transcription
python gorally.py --transcription-file outputs/MyStage/transcription.txt

# Re-render HTML from existing pace notes
python gorally.py --rerender outputs/MyStage/pacenotes.txt
```

---

## Output structure

Each run creates a folder under `outputs/`:

```
outputs/
  Stage Name/
    audio.wav            # extracted audio
    transcription.txt    # co-driver segments with timestamps
    pacenotes.txt        # Gemini-generated shorthand
    pacenotes.html       # interactive viewer
    info.txt             # source URL / metadata
```

---

## Pace note shorthand

GoRally uses standard international rally shorthand conventions:

| Symbol | Meaning |
|--------|---------|
| `L` / `R` | Left / Right |
| `1`–`6` | Corner severity (1 = hairpin, 6 = fast) |
| `<` | Opens (corner widens) |
| `>` | Tightens (corner narrows) |
| `/` | Over (crest, bridge, jump) |
| `&` | And (immediate next corner) |
| `Cr` | Crest |
| `K` | Kink |
| `H` | Hairpin |
| `!` | Caution |
| `CARE` | Take care |
| `DONT` | Do not cut |
| `m` | Metres (distance marker) |

The LLM prompt is tuned for rally-specific homophones and common transcription errors (e.g. "too" → `2`, "tightens" → `>`).

---

## Tips for best results

- **Video quality matters.** Onboards with a clear co-driver microphone (not just cabin audio) produce significantly better transcriptions.
- **Review the transcription first.** Open `transcription.txt` after the transcription step — if the co-driver wasn't correctly isolated, you can manually clean it and use the *Transcription File* tab to regenerate pace notes without re-transcribing.
- **Use Re-render freely.** If you tweak `pacenotes.txt` by hand, use Re-render to regenerate the HTML without any API calls.
- **Edit in the viewer.** The HTML viewer is fully editable — double-click any note to fix it on the fly, then hit Save to download the updated file.

---

## Cost

Both APIs have generous free tiers:

- **AssemblyAI** — free tier includes several hours of transcription per month
- **Google Gemini** — free tier via Google AI Studio; enable billing for higher limits

A typical 20-minute stage costs roughly $0.02–0.05 in API calls on paid tiers.

---

## Contributing

Pull requests are welcome. If you're a co-driver and want to improve the shorthand prompt for your region's conventions, start with `pacenotes_transcription_prompt.md` — that's where all the LLM instructions live.

If you find a video that produces bad results, opening an issue with the transcription excerpt (not the full video) helps a lot.

---

## License

MIT
