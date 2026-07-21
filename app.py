import os
import uuid
import threading
import logging
from functools import wraps
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    send_from_directory,
    session,
    redirect,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from corecce import (
    get_youtube_title,
    download_youtube_audio,
    extract_local_audio,
    transcribe_and_diarize,
    translate_to_pacenotes,
    save_transcription,
    save_info,
    save_pacenotes_txt,
    save_pacenotes_to_html,
    _get_shorthand_list,
    QuotaError,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB upload limit
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-not-for-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not app.debug

INVITE_CODE = os.environ.get("INVITE_CODE")  # None = gate disabled (local dev)
RATE_LIMIT = os.environ.get("RATE_LIMIT", "10 per hour")

limiter = Limiter(get_remote_address, app=app, default_limits=[])


def _is_authed():
    """True if the session belongs to a Tier 1 (invite-code) or Tier 2 (BYOK) user."""
    return session.get("authenticated") or (
        bool(session.get("byok_assemblyai_key"))
        and bool(session.get("byok_gemini_key"))
    )


def get_api_keys():
    """Return (assemblyai_key, gemini_key) for the active session.

    Tier 1 (invite-code): reads owner's env vars.
    Tier 2 (BYOK): reads keys stored in the user's session.
    Local dev (no gate): both return None; corecce.py falls back to env vars.
    """
    if session.get("authenticated"):
        return (
            os.environ.get("ASSEMBLY_AI_API_KEY"),
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
        )
    return session.get("byok_assemblyai_key"), session.get("byok_gemini_key")


def _gate_ctx():
    """Template context dict shared by all routes that render index.html."""
    byok_active = bool(session.get("byok_assemblyai_key")) and bool(
        session.get("byok_gemini_key")
    )
    return {
        "gate_enabled": bool(INVITE_CODE),
        "authenticated": session.get("authenticated", False),
        "byok_active": byok_active,
    }


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if INVITE_CODE and not _is_authed():
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated


UPLOAD_DIR = os.path.join("outputs", "temp", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# job_id → { status: "running"|"done"|"error", message: str, result_url: str|None }
jobs = {}


def _set(job_id, message, status="running", result_url=None):
    jobs[job_id] = {"status": status, "message": message, "result_url": result_url}


def run_youtube(job_id, url, csv_path=None, assemblyai_key=None, gemini_key=None):
    try:
        shorthand_list = _get_shorthand_list(csv_path)

        _set(job_id, "Fetching video info...")
        title = get_youtube_title(url)
        output_dir = os.path.join("outputs", title)
        os.makedirs(output_dir, exist_ok=True)
        save_info(output_dir, title, source=url)

        _set(job_id, "Downloading audio...")
        audio_file = download_youtube_audio(url, output_dir)

        _set(job_id, "Transcribing the Co Driver \n(this may take a few minutes)...")
        transcription = transcribe_and_diarize(audio_file, api_key=assemblyai_key)
        save_transcription(transcription, output_dir)

        _set(job_id, "Generating pace notes...")
        pace_notes = translate_to_pacenotes(
            transcription, shorthand_list=shorthand_list, api_key=gemini_key
        )
        save_pacenotes_txt(pace_notes, output_dir)
        save_pacenotes_to_html(
            title, pace_notes, output_dir, source=url, shorthand_list=shorthand_list
        )

        _set(
            job_id,
            "Done!",
            status="done",
            result_url=f"/outputs/{title}/pacenotes.html",
        )
    except QuotaError as e:
        _set(job_id, str(e), status="error")
    except Exception as e:
        logging.exception("Job %s failed", job_id)
        _set(job_id, str(e), status="error")


def run_local_video(
    job_id, video_path, title, csv_path=None, assemblyai_key=None, gemini_key=None
):
    try:
        shorthand_list = _get_shorthand_list(csv_path)

        output_dir = os.path.join("outputs", title)
        os.makedirs(output_dir, exist_ok=True)
        save_info(output_dir, title, source=os.path.abspath(video_path))

        _set(job_id, "Extracting audio...")
        audio_file = extract_local_audio(video_path, output_dir)

        _set(job_id, "Transcribing the Co Driver (this may take a few minutes)...")
        transcription = transcribe_and_diarize(audio_file, api_key=assemblyai_key)
        save_transcription(transcription, output_dir)

        _set(job_id, "Generating pace notes...")
        pace_notes = translate_to_pacenotes(
            transcription, shorthand_list=shorthand_list, api_key=gemini_key
        )
        save_pacenotes_txt(pace_notes, output_dir)
        save_pacenotes_to_html(
            title, pace_notes, output_dir, shorthand_list=shorthand_list
        )

        _set(
            job_id,
            "Done!",
            status="done",
            result_url=f"/outputs/{title}/pacenotes.html",
        )
    except QuotaError as e:
        _set(job_id, str(e), status="error")
    except Exception as e:
        logging.exception("Job %s failed", job_id)
        _set(job_id, str(e), status="error")


def run_transcription_file(
    job_id, transcription_path, title, csv_path=None, gemini_key=None
):
    try:
        shorthand_list = _get_shorthand_list(csv_path)

        output_dir = os.path.join("outputs", title)
        os.makedirs(output_dir, exist_ok=True)

        _set(job_id, "Loading transcription...")
        with open(transcription_path, "r") as f:
            transcription = f.read()

        _set(job_id, "Generating pace notes...")
        pace_notes = translate_to_pacenotes(
            transcription, shorthand_list=shorthand_list, api_key=gemini_key
        )
        save_pacenotes_txt(pace_notes, output_dir)
        save_pacenotes_to_html(
            title, pace_notes, output_dir, shorthand_list=shorthand_list
        )

        _set(
            job_id,
            "Done!",
            status="done",
            result_url=f"/outputs/{title}/pacenotes.html",
        )
    except QuotaError as e:
        _set(job_id, str(e), status="error")
    except Exception as e:
        logging.exception("Job %s failed", job_id)
        _set(job_id, str(e), status="error")


def run_rerender(job_id, pacenotes_path, title, csv_path=None):
    try:
        shorthand_list = _get_shorthand_list(csv_path)

        output_dir = os.path.join("outputs", title)
        os.makedirs(output_dir, exist_ok=True)

        _set(job_id, "Loading pace notes...")
        with open(pacenotes_path, "r") as f:
            pace_notes = f.read()

        source = None
        info_path = os.path.join(output_dir, "info.txt")
        if os.path.exists(info_path):
            for line in open(info_path):
                if line.startswith("source:"):
                    source = line.split(":", 1)[1].strip()

        _set(job_id, "Rendering HTML...")
        save_pacenotes_to_html(
            title, pace_notes, output_dir, source=source, shorthand_list=shorthand_list
        )

        _set(
            job_id,
            "Done!",
            status="done",
            result_url=f"/outputs/{title}/pacenotes.html",
        )
    except QuotaError as e:
        _set(job_id, str(e), status="error")
    except Exception as e:
        logging.exception("Job %s failed", job_id)
        _set(job_id, str(e), status="error")


@app.route("/")
def index():
    return render_template("index.html", **_gate_ctx())


@app.route("/auth", methods=["POST"])
def auth():
    code = request.form.get("invite_code", "").strip()
    if INVITE_CODE and code == INVITE_CODE:
        session["authenticated"] = True
        return redirect(url_for("index"))
    return render_template("index.html", **_gate_ctx(), auth_error=True)


@app.route("/byok", methods=["POST"])
def byok():
    assemblyai_key = request.form.get("assemblyai_key", "").strip()
    gemini_key = request.form.get("gemini_key", "").strip()
    if not assemblyai_key or not gemini_key:
        return render_template("index.html", **_gate_ctx(), byok_error=True)
    session["byok_assemblyai_key"] = assemblyai_key
    session["byok_gemini_key"] = gemini_key
    return redirect(url_for("index"))


@app.route("/signout", methods=["POST"])
def signout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/process", methods=["POST"])
@login_required
@limiter.limit(RATE_LIMIT)
def process():
    mode = request.form.get("mode")
    job_id = str(uuid.uuid4())
    _set(job_id, "Starting...")
    assemblyai_key, gemini_key = get_api_keys()

    # Optional shorthand upload (CSV / XLSX / XLS) — saved to disk for background thread
    csv_path = None
    csv_file = request.files.get("csv")
    if csv_file and csv_file.filename:
        data = csv_file.read()
        if len(data) > 1 * 1024 * 1024:
            return jsonify({"error": "Shorthand file must be under 1 MB"}), 400
        ext = os.path.splitext(csv_file.filename)[1].lower() or ".csv"
        csv_save_path = os.path.join(UPLOAD_DIR, f"{job_id}_shorthand{ext}")
        with open(csv_save_path, "wb") as fh:
            fh.write(data)
        csv_path = csv_save_path

    if mode == "youtube":
        url = request.form.get("url", "").strip()
        if not url:
            return jsonify({"error": "No URL provided"}), 400
        threading.Thread(
            target=run_youtube,
            args=(job_id, url, csv_path, assemblyai_key, gemini_key),
            daemon=True,
        ).start()

    elif mode == "local":
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "No file provided"}), 400
        title = os.path.splitext(f.filename)[0]
        save_path = os.path.join(UPLOAD_DIR, f.filename)
        f.save(save_path)
        threading.Thread(
            target=run_local_video,
            args=(job_id, save_path, title, csv_path, assemblyai_key, gemini_key),
            daemon=True,
        ).start()

    elif mode == "transcription":
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "No file provided"}), 400
        title = request.form.get("title", "").strip() or os.path.splitext(f.filename)[0]
        save_path = os.path.join(UPLOAD_DIR, f.filename)
        f.save(save_path)
        threading.Thread(
            target=run_transcription_file,
            args=(job_id, save_path, title, csv_path, gemini_key),
            daemon=True,
        ).start()

    elif mode == "rerender":
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "No file provided"}), 400
        title = request.form.get("title", "").strip() or os.path.splitext(f.filename)[
            0
        ].replace("_pacenotes", "")
        save_path = os.path.join(UPLOAD_DIR, f.filename)
        f.save(save_path)
        threading.Thread(
            target=run_rerender, args=(job_id, save_path, title, csv_path), daemon=True
        ).start()

    else:
        return jsonify({"error": "Unknown mode"}), 400

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Job not found — server may have restarted. Please try again.",
                }
            ),
            404,
        )
    return jsonify(job)


@app.route("/outputs/<path:filepath>")
@login_required
def serve_output(filepath):
    return send_from_directory("outputs", filepath)


@app.route("/download/shorthand-template.csv")
def download_shorthand_template():
    return send_from_directory(
        ".",
        "pacenotes_shorthand.csv",
        as_attachment=True,
        download_name="shorthand-template.csv",
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
