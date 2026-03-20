import logging
import os
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



def get_youtube_title(url):
    logging.info(f"Fetching video info: {url}")
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'youtube_video')
    sanitized_title = sanitize_filename(title)
    logging.info(f"Title: {title}")
    return sanitized_title


def download_youtube_audio(url, output_dir):
    sanitized_title = os.path.basename(output_dir)
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


def translate_to_pacenotes(codriver_transcription):
    if not os.path.exists(PROMPT_PATH):
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_PATH}")
    with open(PROMPT_PATH, "r") as f:
        prompt = f.read().replace("{{TRANSCRIPTION}}", codriver_transcription)
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


def save_pacenotes_to_html(title, pace_notes, output_dir, source=None, template_path="template.html"):
    output_filename = os.path.join(output_dir, "pacenotes.html")
    with open(template_path, "r") as f:
        html_content = f.read()

    html_content = html_content.replace("Rally Pace Notes", title)

    if source and "youtube.com" in source or source and "youtu.be" in source:
        yt_link = f' <a href="{source}" target="_blank" title="Open on YouTube" style="color:var(--muted);text-decoration:none;font-size:0.85em;" onmouseover="this.style.color=\'var(--accent)\'" onmouseout="this.style.color=\'var(--muted)\'">&#10138;</a>'
    else:
        yt_link = ""
    html_content = html_content.replace("<!--YOUTUBE_LINK_PLACEHOLDER-->", yt_link)

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
    args = parser.parse_args()

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
        save_pacenotes_to_html(title, pace_notes, output_dir, source=source)
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

    pace_notes = translate_to_pacenotes(transcription)
    logging.info("--- PACE NOTES ---\n" + pace_notes)
    save_pacenotes_txt(pace_notes, output_dir)
    save_pacenotes_to_html(title, pace_notes, output_dir, source=source)


if __name__ == "__main__":
    main()
