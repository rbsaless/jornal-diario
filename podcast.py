#!/usr/bin/env python3
"""Lê docs/index.html, gera um podcast em inglês (2 vozes, Gemini TTS) e envia ao Telegram."""
import base64, html, io, json, os, re, shutil, subprocess, sys, time, uuid, wave
import urllib.error, urllib.request

RAIZ = os.path.dirname(os.path.abspath(__file__))
CHAVE = os.environ["GEMINI_API_KEY"].strip()
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
CHAT = os.environ["TELEGRAM_CHAT_ID"].strip()
N_NOTICIAS = 20  # notícias contadas em detalhe
N_RAPIDAS = 30  # notícias do 'lightning round' (as próximas do ranking)
N_OUTRAS = 15  # manchetes extras só citadas
N_BLOCOS = 3  # 3 blocos de ~550 palavras = ~10 minutos
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


def carregar():
    """Usa docs/noticias.json (ranqueado, todas as seções); se não existir, cai no index.html."""
    try:
        dados = json.load(open(os.path.join(RAIZ, "docs", "noticias.json"), encoding="utf-8"))
        itens = dados["itens"]
        principais = [f'- {i["t"]}. ' + (i.get("d") or i["r"]).replace("\n", " ")[:700] for i in itens[:N_NOTICIAS]]
        rapidas = [f'- [{i["s"]}] {i["t"]}. {i["r"][:200]}' for i in itens[N_NOTICIAS:N_NOTICIAS + N_RAPIDAS]]
        outras = [f'- {o["t"]} ({o["f"]})' for o in dados.get("outras", [])[:N_OUTRAS]]
        print(f"noticias.json: {len(itens)} notícias; rápidas: {len(rapidas)}; manchetes extras: {len(outras)}")
        return principais, rapidas, outras
    except Exception as e:
        print(f"[aviso] sem noticias.json ({e}); usando o index.html")
        return noticias(), [], []


def roteiro(b, i, n):
    if b["rapido"]:
        pedido = ("Write the final 'lightning round' segment of a casual two-host news podcast, in English, at least 450 words. "
                  "Hosts: Alex and Sam. This segment continues an episode already in progress: do NOT greet and do NOT introduce the show; "
                  "start with a short natural transition. Cover EVERY story in STORIES in one or two short sentences each, grouped by topic, "
                  "with high energy and almost no banter; stay faithful to the facts, invent nothing. "
                  + ("Then say 'And in other headlines' and mention each item in OTHER HEADLINES in a few words. " if b["outras"] else "")
                  + "End with a short sign-off from both hosts. "
                  "Format STRICTLY as lines starting with 'Alex:' or 'Sam:' and nothing else.\n\nSTORIES:\n" + "\n".join(b["itens"])
                  + ("\n\nOTHER HEADLINES:\n" + "\n".join(b["outras"]) if b["outras"] else ""))
    else:
        if i == 0:
            regra = ("Alex must open with exactly this sentence: 'This is The Morning Paper report.' "
                     "Then go straight into the stories, with no other greeting or small talk. ")
        else:
            regra = ("This is a continuation of an episode already in progress: do NOT greet, do NOT introduce the show. "
                     "Start with a short natural transition into the next stories. ")
        pedido = ("Write a casual two-host news podcast script in English, at least 550 words (about 3 to 4 minutes when spoken). "
                  "Hosts: Alex and Sam. Short sentences, natural reactions, light banter, no emojis. "
                  "Cover EVERY story below with real detail (who, what, where, why it matters), grouping related ones; "
                  "stay faithful to the facts, invent nothing. " + regra +
                  ("End with a short sign-off from both hosts. " if i == n - 1
                   else "Do NOT close or sign off; the episode continues after this part. ") +
                  "Format STRICTLY as lines starting with 'Alex:' or 'Sam:' and nothing else.\n\nSTORIES:\n"
                  + "\n".join(b["itens"]))
    for m in MODELOS_TEXTO:
        try:
            t = chamar(m, {"contents": [{"parts": [{"text": pedido}]}],
                           "generationConfig": {"temperature": 0.7}})["text"]
            linhas = [l.strip() for l in t.splitlines() if re.match(r"^(Alex|Sam):", l.strip())]
            if len(linhas) >= 8:
                print(f"Roteiro {i + 1}/{n}: {m}, {len(linhas)} falas")
                return "\n".join(linhas)
        except Exception as e:
            print(f"[aviso] roteiro {i + 1} {m}: {e}")
    raise RuntimeError(f"sem roteiro do bloco {i + 1}")


def voz(script, i):
    corpo = {"contents": [{"parts": [{"text": "Read this as a lively, friendly podcast conversation:\n" + script}]}],
             "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"multiSpeakerVoiceConfig": {
                 "speakerVoiceConfigs": [{"speaker": k, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v}}}
                                         for k, v in VOZES.items()]}}}}
    for m in MODELOS_VOZ:
        for tentativa in range(3):
            try:
                pcm = base64.b64decode(chamar(m, corpo, 300)["inlineData"]["data"])
                print(f"Voz bloco {i + 1}: {m}, {len(pcm)} bytes (~{len(pcm) // 48000}s)")
                return pcm
            except urllib.error.HTTPError as e:
                print(f"[aviso] voz bloco {i + 1} {m}: HTTP {e.code} (tentativa {tentativa + 1})")
                if e.code in (429, 500, 503) and tentativa < 2:
                    time.sleep(45 * (tentativa + 1))
                    continue
                break
            except Exception as e:
                print(f"[aviso] voz bloco {i + 1} {m}: {e}")
                break
    raise RuntimeError(f"sem áudio do bloco {i + 1}")


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
    principais, rapidas, outras = carregar()
    if len(principais) < 3:
        sys.exit("Poucas notícias; podcast ignorado.")
    tam = -(-len(principais) // N_BLOCOS)
    blocos = [{"itens": principais[k:k + tam], "outras": [], "rapido": False} for k in range(0, len(principais), tam)]
    if rapidas or outras:
        blocos.append({"itens": rapidas, "outras": outras, "rapido": True})
    roteiros, pcm, silencio = [], b"", b"\x00" * int(24000 * 2 * 0.7)
    for i, b in enumerate(blocos):
        try:
            s = roteiro(b, i, len(blocos))
        except Exception as e:
            print(f"[aviso] {e}")
            continue
        roteiros.append(s)
        try:
            pcm += voz(s, i) + silencio
            time.sleep(8)  # respiro entre chamadas, por causa dos limites da cota gratuita
        except Exception as e:
            print(f"[aviso] {e}")
    if not roteiros:
        sys.exit("Nenhum roteiro gerado; podcast ignorado.")
    try:
        if not pcm:
            raise RuntimeError("nenhum bloco virou áudio")
        arq = para_arquivo(pcm)
        metodo, campo = ("sendAudio", "audio") if arq.endswith(".mp3") else ("sendDocument", "document")
        telegram(metodo, {"chat_id": CHAT, "title": "Morning Podcast", "performer": "The Morning Paper",
                          "caption": "Your morning podcast"}, (campo, arq))
        print(f"Podcast enviado (~{len(pcm) // 48000 // 60} min).")
    except Exception as e:
        print(f"[aviso] áudio falhou ({e}); enviando só o roteiro.")
        texto_final = "\n".join(roteiros)
        for k in range(0, len(texto_final), 3900):
            telegram("sendMessage", {"chat_id": CHAT, "text": texto_final[k:k + 3900]})


if __name__ == "__main__":
    main()
