#!/usr/bin/env python3
"""Lê docs/index.html, gera um podcast em inglês (2 vozes, Gemini TTS) e envia ao Telegram."""
import base64, html, io, json, os, re, shutil, subprocess, sys, uuid, wave
import urllib.error, urllib.request

RAIZ = os.path.dirname(os.path.abspath(__file__))
CHAVE = os.environ["GEMINI_API_KEY"].strip()
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
CHAT = os.environ["TELEGRAM_CHAT_ID"].strip()
N_NOTICIAS = 12
MODELOS_TEXTO = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-flash-lite-latest",
                             "gemini-3.5-flash-lite", "gemini-3.8-flash"] if m]
MODELOS_VOZ = [m for m in [os.environ.get("GEMINI_TTS_MODEL"), "gemini-2.5-flash-preview-tts",
                           "gemini-2.5-pro-preview-tts"] if m]
VOZES = {"Alex": "Kore", "Sam": "Puck"}


def texto(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def noticias():
    h = open(os.path.join(RAIZ, "docs", "index.html"), encoding="utf-8").read()
    saida, vistos = [], set()
    for art in re.findall(r"<article.*?</article>", h, re.S):
        m = re.search(r'<a[^>]+href="[^"]*"[^>]*>(.*?)</a>', art, re.S)
        ps = [texto(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", art, re.S)]
        titulo = texto(m.group(1)) if m else ""
        if titulo and titulo not in vistos:
            vistos.add(titulo)
            saida.append(f"- {titulo}. {ps[0] if ps else ''}")
        if len(saida) == N_NOTICIAS:
            break
    return saida


def chamar(modelo, corpo, t=180):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
        data=json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": CHAVE})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.load(r)["candidates"][0]["content"]["parts"][0]


def roteiro(itens):
    pedido = ("Write a casual two-host news podcast script in English, about 800 words (5-6 minutes). "
              "Hosts: Alex and Sam. Short sentences, natural reactions, light banter, no emojis. "
              "Cover the stories below, grouping related ones; stay faithful to the facts, invent nothing. "
              "Open with a quick greeting and close with a short sign-off. "
              "Format STRICTLY as lines starting with 'Alex:' or 'Sam:' and nothing else.\n\nSTORIES:\n"
              + "\n".join(itens))
    for m in MODELOS_TEXTO:
        try:
            t = chamar(m, {"contents": [{"parts": [{"text": pedido}]}],
                           "generationConfig": {"temperature": 0.7}})["text"]
            linhas = [l.strip() for l in t.splitlines() if re.match(r"^(Alex|Sam):", l.strip())]
            if len(linhas) >= 6:
                print(f"Roteiro: {m}, {len(linhas)} falas")
                return "\n".join(linhas)
        except Exception as e:
            print(f"[aviso] roteiro {m}: {e}")
    raise RuntimeError("sem roteiro")


def voz(script):
    corpo = {"contents": [{"parts": [{"text": "Read this as a lively, friendly podcast conversation:\n" + script}]}],
             "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"multiSpeakerVoiceConfig": {
                 "speakerVoiceConfigs": [{"speaker": k, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v}}}
                                         for k, v in VOZES.items()]}}}}
    for m in MODELOS_VOZ:
        try:
            pcm = base64.b64decode(chamar(m, corpo, 300)["inlineData"]["data"])
            print(f"Voz: {m}, {len(pcm)} bytes")
            return pcm
        except Exception as e:
            print(f"[aviso] voz {m}: {e}")
    raise RuntimeError("sem áudio")


def para_arquivo(pcm):
    wav = os.path.join(RAIZ, "podcast.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(pcm)
    if shutil.which("ffmpeg"):
        mp3 = os.path.join(RAIZ, "podcast.mp3")
        if subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-b:a", "96k", mp3]).returncode == 0:
            return mp3
    return wav


def telegram(metodo, campos, arquivo=None):
    lim = uuid.uuid4().hex
    corpo = b""
    for k, v in campos.items():
        corpo += f'--{lim}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    if arquivo:
        campo, caminho = arquivo
        corpo += (f'--{lim}\r\nContent-Disposition: form-data; name="{campo}"; '
                  f'filename="{os.path.basename(caminho)}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        corpo += open(caminho, "rb").read() + b"\r\n"
    corpo += f"--{lim}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{metodo}", data=corpo,
                                 headers={"Content-Type": f"multipart/form-data; boundary={lim}"})
    return urllib.request.urlopen(req, timeout=300).read()


def main():
    itens = noticias()
    if len(itens) < 3:
        sys.exit("Poucas notícias no index.html; podcast ignorado.")
    script = roteiro(itens)
    try:
        arq = para_arquivo(voz(script))
        metodo, campo = ("sendAudio", "audio") if arq.endswith(".mp3") else ("sendDocument", "document")
        telegram(metodo, {"chat_id": CHAT, "title": "Morning Podcast", "caption": "Your morning podcast"}, (campo, arq))
        print("Podcast enviado.")
    except Exception as e:
        print(f"[aviso] áudio falhou ({e}); enviando só o roteiro.")
        for i in range(0, len(script), 3900):
            telegram("sendMessage", {"chat_id": CHAT, "text": script[i:i + 3900]})


if __name__ == "__main__":
    main()
