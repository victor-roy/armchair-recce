import csv
import logging
import os
import re
import subprocess
import argparse
from collections import defaultdict

import assemblyai as aai
import yt_dlp
from yt_dlp.utils import sanitize_filename
from google import genai
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("google.genai").setLevel(logging.WARNING)

load_dotenv()

assert "GEMINI_API_KEY" in os.environ or "GOOGLE_API_KEY" in os.environ
assert "ASSEMBLY_AI_API_KEY" in os.environ, "ASSEMBLY_AI_API_KEY not set in .env"

aai.settings.api_key = os.environ["ASSEMBLY_AI_API_KEY"]
genai_client = genai.Client()

GEMINI_MODEL = 'gemini-2.5-flash'
PROMPT_PATH = "pacenotes_transcription_prompt.md"
DEFAULT_CSV_PATH = "pacenotes_shorthand.csv"

# JS regex special characters that need escaping
_JS_REGEX_SPECIAL = set(r'\^$.|?*+()[]{/')


def _escape_for_js_regex(s):
    result = []
    for ch in s:
        if ch in _JS_REGEX_SPECIAL:
            result.append('\\' + ch)
        else:
            result.append(ch)
    return ''.join(result)


def _shorthand_to_js_pattern(shorthand):
    escaped = _escape_for_js_regex(shorthand)
    if re.match(r'^\w+$', shorthand):
        return f'/\\b{escaped}\\b/gi'
    return f'/{escaped}/g'


def load_shorthand_csv(csv_path):
    """
    Load a shorthand CSV with columns: Note, Shorthand, Severity.
    Returns a list of dicts with keys: note, shorthand, severity (int or None).
    Skips blank or malformed rows.
    """
    if not csv_path or not os.path.exists(csv_path):
        return []
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            note = row.get('Note', '').strip()
            shorthand = row.get('Shorthand', '').strip()
            severity_raw = row.get('Severity', '').strip()
            if not note or not shorthand:
                continue
            try:
                severity = int(severity_raw) if severity_raw else None
            except ValueError:
                severity = None
            rows.append({'note': note, 'shorthand': shorthand, 'severity': severity})
    logging.info(f"Loaded {len(rows)} shorthand entries from {csv_path}")
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
        sev = entry['severity']
        if sev not in (1, 2, 3):
            continue
        shorthand = entry['shorthand']
        key = (shorthand, sev)
        if key in seen:
            continue
        seen.add(key)
        pattern = _shorthand_to_js_pattern(shorthand)
        lines.append(f"        {{ pattern: {pattern}, cls: 'sev-{sev}' }},")
    return "\n".join(lines)


def get_youtube_title(url):
    logging.info(f"Fetching video info: {url}")
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'youtube_video')
    sanitized_title = sanitize_filename(title)
    logging.info(f"Title: {title}")
    return sanitized_title


def download_youtube_audio(url, output_dir):
    audio_path = os.path.join(output_dir, "audio.wav")
    cmd = [
        "yt-dlp",
        "--force-ipv4",
        "-f", "bestaudio/best",
        "-o", os.path.join(output_dir, "audio.%(ext)s"),
        "--extract-audio",
        "--audio-format", "wav",
        "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
        "--quiet",
        url,
    ]
    logging.info("Downloading audio...")
    subprocess.run(cmd, check=True)
    return audio_path


def extract_local_audio(video_path, output_dir):
    logging.info(f"Extracting audio from: {video_path}")
    audio_path = os.path.join(output_dir, "audio.wav")
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_path, "-y", "-loglevel", "error"
    ], check=True)
    return audio_path


def transcribe_and_diarize(audio_path):
    """
    Upload audio to AssemblyAI, transcribe with speaker diarization,
    then return only the co-driver's utterances (dominant speaker by time).
    """
    logging.info("Uploading audio to AssemblyAI...")
    config = aai.TranscriptionConfig(
        speech_models=["universal-2"],
        speaker_labels=True,
        speakers_expected=2,
        punctuate=True,
        format_text=True,
    )

    transcriber = aai.Transcriber()
    logging.info("Transcribing with speaker diarization (this runs in the cloud)...")
    transcript = transcriber.transcribe(audio_path, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")

    # Tally speaking time per speaker using word-level data
    speaker_times = defaultdict(float)
    for word in transcript.words:
        if word.speaker:
            speaker_times[word.speaker] += (word.end - word.start)

    logging.info("Speaker breakdown:")
    for speaker, ms in sorted(speaker_times.items(), key=lambda x: -x[1]):
        logging.info(f"  Speaker {speaker}: {ms/1000:.1f}s")

    codriver = max(speaker_times, key=speaker_times.get)
    logging.info(f"Co-driver identified as: Speaker {codriver}")

    # Group co-driver words into subtitle-level segments by pause threshold
    PAUSE_THRESHOLD_MS = 400  # new segment when gap between words exceeds this

    codriver_words = [w for w in transcript.words if w.speaker == codriver]

    lines = []
    if codriver_words:
        seg_words = [codriver_words[0]]
        for word in codriver_words[1:]:
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

    logging.info(f"Produced {len(lines)} segments (pause threshold: {PAUSE_THRESHOLD_MS}ms)")
    return "\n".join(lines)


def translate_to_pacenotes(codriver_transcription, shorthand_list=None):
    if not os.path.exists(PROMPT_PATH):
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_PATH}")
    with open(PROMPT_PATH, "r") as f:
        prompt = f.read()

    shorthand_table = _build_shorthand_prompt_table(shorthand_list or [])
    prompt = prompt.replace("{{SHORTHAND_CSV}}", shorthand_table)
    prompt = prompt.replace("{{TRANSCRIPTION}}", codriver_transcription)

    logging.info("Sending to Gemini for pace note conversion...")
    response = genai_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
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


def save_pacenotes_to_html(title, pace_notes, output_dir, source=None, shorthand_list=None,
                           template_path="template.html"):
    output_filename = os.path.join(output_dir, "pacenotes.html")
    with open(template_path, "r") as f:
        html_content = f.read()

    html_content = html_content.replace("Rally Pace Notes", title)

    if source and "youtube.com" in source or source and "youtu.be" in source:
        yt_link = f' <a href="{source}" target="_blank" title="Open on YouTube" style="color:var(--muted);text-decoration:none;font-size:0.85em;" onmouseover="this.style.color=\'var(--accent)\'" onmouseout="this.style.color=\'var(--muted)\'">&#10138;</a>'
    else:
        yt_link = ""
    html_content = html_content.replace("<!--YOUTUBE_LINK_PLACEHOLDER-->", yt_link)

    # Inject severity highlight patterns from the CSV
    highlights_js = _build_csv_highlights_js(shorthand_list or [])
    html_content = html_content.replace("        // {{CSV_HIGHLIGHTS}}", highlights_js)

    notes_html = ''.join(
        f'<div class="pacenote">{note}</div>'
        for note in pace_notes.splitlines()
        if note.strip()
    )
    html_content = html_content.replace("<!-- PACENOTES_PLACEHOLDER -->", notes_html)

    with open(output_filename, "w") as f:
        f.write(html_content)
    logging.info(f"Pace notes HTML saved to {output_filename}")


def main():
    parser = argparse.ArgumentParser(description="Rally onboard → printable pace notes.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--link", "-l", help="YouTube URL")
    group.add_argument("--path", "-p", help="Local video file path")
    group.add_argument("--transcription-file", "-tf", help="Existing co-driver transcription file")
    group.add_argument("--rerender", "-rr", help="Re-render HTML from an existing _pacenotes.txt file")
    parser.add_argument("--shorthand-csv", "-sc", help="Path to shorthand CSV (Note, Shorthand, Severity)")
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
        save_pacenotes_to_html(title, pace_notes, output_dir, source=source, shorthand_list=shorthand_list)
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
        with open(args.transcription_file, 'r') as f:
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
    save_pacenotes_to_html(title, pace_notes, output_dir, source=source, shorthand_list=shorthand_list)


if __name__ == "__main__":
    main()
