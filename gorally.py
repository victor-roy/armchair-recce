import os
import subprocess
import argparse
import yt_dlp
from faster_whisper import WhisperModel
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure Gemini API
assert "GEMINI_API_KEY" in os.environ
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

# Initialize the model
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

def download_youtube_audio(url, output_filename="temp_audio"):
    """Downloads the best audio stream from a YouTube URL and converts to WAV."""
    print(f"Downloading audio from YouTube: {url}")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_filename,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
        }],
        'postprocessor_args': [
            '-ar', '16000',  # 16kHz sample rate (ideal for Whisper)
            '-ac', '1'       # Mono audio
        ],
        'quiet': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    return f"{output_filename}.wav"

def extract_local_audio(video_path, output_filename="temp_audio.wav"):
    """Extracts audio from a local video file using FFmpeg."""
    print(f"Extracting audio from local file: {video_path}")
    command = [
        "ffmpeg", "-i", video_path,
        "-vn",                            # No video
        "-acodec", "pcm_s16le",           # 16-bit WAV
        "-ar", "16000",                   # 16kHz sample rate
        "-ac", "1",                       # Mono
        output_filename,
        "-y",                             # Overwrite if exists
        "-loglevel", "error"              # Keep console output clean
    ]
    subprocess.run(command, check=True)
    return output_filename

def translate_to_pacenotes(raw_text):
    """Translates raw spoken transcription to rally pace notes using Gemini."""
    prompt = f"""
    You are a professional rally co-driver. Convert the following spoken transcription 
    into standard, ultra-concise rally pace note shorthand. 
    Example: 'right five over crest' becomes 'R5 / Cr'.
    
    Transcription: {raw_text}
    """
    
    try:
        response = gemini_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"\n[Gemini API Error: {e}]")
        return raw_text

def transcribe_audio(audio_path):
    """Transcribes the audio using faster-whisper and prints timestamped segments."""
    print("Loading transcription model (this may take a moment on the first run)...")
    
    # "small.en" is a great balance of speed/accuracy for English on laptop CPUs. 
    # compute_type="int8" reduces memory usage heavily.
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    
    print(f"Transcribing {audio_path}...")
    # beam_size=5 helps accuracy but uses slightly more compute
    segments, info = model.transcribe(audio_path, beam_size=5)
    
    print(f"\n--- TRANSCRIPTION START (Language: {info.language}) ---\n")
    
    # Iterating through segments actually triggers the transcription process
    parsed_calls = []
    for segment in segments:
        shorthand_text = translate_to_pacenotes(segment.text)
        call_text = f"[{segment.start:.2f}s -> {segment.end:.2f}s] {shorthand_text}"
        print(call_text)
        parsed_calls.append({
            "start": segment.start,
            "end": segment.end,
            "text": shorthand_text
        })
        
    print("\n--- TRANSCRIPTION END ---")
    return parsed_calls

def main():
    parser = argparse.ArgumentParser(description="Extract and transcribe rally onboard audio.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--link", help="A YouTube URL to transcribe")
    group.add_argument("--path", help="A local video file path to transcribe")
    args = parser.parse_args()
    
    if "GEMINI_API_KEY" not in os.environ:
        print("Error: GEMINI_API_KEY environment variable is not set.")
        print("Please set it in your .env file like this: GEMINI_API_KEY='your_api_key_here'")
        return

    audio_file = None
    
    try:
        if args.link:
            audio_file = download_youtube_audio(args.link)
        elif args.path:
            if not os.path.exists(args.path):
                print(f"Error: Local file '{args.path}' not found.")
                return
            audio_file = extract_local_audio(args.path)
            
        # Run transcription
        transcription_data = transcribe_audio(audio_file)
        
    finally:
        # Cleanup the temporary audio file so it doesn't clutter your drive
        if audio_file and os.path.exists(audio_file):
            print(f"Cleaning up {audio_file}...")
            os.remove(audio_file)

if __name__ == "__main__":
    main()