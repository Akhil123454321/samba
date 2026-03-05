from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import os
import sys
import json
import subprocess
import threading
import queue
import time
import logging
from datetime import datetime

import re

import numpy as np
import sounddevice as sd
import mlx_whisper
import requests as http_requests
import psutil

# Optional: mlx-lm for built-in summarization (Apple Silicon)
try:
    from mlx_lm import load as mlx_lm_load, generate as mlx_lm_generate
    _MLX_LM_AVAILABLE = True
except ImportError:
    _MLX_LM_AVAILABLE = False
    logging.warning("mlx-lm not installed — mlx summarization disabled.")

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# ── Path setup ─────────────────────────────────────────────────────────────────
# When running as a PyInstaller bundle, templates/static are inside sys._MEIPASS.
# User data (settings, todos, notes) always lives in ~/Library/Application Support/Samba
# so it survives app updates and isn't lost when the bundle is replaced.
if getattr(sys, "frozen", False):
    _BUNDLE_DIR = sys._MEIPASS
    _DATA_DIR = os.path.expanduser("~/Library/Application Support/Samba")
    os.makedirs(_DATA_DIR, exist_ok=True)
else:
    _BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    _DATA_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(_BUNDLE_DIR, "templates"),
    static_folder=os.path.join(_BUNDLE_DIR, "static"),
)
CORS(app)

BASE_DIR = _BUNDLE_DIR

# ── Audio config ───────────────────────────────────────────────────────────────
BLACKHOLE_DEVICE = 2
MIC_DEVICE = 3
SAMPLE_RATE = 48000
WHISPER_RATE = 16000
DOWNSAMPLE = SAMPLE_RATE // WHISPER_RATE
CHUNK_SECONDS = 10

MLX_MODEL = "mlx-community/whisper-large-v3-turbo"

# Whisper tends to hallucinate these on silence
HALLUCINATIONS = {
    "you", ".", "..", "...", "thank you.", "thanks for watching.",
    "[music]", "(music)", "[applause]", "bye.", "okay.", "hmm.", "bye!",
}

# Process name keywords that indicate an active meeting
MEETING_KEYWORDS = {"zoom", "teams", "webex", "chime"}

# ── Settings ───────────────────────────────────────────────────────────────────
SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")

_default_settings = {
    "notes_dir": os.path.join(_DATA_DIR, "notes"),
    "summarizer": "mlx",
    "mlx_model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "ollama_model": "llama3.2",
    "ollama_url": "http://localhost:11434",
    "notion_token": "",
    "notion_parent_id": "",
    "auto_start": False,
}

def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
            return {**_default_settings, **saved}
        except Exception:
            pass
    return dict(_default_settings)

def _save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

settings = _load_settings()

# ── Todos persistence ──────────────────────────────────────────────────────────
TODOS_FILE = os.path.join(_DATA_DIR, "todos.json")

def _load_todos():
    if os.path.exists(TODOS_FILE):
        try:
            with open(TODOS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _write_todos(todos):
    with open(TODOS_FILE, "w") as f:
        json.dump(todos, f, indent=2)

# ── Meeting state ──────────────────────────────────────────────────────────────
state = {
    "is_recording": False,
    "transcript": "",
    "summary": "",
    "action_items": [],
    "meeting_title": "",
    "started_at": None,
    "meeting_detected": False,
    "summarizing": False,
}

# ── Recording infrastructure ───────────────────────────────────────────────────
_bh_stream = None
_mic_stream = None
_transcription_thread = None
_stop_event = threading.Event()
_bh_queue = queue.Queue()
_mic_queue = queue.Queue()

# ── Whisper model ──────────────────────────────────────────────────────────────
_model_ready = threading.Event()


def _load_model():
    logging.info("Warming up mlx-whisper model...")
    mlx_whisper.transcribe(np.zeros(WHISPER_RATE, dtype=np.float32), path_or_hf_repo=MLX_MODEL)
    logging.info("Whisper model ready.")
    _model_ready.set()


logging.info("Loading Whisper model in background...")
threading.Thread(target=_load_model, daemon=True).start()

# ── Audio helpers ──────────────────────────────────────────────────────────────

def _bh_callback(indata, frames, time, status):
    _bh_queue.put(indata.copy())


def _mic_callback(indata, frames, time, status):
    _mic_queue.put(indata.copy())


def _normalize_audio(audio, target_rms=0.08, max_gain=20.0):
    """Boost quiet audio (e.g. Zoom) to a consistent level before transcription."""
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms > 0.001:
        gain = min(target_rms / rms, max_gain)
        return np.clip(audio * gain, -1.0, 1.0)
    return audio


def _has_speech(audio, threshold=0.003):
    return float(np.sqrt(np.mean(audio ** 2))) > threshold


def _is_repetitive(text):
    """Detect Whisper hallucination loops (same phrase repeated 3+ times)."""
    words = text.lower().split()
    if len(words) < 6:
        return False
    # Low unique-word ratio is a strong signal of a loop
    if len(set(words)) / len(words) < 0.35:
        return True
    return False


def _clean_text(text):
    t = text.strip()
    if not t or len(t) < 3:
        return ""
    if t.lower() in HALLUCINATIONS:
        return ""
    if _is_repetitive(t):
        logging.info("Filtered repetitive hallucination: %s", t[:60])
        return ""
    return t


def _rms_segment(audio, start_s, end_s):
    s = int(start_s * WHISPER_RATE)
    e = int(end_s * WHISPER_RATE)
    seg = audio[s:e]
    if len(seg) == 0:
        return 0.0
    return float(np.sqrt(np.mean(seg ** 2)))


def _resolve_speaker(seg_start, seg_end, bh_16k, mic_16k):
    """Return 'You', 'Meeting', or None based on mic vs BlackHole energy."""
    if mic_16k is not None:
        mic_rms = _rms_segment(mic_16k, seg_start, seg_end)
        bh_rms  = _rms_segment(bh_16k,  seg_start, seg_end)
        if mic_rms > bh_rms * 1.8:
            return "You"
        if bh_rms > mic_rms * 1.5:
            return "Meeting"
    return None


# ── Pending line buffer (speaker-change-driven flushing) ──────────────────────
# Accumulates one speaker's text across chunk boundaries until they stop talking.

_pending = {"speaker": None, "text": "", "ts": "", "since": 0.0}
MAX_PENDING_SECONDS = 30  # force-flush if a single speaker goes on for very long


def _flush_pending():
    if not _pending["text"].strip():
        return
    sp = _pending["speaker"]
    line = (f"{_pending['ts']} {sp}: {_pending['text'].strip()}"
            if sp else f"{_pending['ts']} {_pending['text'].strip()}")
    state["transcript"] += line + "\n"
    logging.info("Flushed: %s", line[:80])
    _pending["speaker"] = None
    _pending["text"] = ""
    _pending["ts"] = ""
    _pending["since"] = 0.0


def _feed_pending(speaker, text, ts):
    """Add a segment to the pending buffer, flushing if the speaker changes.
    None-speaker segments (pyannote gap / ambiguous) are appended to whatever
    is currently buffered rather than creating a stray fragment line."""
    now = time.time()
    timed_out = _pending["since"] > 0 and (now - _pending["since"]) > MAX_PENDING_SECONDS

    # Treat None as "continue with current speaker" to avoid fragment lines
    if speaker is None:
        if _pending["text"]:
            _pending["text"] += " " + text  # append to existing buffer silently
        # else: discard — nothing to attach to yet
        return

    same_speaker = (speaker == _pending["speaker"])
    if same_speaker and not timed_out:
        _pending["text"] += " " + text
    else:
        _flush_pending()
        _pending["speaker"] = speaker
        _pending["text"] = text
        _pending["ts"] = ts
        _pending["since"] = now


# ── Transcription worker ───────────────────────────────────────────────────────

def _transcription_worker():
    bh_buffer = []
    mic_buffer = []
    target_samples = SAMPLE_RATE * CHUNK_SECONDS
    chunk_start = 0
    logging.info("Transcription worker started.")

    while not _stop_event.is_set():
        try:
            bh_chunk = _bh_queue.get(timeout=0.5)
            bh_buffer.append(bh_chunk[:, 0])

            while not _mic_queue.empty():
                mic_buffer.append(_mic_queue.get_nowait()[:, 0])

            if sum(len(c) for c in bh_buffer) < target_samples:
                continue

            bh_audio = np.concatenate(bh_buffer)
            bh_buffer = []
            mic_audio = np.concatenate(mic_buffer) if mic_buffer else None
            mic_buffer = []

            chunk_offset = chunk_start
            chunk_start += CHUNK_SECONDS

            if not _model_ready.is_set():
                logging.warning("Model not ready, skipping chunk.")
                continue

            # Normalize each stream independently, keep separate for speaker ID
            bh_16k = _normalize_audio(bh_audio[::DOWNSAMPLE].astype(np.float32))
            mic_16k = _normalize_audio(mic_audio[::DOWNSAMPLE].astype(np.float32)) if mic_audio is not None else None

            # Mix for transcription (mic slightly louder to prioritise user's voice)
            if mic_16k is not None:
                n = min(len(bh_16k), len(mic_16k))
                mixed = np.clip(bh_16k[:n] * 0.6 + mic_16k[:n] * 0.8, -1.0, 1.0)
            else:
                mixed = bh_16k

            if not _has_speech(mixed):
                continue

            logging.info("Transcribing %.0fs chunk...", CHUNK_SECONDS)
            result = mlx_whisper.transcribe(mixed, path_or_hf_repo=MLX_MODEL)

            segments = result.get("segments", [])
            for seg in segments:
                text = _clean_text(seg.get("text", ""))
                if not text:
                    continue
                seg_start = seg.get("start", 0)
                seg_end = seg.get("end", seg_start + 1)
                speaker = _resolve_speaker(seg_start, seg_end, bh_16k, mic_16k)
                abs_s = chunk_offset + int(seg_start)
                ts = f"[{abs_s//3600:02d}:{(abs_s%3600)//60:02d}:{abs_s%60:02d}]"
                _feed_pending(speaker, text, ts)

        except queue.Empty:
            continue

    # Flush any remaining buffered text when recording stops
    _flush_pending()
    logging.info("Transcription worker stopped.")


# ── Meeting watcher ────────────────────────────────────────────────────────────

def _meeting_watcher():
    was_detected = False
    while True:
        try:
            names = {
                (p.info["name"] or "").lower()
                for p in psutil.process_iter(["name"])
            }
            detected = any(kw in name for name in names for kw in MEETING_KEYWORDS)
            state["meeting_detected"] = detected

            if detected and not was_detected:
                logging.info("Meeting app detected.")
                if settings["auto_start"] and not state["is_recording"]:
                    state["_auto_action"] = "start"

            elif not detected and was_detected:
                logging.info("Meeting app closed.")
                if settings["auto_start"] and state["is_recording"]:
                    state["_auto_action"] = "stop"

            was_detected = detected
        except Exception as e:
            logging.debug("Meeting watcher error: %s", e)

        time.sleep(5)


threading.Thread(target=_meeting_watcher, daemon=True).start()


# ── Summarization ──────────────────────────────────────────────────────────────

_mlx_lm_model = None
_mlx_lm_tokenizer = None
_mlx_lm_name = None
_mlx_lm_lock = threading.Lock()

_SUMMARIZE_PROMPT = (
    "You are a meeting assistant. Analyze this transcript and return ONLY valid JSON "
    "with two fields: \"summary\" (a concise 2-3 paragraph summary as a string) and "
    "\"action_items\" (a JSON array of strings). "
    "For action_items: ONLY include tasks, assignments, or follow-ups that were EXPLICITLY "
    "mentioned or agreed upon in the conversation. Do NOT invent or infer items not directly stated. "
    "If no action items were discussed, return an empty array []. "
    "No markdown, no explanation outside the JSON.\n\nTranscript:\n"
)


def _ensure_mlx_model(model_name):
    global _mlx_lm_model, _mlx_lm_tokenizer, _mlx_lm_name
    with _mlx_lm_lock:
        if _mlx_lm_name == model_name and _mlx_lm_model is not None:
            return True
        try:
            logging.info("Loading mlx-lm model: %s", model_name)
            _mlx_lm_model, _mlx_lm_tokenizer = mlx_lm_load(model_name)
            _mlx_lm_name = model_name
            logging.info("mlx-lm model loaded.")
            return True
        except Exception as exc:
            logging.error("Failed to load mlx-lm model %s: %s", model_name, exc)
            return False


def _summarize_with_mlx(transcript):
    model_name = settings.get("mlx_model", _default_settings["mlx_model"])
    if not _ensure_mlx_model(model_name):
        raise RuntimeError(f"Could not load mlx-lm model: {model_name}")
    prompt_text = _SUMMARIZE_PROMPT + transcript
    messages = [{"role": "user", "content": prompt_text}]
    if hasattr(_mlx_lm_tokenizer, "apply_chat_template") and _mlx_lm_tokenizer.chat_template is not None:
        formatted = _mlx_lm_tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    else:
        formatted = prompt_text
    with _mlx_lm_lock:
        raw = mlx_lm_generate(
            _mlx_lm_model, _mlx_lm_tokenizer,
            prompt=formatted, max_tokens=1024, verbose=False,
        )
    text = raw.strip()
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


def _summarize_with_ollama(transcript):
    model = settings["ollama_model"]
    url = f"{settings['ollama_url']}/api/generate"
    prompt = _SUMMARIZE_PROMPT + transcript
    resp = http_requests.post(
        url,
        json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
        timeout=120,
    )
    resp.raise_for_status()
    return json.loads(resp.json().get("response", "{}"))


def _summarize(transcript):
    """Dispatch to the configured summarization backend (mlx or ollama)."""
    backend = settings.get("summarizer", "mlx")
    if backend == "ollama":
        return _summarize_with_ollama(transcript)
    if not _MLX_LM_AVAILABLE:
        raise RuntimeError("mlx-lm not installed. Run: pip install mlx-lm")
    return _summarize_with_mlx(transcript)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_recording():
    global _bh_stream, _mic_stream, _transcription_thread

    data = request.get_json()
    state["is_recording"] = True
    state["transcript"] = ""
    state["summary"] = ""
    state["action_items"] = []
    state["meeting_title"] = data.get("title", "Untitled Meeting")
    state["started_at"] = datetime.now().isoformat()
    state.pop("_auto_action", None)
    _pending.update(speaker=None, text="", ts="", since=0.0)  # clear line buffer

    _stop_event.clear()
    for q in (_bh_queue, _mic_queue):
        while not q.empty():
            q.get_nowait()

    _bh_stream = sd.InputStream(
        device=BLACKHOLE_DEVICE, channels=1, samplerate=SAMPLE_RATE,
        blocksize=SAMPLE_RATE, callback=_bh_callback, dtype="float32",
    )
    _bh_stream.start()

    _mic_stream = sd.InputStream(
        device=MIC_DEVICE, channels=1, samplerate=SAMPLE_RATE,
        blocksize=SAMPLE_RATE, callback=_mic_callback, dtype="float32",
    )
    _mic_stream.start()

    _transcription_thread = threading.Thread(target=_transcription_worker, daemon=True)
    _transcription_thread.start()

    return jsonify({"status": "recording", "started_at": state["started_at"]})


@app.route("/api/stop", methods=["POST"])
def stop_recording():
    global _bh_stream, _mic_stream

    state["is_recording"] = False
    state.pop("_auto_action", None)
    _stop_event.set()

    for stream in (_bh_stream, _mic_stream):
        if stream is not None:
            stream.stop()
            stream.close()
    _bh_stream = None
    _mic_stream = None

    # Auto-summarize after a short delay (let last chunk finish)
    def _delayed_summarize():
        time.sleep(3)
        if state["transcript"].strip() and not state["summarizing"]:
            logging.info("Auto-summarizing...")
            state["summarizing"] = True
            try:
                result = _summarize(state["transcript"])
                state["summary"] = result.get("summary", "")
                state["action_items"] = result.get("action_items", [])
                logging.info("Auto-summary done.")
                if state["action_items"]:
                    todos = _load_todos()
                    todos.append({
                        "id": datetime.now().isoformat(),
                        "meeting_title": state["meeting_title"],
                        "date": datetime.now().strftime("%B %d, %Y %H:%M"),
                        "items": [{"text": t, "done": False} for t in state["action_items"]],
                    })
                    _write_todos(todos)
            except Exception as e:
                logging.error("Auto-summarization failed: %s", e)
            finally:
                state["summarizing"] = False

    threading.Thread(target=_delayed_summarize, daemon=True).start()

    return jsonify({"status": "stopped"})


@app.route("/api/status", methods=["GET"])
def get_status():
    auto_action = state.pop("_auto_action", None)
    return jsonify({
        "is_recording": state["is_recording"],
        "transcript": state["transcript"],
        "summary": state["summary"],
        "action_items": state["action_items"],
        "meeting_title": state["meeting_title"],
        "started_at": state["started_at"],
        "meeting_detected": state["meeting_detected"],
        "summarizing": state["summarizing"],
        "auto_action": auto_action,
    })


@app.route("/api/transcript", methods=["GET"])
def get_transcript():
    return jsonify({"transcript": state["transcript"]})


@app.route("/api/clear", methods=["POST"])
def clear_transcript():
    state["transcript"] = ""
    state["summary"] = ""
    state["action_items"] = []
    return jsonify({"status": "cleared"})


@app.route("/api/summary", methods=["GET"])
def get_summary():
    return jsonify({
        "summary": state["summary"],
        "action_items": state["action_items"],
        "summarizing": state["summarizing"],
    })


@app.route("/api/summary", methods=["POST"])
def generate_summary():
    if not state["transcript"].strip():
        return jsonify({"error": "No transcript to summarize"}), 400
    if state["summarizing"]:
        return jsonify({"error": "Already summarizing"}), 409

    def _run():
        state["summarizing"] = True
        try:
            result = _summarize(state["transcript"])
            state["summary"] = result.get("summary", "")
            state["action_items"] = result.get("action_items", [])
            logging.info("Summary generated.")
        except Exception as e:
            logging.error("Summarization failed: %s", e)
            state["summary"] = f"Summarization failed: {e}"
        finally:
            state["summarizing"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "generating"})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(settings)


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json()
    for key in ("notes_dir", "summarizer", "mlx_model", "ollama_model", "ollama_url", "notion_token", "notion_parent_id", "auto_start"):
        if key in data:
            settings[key] = data[key]
    _save_settings()
    return jsonify(settings)


@app.route("/api/clipboard", methods=["POST"])
def copy_to_clipboard():
    text = request.get_json().get("text", "")
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    return jsonify({"status": "ok"})


@app.route("/api/notion/push", methods=["POST"])
def push_to_notion():
    token = settings["notion_token"]
    parent_id = settings["notion_parent_id"].replace("-", "")
    if not token or not parent_id:
        return jsonify({"error": "Notion token and parent page ID are required in Settings"}), 400

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    def _heading(text):
        return {"object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

    def _paragraph(text):
        return {"object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}

    def _todo(text):
        return {"object": "block", "type": "to_do",
                "to_do": {"rich_text": [{"type": "text", "text": {"content": text}}], "checked": False}}

    children = []
    if state["summary"]:
        children += [_heading("Summary"), _paragraph(state["summary"])]
    if state["action_items"]:
        children.append(_heading("Action Items"))
        for item in state["action_items"]:
            children.append(_todo(item))
    if state["transcript"]:
        children.append(_heading("Transcript"))
        for i in range(0, len(state["transcript"]), 1900):
            children.append(_paragraph(state["transcript"][i:i + 1900]))

    page = {
        "parent": {"page_id": parent_id},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": state["meeting_title"] or "Meeting Notes"}}]}
        },
        "children": children,
    }

    resp = http_requests.post("https://api.notion.com/v1/pages", headers=headers, json=page, timeout=30)
    if resp.status_code != 200:
        logging.error("Notion error: %s", resp.text)
        return jsonify({"error": resp.json()}), resp.status_code

    url = resp.json().get("url", "")
    logging.info("Pushed to Notion: %s", url)
    return jsonify({"status": "pushed", "url": url})


@app.route("/api/todos", methods=["GET"])
def get_todos():
    return jsonify(_load_todos())


@app.route("/api/todos/<entry_id>/toggle/<int:item_idx>", methods=["POST"])
def toggle_todo(entry_id, item_idx):
    todos = _load_todos()
    for entry in todos:
        if entry["id"] == entry_id:
            if 0 <= item_idx < len(entry["items"]):
                entry["items"][item_idx]["done"] = not entry["items"][item_idx]["done"]
                _write_todos(todos)
                return jsonify({"done": entry["items"][item_idx]["done"]})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/todos/<entry_id>", methods=["DELETE"])
def delete_todo_meeting(entry_id):
    todos = [t for t in _load_todos() if t["id"] != entry_id]
    _write_todos(todos)
    return jsonify({"status": "deleted"})


@app.route("/api/save", methods=["POST"])
def save_notes():
    title = state["meeting_title"] or "meeting"
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_title}_{timestamp}.md"
    notes_dir = os.path.expanduser(settings["notes_dir"])
    os.makedirs(notes_dir, exist_ok=True)
    filepath = os.path.join(notes_dir, filename)
    with open(filepath, "w") as f:
        f.write(f"# {state['meeting_title']}\n")
        f.write(f"**Date:** {datetime.now().strftime('%B %d, %Y')}\n\n")
        if state["summary"]:
            f.write("## Summary\n\n")
            f.write(state["summary"] + "\n\n")
        if state["action_items"]:
            f.write("## Action Items\n\n")
            for item in state["action_items"]:
                f.write(f"- [ ] {item}\n")
            f.write("\n")
        if state["transcript"]:
            f.write("## Full Transcript\n\n")
            f.write(state["transcript"] + "\n")
    logging.info("Notes saved to: %s", filepath)
    return jsonify({"status": "saved", "filename": filename, "path": filepath})


def start_flask():
    app.run(port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    import webview

    t = threading.Thread(target=start_flask, daemon=True)
    t.start()

    webview.create_window(
        "Samba",
        "http://127.0.0.1:5000",
        width=1000,
        height=720,
        min_size=(800, 600),
    )
    webview.start()
