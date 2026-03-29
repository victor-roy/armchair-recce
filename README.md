# CoRecce

Turn rally onboard videos into printable, interactive pace notes — in minutes.

CoRecce takes a YouTube link or local video file, isolates the co-driver's calls using speaker diarization, and uses an LLM to convert them into clean rally shorthand. The result is an interactive HTML pace note viewer you can use on a tablet in the car, or print and cut for a real stage.

---

## How it works

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
git clone https://github.com/victor-roy/armchair-recce.git
cd armchair-recce
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

All processing tabs include an optional **Shorthand CSV** file picker (see below). The transcription + LLM step typically takes **2–5 minutes** depending on video length. Progress is shown in real time.

### Command line

```bash
# From a YouTube link
python corecce.py --link "https://www.youtube.com/watch?v=..."

# From a local video file
python corecce.py --path "/path/to/video.mp4"

# From an existing transcription
python corecce.py --transcription-file outputs/MyStage/transcription.txt

# Re-render HTML from existing pace notes
python corecce.py --rerender outputs/MyStage/pacenotes.txt

# With a custom shorthand CSV
python corecce.py --link "..." --shorthand-csv my_shorthand.csv
```

---

## Shorthand CSV

CoRecce uses a CSV file to define the shorthand vocabulary and severity highlighting. The default file is `pacenotes_shorthand.csv` in the project root and is loaded automatically on every run.

### Format

The CSV must have exactly these three columns:

| Column | Description |
|--------|-------------|
| `Note` | The spoken word or phrase (e.g. `caution`, `off camber`) |
| `Shorthand` | The shorthand to output (e.g. `!`, `Offc`) |
| `Severity` | `1`, `2`, `3`, or blank — controls highlight colour in the viewer |

Example:

```csv
Note,Shorthand,Severity
Caution,!,1
Dont,Dont,2
Care,Care,2
Tightens,>,3
Jump,Jmp,3
Slippy,$,2
```

### Severity colours

| Severity | Colour | Use for |
|----------|--------|---------|
| 1 | Bright red | High danger (caution, don't cut) |
| 2 | Orange | Moderate warnings (care, slippy, narrow) |
| 3 | Peach | Attention notes (tightens, brake, jump) |
| *(blank)* | No highlight | Neutral shorthand (kinks, crest, etc.) |

### Casing convention (small-caps rendering)

The viewer renders all note text in **small-caps**, so letter case in your shorthand values controls visual size:

- **First letter uppercase, rest lowercase** → first letter is a large cap, rest are small caps (e.g. `Care`, `Dont`, `Decept`)
- **All uppercase** → all letters appear as full large caps (e.g. `HP`, `IN`)

### Using a custom CSV

- **Web app:** use the "Shorthand CSV (optional)" file picker on any processing tab to upload a CSV that overrides the default for that run.
- **CLI:** pass `--shorthand-csv /path/to/my.csv`

The list is **definitive**: every term found in the `Note` column is translated to its `Shorthand`. Terms not in the list are kept as transcribed, unless they match a known homophone or transcription error.

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

CoRecce uses standard international rally shorthand conventions:

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
| `Care` | Take care |
| `Dont` | Do not cut |
| `m` | Metres (distance marker) |

### Line grouping

Notes are grouped onto lines following these rules:

- Maximum **3** corners per line when all are plain
- Maximum **2** corners per line when any has a modifier (`>`, `<`, short, slippy, sharp, etc.)
- Distance numbers (30, 50, 75, 100…) appear at the **end** of the line they belong to, signalling how far to the next call:

```
R5 / Cr
4L >  30
R2
L5 R6 L6
! Dont
```

---

## Tips for best results

- **Video quality matters.** Onboards with a clear co-driver microphone (not just cabin audio) produce significantly better transcriptions.
- **Review the transcription first.** Open `transcription.txt` after the transcription step — if the co-driver wasn't correctly isolated, you can manually clean it and use the *Transcription File* tab to regenerate pace notes without re-transcribing.
- **Use Re-render freely.** If you tweak `pacenotes.txt` by hand, use Re-render to regenerate the HTML without any API calls. Your shorthand CSV will be re-applied.
- **Edit in the viewer.** The HTML viewer is fully editable — double-click any note to fix it on the fly, then hit Save to download the updated file.
- **Tune your CSV.** Edit `pacenotes_shorthand.csv` to match your co-driver's vocabulary. The CSV is the single source of truth for what gets translated and how it's highlighted.

---

## Cost

Both APIs have generous free tiers:

- **AssemblyAI** — free tier includes several hours of transcription per month
- **Google Gemini** — free tier via Google AI Studio; enable billing for higher limits

A typical 20-minute stage costs roughly $0.02–0.05 in API calls on paid tiers.

---

## Contributing

Pull requests are welcome.

### Local setup for contributors

Install development dependencies and set up pre-commit hooks:

```bash
pip install pre-commit
pre-commit install
```

Hooks will now run automatically on every `git commit`. To run them manually against all files:

```bash
pre-commit run --all-files
```

### CI enforcement

A GitHub Actions workflow runs the same pre-commit checks on every push and pull request. PRs must pass this check before merging — see `.github/workflows/pre-commit.yml`.

To enable branch protection locally after forking, go to **Settings → Branches → Add rule** for `main`, enable *Require status checks to pass*, and add `pre-commit` as a required check.

### Where to start

- **Shorthand prompt** — if you want to improve transcription quality or adapt shorthand for your region's conventions, start with `pacenotes_transcription_prompt.md`.
- **Bug reports** — if a video produces bad results, open an issue with the relevant excerpt from `transcription.txt` (not the full video URL).

---

## License

MIT
