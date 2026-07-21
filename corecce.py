import argparse
import csv
import logging
import os
import re
import subprocess

import assemblyai as aai
from dotenv import load_dotenv
from google import genai
from youtube import get_youtube_title, download_youtube_audio


class QuotaError(RuntimeError):
    """Raised when an external API quota or rate limit is exceeded."""


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logging.getLogger("google.genai").setLevel(logging.WARNING)

load_dotenv()


GEMINI_MODEL = "gemini-2.5-flash"
PROMPT_PATH = "pacenotes_transcription_prompt.md"
DEFAULT_CSV_PATH = "pacenotes_shorthand.csv"
MAX_AUDIO_DURATION_SECONDS = 30 * 60  # 30 minutes

# JS regex special characters that need escaping
_JS_REGEX_SPECIAL = set(r"\^$.|?*+()[]{/")


def _escape_for_js_regex(s):
    result = []
    for ch in s:
        if ch in _JS_REGEX_SPECIAL:
            result.append("\\" + ch)
        else:
            result.append(ch)
    return "".join(result)


def _shorthand_to_js_pattern(shorthand):
    escaped = _escape_for_js_regex(shorthand)
    if re.match(r"^\w+$", shorthand):
        return f"/\\b{escaped}\\b/gi"
    return f"/{escaped}/g"


def _parse_shorthand_rows(raw_rows):
    """
    Convert an iterable of dicts (with Note/Shorthand/Severity keys) into
    the internal format. Skips blank or malformed rows.
    """
    rows = []
    for row in raw_rows:
        note = str(row.get("Note") or "").strip()
        shorthand = str(row.get("Shorthand") or "").strip()
        severity_raw = str(row.get("Severity") or "").strip()
        if not note or not shorthand:
            continue
        try:
            severity = int(severity_raw) if severity_raw else None
        except ValueError:
            severity = None
        rows.append({"note": note, "shorthand": shorthand, "severity": severity})
    return rows


def load_shorthand_csv(path):
    """
    Load a shorthand file (CSV, XLSX, or XLS) with columns: Note, Shorthand, Severity.
    Reads the first sheet for Excel files. Returns a list of dicts.
    """
    if not path or not os.path.exists(path):
        return []
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".xlsx":
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            headers = [
                str(c.value).strip() if c.value is not None else ""
                for c in next(ws.iter_rows(min_row=1, max_row=1))
            ]
            raw = [
                dict(
                    zip(
                        headers,
                        [
                            str(c.value).strip() if c.value is not None else ""
                            for c in row
                        ],
                    )
                )
                for row in ws.iter_rows(min_row=2)
            ]
            wb.close()
        elif ext == ".xls":
            import xlrd

            wb = xlrd.open_workbook(path)
            ws = wb.sheet_by_index(0)
            headers = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]
            raw = [
                dict(
                    zip(
                        headers,
                        [str(ws.cell_value(r, c)).strip() for c in range(ws.ncols)],
                    )
                )
                for r in range(1, ws.nrows)
            ]
        else:
            with open(path, newline="", encoding="utf-8") as f:
                raw = list(csv.DictReader(f))
    except Exception as e:
        raise ValueError(f"Could not read shorthand file: {e}") from e
    rows = _parse_shorthand_rows(raw)
    logging.info(f"Loaded {len(rows)} shorthand entries from {path}")
    return rows


def _get_shorthand_list(csv_path=None):
    """Load from the given path, or fall back to the default CSV if it exists."""
    path = csv_path if csv_path and os.path.exists(csv_path) else DEFAULT_CSV_PATH
    return load_shorthand_csv(path)


def _build_shorthand_prompt_table(shorthand_list):
    """Render shorthand_list as a markdown table for injection into the prompt."""
    if not shorthand_list:
        return "(no custom shorthand list provided — use built-in mappings only)"
    lines = ["| Note | Shorthand |", "|------|-----------|"]
    for entry in shorthand_list:
        lines.append(f"| {entry['note']} | {entry['shorthand']} |")
    return "\n".join(lines)


def _build_csv_highlights_js(shorthand_list):
    """
    Generate JS highlight entries for all CSV rows that have a severity.
    Deduplicates by (shorthand, severity). Returns a string of JS lines
    to replace the // {{CSV_HIGHLIGHTS}} placeholder.
    """
    seen = set()
    lines = []
    for entry in shorthand_list:
        sev = entry["severity"]
        if sev not in (1, 2, 3):
            continue
        shorthand = entry["shorthand"]
        key = (shorthand, sev)
        if key in seen:
            continue
        seen.add(key)
        pattern = _shorthand_to_js_pattern(shorthand)
        lines.append(f"        {{ pattern: {pattern}, cls: 'sev-{sev}' }},")
    return "\n".join(lines)


def extract_local_audio(video_path, output_dir):
    logging.info(f"Extracting audio from: {video_path}")
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        duration = float(probe.stdout.strip())
        if duration > MAX_AUDIO_DURATION_SECONDS:
            raise ValueError(
                f"Video is {int(duration // 60)} min long — maximum is {MAX_AUDIO_DURATION_SECONDS // 60} minutes."  # noqa: E501
            )
        logging.info(f"Duration: {int(duration // 60)}m {int(duration % 60)}s")
    audio_path = os.path.join(output_dir, "audio.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            audio_path,
            "-y",
            "-loglevel",
            "error",
        ],
        check=True,
    )
    return audio_path


def transcribe_and_diarize(audio_path, *, api_key=None):
    """
    Upload audio to AssemblyAI and transcribe without speaker filtering.
    All speech is included — the LLM prompt filters out non-pacenote content.
    api_key: AssemblyAI key; falls back to ASSEMBLY_AI_API_KEY env var if omitted.
    """
    aai_key = api_key or os.environ.get("ASSEMBLY_AI_API_KEY")
    if not aai_key:
        raise ValueError(
            "No AssemblyAI API key provided. Set ASSEMBLY_AI_API_KEY or pass api_key."
        )
    logging.info("Uploading audio to AssemblyAI...")
    config = aai.TranscriptionConfig(
        speech_models=["universal-2"],
        punctuate=True,
        format_text=True,
    )

    transcriber = aai.Transcriber(api_key=aai_key)
    logging.info("Transcribing (this runs in the cloud)...")
    transcript = transcriber.transcribe(audio_path, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        msg = transcript.error or ""
        if (
            "rate limit" in msg.lower()
            or "quota" in msg.lower()
            or "limit exceeded" in msg.lower()
        ):
            raise QuotaError(
                "AssemblyAI transcription limit reached — please try again later."
            )
        raise RuntimeError(f"AssemblyAI transcription failed: {msg}")

    # Group all words into segments by pause threshold
    PAUSE_THRESHOLD_MS = 400  # new segment when gap between words exceeds this

    words = [w for w in transcript.words if w.text]

    lines = []
    if words:
        seg_words = [words[0]]
        for word in words[1:]:
            gap = word.start - seg_words[-1].end
            if gap > PAUSE_THRESHOLD_MS:
                start = seg_words[0].start / 1000
                end = seg_words[-1].end / 1000
                text = " ".join(w.text for w in seg_words)
                lines.append(f"[{start:.2f}s -> {end:.2f}s] {text}")
                seg_words = [word]
            else:
                seg_words.append(word)
        # flush last segment
        start = seg_words[0].start / 1000
        end = seg_words[-1].end / 1000
        text = " ".join(w.text for w in seg_words)
        lines.append(f"[{start:.2f}s -> {end:.2f}s] {text}")

    logging.info(
        f"Produced {len(lines)} segments (pause threshold: {PAUSE_THRESHOLD_MS}ms)"
    )
    return "\n".join(lines)


def translate_to_pacenotes(
    codriver_transcription, shorthand_list=None, *, api_key=None
):
    """
    Convert a co-driver transcription into rally pace notes using Gemini.

    codriver_transcription: timestamped text output from transcribe_and_diarize().
    shorthand_list: list of dicts from load_shorthand_csv(); injected into the prompt.
    api_key: Gemini key; falls back to GEMINI_API_KEY / GOOGLE_API_KEY env var if omitted.
    """
    gemini_key = (
        api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    )
    if not gemini_key:
        raise ValueError(
            "No Gemini API key provided. Set GEMINI_API_KEY or pass api_key."
        )
    if not os.path.exists(PROMPT_PATH):
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_PATH}")
    with open(PROMPT_PATH, "r") as f:
        prompt = f.read()

    shorthand_table = _build_shorthand_prompt_table(shorthand_list or [])
    prompt = prompt.replace("{{SHORTHAND_CSV}}", shorthand_table)
    prompt = prompt.replace("{{TRANSCRIPTION}}", codriver_transcription)

    client = genai.Client(api_key=gemini_key)
    logging.info("Sending to Gemini for pace note conversion...")
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    except Exception as e:
        name = type(e).__name__
        msg = str(e).lower()
        if (
            "resourceexhausted" in name
            or "429" in msg
            or "quota" in msg
            or "rate limit" in msg
        ):
            raise QuotaError(
                "Gemini API limit reached — please try again later."
            ) from e
        raise
    return response.text.strip()


def save_transcription(transcription, output_dir):
    output_path = os.path.join(output_dir, "transcription.txt")
    with open(output_path, "w") as f:
        f.write(transcription)
    logging.info(f"Transcription saved to {output_path}")


def save_info(output_dir, title, source=None):
    info_path = os.path.join(output_dir, "info.txt")
    with open(info_path, "w") as f:
        f.write(f"title: {title}\n")
        if source:
            f.write(f"source: {source}\n")
    logging.info(f"Info saved to {info_path}")


def save_pacenotes_txt(pace_notes, output_dir):
    output_path = os.path.join(output_dir, "pacenotes.txt")
    with open(output_path, "w") as f:
        f.write(pace_notes)
    logging.info(f"Pace notes text saved to {output_path}")
    return output_path


def save_pacenotes_to_html(
    title,
    pace_notes,
    output_dir,
    source=None,
    shorthand_list=None,
    template_path="template.html",
):
    output_filename = os.path.join(output_dir, "pacenotes.html")
    with open(template_path, "r") as f:
        html_content = f.read()

    html_content = html_content.replace("Rally Pace Notes", title)

    if source and "youtube.com" in source or source and "youtu.be" in source:
        yt_link = f' <a href="{source}" target="_blank" title="Open on YouTube" style="color:var(--muted);text-decoration:none;font-size:0.85em;" onmouseover="this.style.color=\'var(--accent)\'" onmouseout="this.style.color=\'var(--muted)\'">&#10138;</a>'  # noqa: E501
    else:
        yt_link = ""
    html_content = html_content.replace("<!--YOUTUBE_LINK_PLACEHOLDER-->", yt_link)

    # Inject severity highlight patterns from the CSV
    highlights_js = _build_csv_highlights_js(shorthand_list or [])
    html_content = html_content.replace("        // {{CSV_HIGHLIGHTS}}", highlights_js)

    notes_html = "".join(
        f'<div class="pacenote">{note}</div>'
        for note in pace_notes.splitlines()
        if note.strip()
    )
    html_content = html_content.replace("<!-- PACENOTES_PLACEHOLDER -->", notes_html)

    with open(output_filename, "w") as f:
        f.write(html_content)
    logging.info(f"Pace notes HTML saved to {output_filename}")


def main():
    parser = argparse.ArgumentParser(
        description="Rally onboard → printable pace notes."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--link", "-l", help="YouTube URL")
    group.add_argument("--path", "-p", help="Local video file path")
    group.add_argument(
        "--transcription-file", "-tf", help="Existing co-driver transcription file"
    )
    group.add_argument(
        "--rerender", "-rr", help="Re-render HTML from an existing _pacenotes.txt file"
    )
    parser.add_argument(
        "--shorthand-csv",
        "-sc",
        help="Path to shorthand CSV (Note, Shorthand, Severity)",
    )
    args = parser.parse_args()

    shorthand_list = _get_shorthand_list(args.shorthand_csv)

    # ── Re-render shortcut ───────────────────────────────────────────────────
    if args.rerender:
        pacenotes_file = args.rerender
        if not os.path.exists(pacenotes_file):
            logging.error(f"File not found: {pacenotes_file}")
            return
        output_dir = os.path.dirname(os.path.abspath(pacenotes_file))
        title = os.path.basename(output_dir)
        with open(pacenotes_file, "r") as f:
            pace_notes = f.read()
        info_path = os.path.join(output_dir, "info.txt")
        source = None
        if os.path.exists(info_path):
            for line in open(info_path):
                if line.startswith("source:"):
                    source = line.split(":", 1)[1].strip()
        save_pacenotes_to_html(
            title, pace_notes, output_dir, source=source, shorthand_list=shorthand_list
        )
        return

    # ── Full pipeline ────────────────────────────────────────────────────────
    title = None
    source = None
    audio_file = None

    if args.link:
        source = args.link
        title = get_youtube_title(args.link)
    elif args.path:
        source = os.path.abspath(args.path)
        title = os.path.splitext(os.path.basename(args.path))[0]
    elif args.transcription_file:
        if not os.path.exists(args.transcription_file):
            logging.error(f"File not found: {args.transcription_file}")
            return
        title = os.path.basename(
            os.path.dirname(os.path.abspath(args.transcription_file))
        )

    if not title:
        logging.error("Could not determine title.")
        return

    output_dir = os.path.join("outputs", title)
    os.makedirs(output_dir, exist_ok=True)
    logging.info(f"Outputs → {output_dir}")
    save_info(output_dir, title, source)

    if args.transcription_file:
        logging.info(f"Loading transcription from {args.transcription_file}...")
        with open(args.transcription_file, "r") as f:
            transcription = f.read()
    else:
        if args.link:
            audio_file = download_youtube_audio(args.link, output_dir)
        else:
            audio_file = extract_local_audio(args.path, output_dir)
        transcription = transcribe_and_diarize(audio_file)
        logging.info("Transcription complete.")
        save_transcription(transcription, output_dir)

    if not transcription.strip():
        logging.warning("Transcription is empty — nothing to generate.")
        return

    pace_notes = translate_to_pacenotes(transcription, shorthand_list=shorthand_list)
    save_pacenotes_txt(pace_notes, output_dir)
    save_pacenotes_to_html(
        title, pace_notes, output_dir, source=source, shorthand_list=shorthand_list
    )


if __name__ == "__main__":
    main()
