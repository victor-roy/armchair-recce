import logging
import os
import subprocess
import argparse
from collections import defaultdict

import assemblyai as aai
import yt_dlp
from google import genai
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

assert "GEMINI_API_KEY" in os.environ or "GOOGLE_API_KEY" in os.environ
assert "ASSEMBLY_AI_API_KEY" in os.environ, "ASSEMBLY_AI_API_KEY not set in .env"

aai.settings.api_key = os.environ["ASSEMBLY_AI_API_KEY"]
genai_client = genai.Client()

GEMINI_MODEL = 'gemini-2.5-flash'
TEMP_DIR = os.path.join("outputs", "temp")

PROMPT_PATH = "pacenotes_transcription_prompt.md"



def download_youtube_audio(url):
    logging.info(f"Fetching video info: {url}")
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'youtube_video')

    from yt_dlp.utils import sanitize_filename
    sanitized_title = sanitize_filename(title)
    logging.info(f"Title: {title}")

    os.makedirs(TEMP_DIR, exist_ok=True)
    output_path = os.path.join(TEMP_DIR, f"{sanitized_title}.wav")
    cmd = [
        "yt-dlp",
        "--force-ipv4",
        "-f", "bestaudio/best",
        "-o", os.path.join(TEMP_DIR, f"{sanitized_title}.%(ext)s"),
        "--extract-audio",
        "--audio-format", "wav",
        "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
        "--quiet",
        url,
    ]
    logging.info("Downloading audio...")
    subprocess.run(cmd, check=True)

    return sanitized_title, output_path


def extract_local_audio(video_path):
    logging.info(f"Extracting audio from: {video_path}")
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    os.makedirs(TEMP_DIR, exist_ok=True)
    output_path = os.path.join(TEMP_DIR, f"{base_name}.wav")
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_path, "-y", "-loglevel", "error"
    ], check=True)
    return base_name, output_path


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

    # Tally speaking time per speaker
    speaker_times = defaultdict(float)
    for utt in transcript.utterances:
        speaker_times[utt.speaker] += (utt.end - utt.start)

    logging.info("Speaker breakdown:")
    for speaker, ms in sorted(speaker_times.items(), key=lambda x: -x[1]):
        logging.info(f"  Speaker {speaker}: {ms/1000:.1f}s")

    codriver = max(speaker_times, key=speaker_times.get)
    logging.info(f"Co-driver identified as: Speaker {codriver}")

    lines = []
    for utt in transcript.utterances:
        if utt.speaker == codriver:
            start = utt.start / 1000
            end = utt.end / 1000
            lines.append(f"[{start:.2f}s -> {end:.2f}s] {utt.text.strip()}")

    return "\n".join(lines)


def translate_to_pacenotes(codriver_transcription):
    with open(PROMPT_PATH, "r") as f:
        prompt = f.read().replace("{{TRANSCRIPTION}}", codriver_transcription)
    logging.info("Sending to Gemini for pace note conversion...")
    response = genai_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text.strip()


def save_transcription(title, transcription, output_dir):
    output_path = os.path.join(output_dir, f"{title}_transcription.txt")
    with open(output_path, "w") as f:
        f.write(transcription)
    logging.info(f"Transcription saved to {output_path}")


def save_pacenotes_to_html(title, pace_notes, output_dir, template_path="template.html"):
    output_filename = os.path.join(output_dir, f"{title}_pacenotes.html")
    with open(template_path, "r") as f:
        html_content = f.read()

    html_content = html_content.replace("Rally Pace Notes", title)
    notes_html = ''.join(
        f'<div class="pacenote">{note}</div>'
        for note in pace_notes.splitlines()
        if note.strip()
    )
    html_content = html_content.replace("<!-- PACENOTES_PLACEHOLDER -->", notes_html)

    with open(output_filename, "w") as f:
        f.write(html_content)
    logging.info(f"Pace notes saved to {output_filename}")


def main():
    parser = argparse.ArgumentParser(description="Rally onboard → printable pace notes.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--link", "-l", help="YouTube URL")
    group.add_argument("--path", "-p", help="Local video file path")
    group.add_argument("--transcription-file", "-tf", help="Existing co-driver transcription file")
    args = parser.parse_args()

    audio_file = None
    title = None

    try:
        if args.link:
            title, audio_file = download_youtube_audio(args.link)
        elif args.path:
            title, audio_file = extract_local_audio(args.path)
        elif args.transcription_file:
            if not os.path.exists(args.transcription_file):
                logging.error(f"File not found: {args.transcription_file}")
                return
            title = os.path.splitext(
                os.path.basename(args.transcription_file)
            )[0].replace("_transcription", "")

        if not title:
            logging.error("Could not determine title.")
            return

        output_dir = os.path.join("outputs", title)
        os.makedirs(output_dir, exist_ok=True)
        logging.info(f"Outputs → {output_dir}")

        if args.transcription_file:
            logging.info(f"Loading transcription from {args.transcription_file}...")
            with open(args.transcription_file, 'r') as f:
                transcription = f.read()
        else:
            transcription = transcribe_and_diarize(audio_file)
            logging.info("Transcription complete.")
            save_transcription(title, transcription, output_dir)

        if not transcription.strip():
            logging.warning("Transcription is empty — nothing to generate.")
            return

        pace_notes = translate_to_pacenotes(transcription)
        logging.info("--- PACE NOTES ---\n" + pace_notes)
        save_pacenotes_to_html(title, pace_notes, output_dir)

    finally:
        if audio_file and os.path.exists(audio_file):
            logging.info(f"Cleaning up {audio_file}...")
            os.remove(audio_file)


if __name__ == "__main__":
    main()
