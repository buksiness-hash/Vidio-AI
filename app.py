"""VidioAI : serveur de génération vidéo (Replicate et Luma AI).

Lancer le site et l'API :  python app.py   puis ouvrir http://localhost:5000

Les clés API des visiteurs ne sont jamais enregistrées : elles servent
uniquement à relayer la demande vers Replicate ou Luma AI.
"""
import os
import re
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
VIDEO_DIR = BASE_DIR / "videos"
VIDEO_DIR.mkdir(exist_ok=True)

# Réglages (modifiables par variables d'environnement)
REPLICATE_MODEL = os.environ.get("REPLICATE_MODEL", "wan-video/wan-2.2-t2v-fast")
LUMA_MODEL = os.environ.get("LUMA_MODEL", "ray-flash-2")
LUMA_RESOLUTION = os.environ.get("LUMA_RESOLUTION", "720p")
LUMA_DURATION = os.environ.get("LUMA_DURATION", "5s")
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGIN", "").split(",") if o.strip()}

MAX_PROMPT_LENGTH = 1000
MAX_VIDEO_BYTES = 300 * 1024 * 1024
HTTP_TIMEOUT = 30
RATE_LIMIT = 10  # demandes de génération par minute et par adresse IP
ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")
VIDEO_NAME_PATTERN = re.compile(r"^(replicate|luma)-[A-Za-z0-9-]{8,64}\.mp4$")
API_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{8,200}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


class ApiError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify({"error": err.message}), err.status


@app.errorhandler(413)
def handle_too_large(_err):
    return jsonify({"error": "Requête trop volumineuse."}), 413


@app.after_request
def add_headers(response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Vary"] = "Origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# Limite simple en mémoire : 10 demandes de génération par minute et par IP
_hits = defaultdict(deque)


def rate_limited(ip):
    now = time.time()
    hits = _hits[ip]
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        return True
    hits.append(now)
    return False


# Appels vers les fournisseurs

def auth(api_key):
    return {"Authorization": "Bearer " + api_key}


def call(method, url, name, **kwargs):
    try:
        return requests.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
    except requests.RequestException:
        raise ApiError("Impossible de joindre " + name + ". Réessayez dans un instant.", 504)


def read_json(response, name):
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ApiError("Réponse inattendue de " + name + ".", 502)
    return data


def upstream_error(name, response):
    code = response.status_code
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or body.get("message") or "")
    except ValueError:
        pass
    if code in (401, 403):
        return ApiError(name + " a refusé la clé API. Vérifiez la clé et le crédit de votre compte.", 401)
    if code == 402:
        return ApiError("Crédit " + name + " insuffisant. Ajoutez du crédit sur votre compte " + name + ".", 402)
    if code == 429:
        return ApiError(name + " limite les demandes. Réessayez dans une minute.", 429)
    message = name + " a refusé la demande"
    if detail:
        message += " : " + detail[:200]
    return ApiError(message + ".", 502)


def replicate_create(api_key, prompt):
    response = call(
        "POST",
        "https://api.replicate.com/v1/models/" + REPLICATE_MODEL + "/predictions",
        "Replicate",
        headers={**auth(api_key), "Content-Type": "application/json"},
        json={"input": {"prompt": prompt}},
    )
    if response.status_code not in (200, 201):
        raise upstream_error("Replicate", response)
    return read_json(response, "Replicate").get("id")


def replicate_status(api_key, job_id):
    response = call("GET", "https://api.replicate.com/v1/predictions/" + job_id, "Replicate", headers=auth(api_key))
    if response.status_code != 200:
        raise upstream_error("Replicate", response)
    data = read_json(response, "Replicate")
    status = data.get("status")
    if status == "succeeded":
        output = data.get("output")
        if isinstance(output, list):
            output = output[0] if output else None
        if not isinstance(output, str):
            return {"status": "failed", "error": "Replicate n'a renvoyé aucune vidéo."}
        return {"status": "done", "source": output}
    if status in ("failed", "canceled"):
        reason = str(data.get("error") or "génération annulée")
        return {"status": "failed", "error": "La génération a échoué chez Replicate : " + reason[:200]}
    return {"status": "queued" if status == "starting" else "processing"}


def luma_create(api_key, prompt):
    response = call(
        "POST",
        "https://api.lumalabs.ai/dream-machine/v1/generations",
        "Luma AI",
        headers={**auth(api_key), "Content-Type": "application/json", "Accept": "application/json"},
        json={"prompt": prompt, "model": LUMA_MODEL, "resolution": LUMA_RESOLUTION, "duration": LUMA_DURATION},
    )
    if response.status_code not in (200, 201):
        raise upstream_error("Luma AI", response)
    return read_json(response, "Luma AI").get("id")


def luma_status(api_key, job_id):
    response = call(
        "GET",
        "https://api.lumalabs.ai/dream-machine/v1/generations/" + job_id,
        "Luma AI",
        headers={**auth(api_key), "Accept": "application/json"},
    )
    if response.status_code != 200:
        raise upstream_error("Luma AI", response)
    data = read_json(response, "Luma AI")
    state = data.get("state")
    if state == "completed":
        video = (data.get("assets") or {}).get("video")
        if not isinstance(video, str):
            return {"status": "failed", "error": "Luma AI n'a renvoyé aucune vidéo."}
        return {"status": "done", "source": video}
    if state == "failed":
        reason = str(data.get("failure_reason") or "raison inconnue")
        return {"status": "failed", "error": "La génération a échoué chez Luma AI : " + reason[:200]}
    return {"status": "queued" if state == "queued" else "processing"}


PROVIDERS = {
    "replicate": {"create": replicate_create, "status": replicate_status},
    "luma": {"create": luma_create, "status": luma_status},
}


def save_video(provider, job_id, source, api_key):
    """Télécharge la vidéo sur le serveur (Replicate supprime ses fichiers après une heure)."""
    parsed = urlparse(source)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ApiError("Adresse de vidéo invalide reçue du fournisseur.", 502)
    filename = provider + "-" + job_id + ".mp4"
    target = VIDEO_DIR / filename
    if target.exists():
        return filename

    headers = {}
    host = parsed.hostname.lower()
    replicate_hosts = ("replicate.com", "replicate.delivery")
    if provider == "replicate" and any(host == h or host.endswith("." + h) for h in replicate_hosts):
        headers = auth(api_key)

    part = VIDEO_DIR / ("." + filename + "." + uuid.uuid4().hex + ".part")
    try:
        with requests.get(source, headers=headers, stream=True, timeout=HTTP_TIMEOUT) as download:
            if download.status_code != 200:
                raise ApiError("Téléchargement de la vidéo impossible (code " + str(download.status_code) + ").", 502)
            size = 0
            with open(part, "wb") as out:
                for chunk in download.iter_content(chunk_size=256 * 1024):
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise ApiError("La vidéo dépasse la taille maximale autorisée.", 502)
                    out.write(chunk)
        os.replace(part, target)
    except requests.RequestException:
        raise ApiError("Téléchargement de la vidéo interrompu. Réessayez.", 502)
    finally:
        part.unlink(missing_ok=True)
    return filename


# Routes

@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/generate")
def generate():
    if rate_limited(request.remote_addr or "inconnu"):
        raise ApiError("Trop de demandes. Patientez une minute avant de réessayer.", 429)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Demande invalide.", 400)
    provider = str(payload.get("provider", "")).lower()
    api_key = str(payload.get("api_key", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()

    if provider not in PROVIDERS:
        raise ApiError("Moteur inconnu.", 400)
    if not API_KEY_PATTERN.match(api_key):
        raise ApiError("Clé API manquante ou invalide.", 400)
    if not prompt:
        raise ApiError("Décrivez la vidéo que vous voulez générer.", 400)
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ApiError("La description dépasse " + str(MAX_PROMPT_LENGTH) + " caractères.", 400)

    job_id = PROVIDERS[provider]["create"](api_key, prompt)
    if not isinstance(job_id, str) or not ID_PATTERN.match(job_id):
        raise ApiError("Réponse inattendue du fournisseur.", 502)
    return jsonify({"id": job_id, "provider": provider}), 202


@app.get("/api/status")
def status():
    provider = request.args.get("provider", "").lower()
    job_id = request.args.get("id", "")
    api_key = request.headers.get("X-API-Key", "").strip()

    if provider not in PROVIDERS:
        raise ApiError("Moteur inconnu.", 400)
    if not ID_PATTERN.match(job_id):
        raise ApiError("Identifiant de génération invalide.", 400)
    if not API_KEY_PATTERN.match(api_key):
        raise ApiError("Clé API manquante ou invalide.", 400)

    result = PROVIDERS[provider]["status"](api_key, job_id)
    if result["status"] == "done":
        filename = save_video(provider, job_id, result["source"], api_key)
        return jsonify({"status": "done", "video_url": "/videos/" + filename})
    return jsonify(result)


@app.get("/videos/<name>")
def video(name):
    if not VIDEO_NAME_PATTERN.match(name):
        return jsonify({"error": "Vidéo introuvable."}), 404
    return send_from_directory(VIDEO_DIR, name, mimetype="video/mp4", conditional=True)


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
