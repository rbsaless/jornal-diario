#!/usr/bin/env python3
"""Gera a primeira página do jornal do dia em docs/index.html (só biblioteca padrão)."""
import datetime, html, json, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

RAIZ = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(RAIZ, "fontes.json"), encoding="utf-8"))
OPC = CFG["configuracoes"]
MODELOS = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-flash-lite-latest", "gemini-3.5-flash-lite",
                       "gemini-3.8-flash", "gemini-3.6-flash"] if m]
UA = {"User-Agent": "Mozilla/5.0 (compatible; JornalDiario/1.0)"}
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


def baixar(url, limite=None, t=25):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=t) as r:
        return r.read(limite)


def ln(tag):
    return tag.rsplit("}", 1)[-1]


def limpar(txt, n=500):
    txt = re.sub(r"<[^>]+>", " ", html.unescape(txt or ""))
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()[:n]


def data(txt):
    for f in (parsedate_to_datetime, lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            d = f(txt.strip())
            return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            pass
    return None


def imagem_do_item(el, blob):
    for c in el.iter():
        n, u = ln(c.tag), c.get("url") or ""
        tipo = (c.get("type") or "") + (c.get("medium") or "")
        if u and n in ("content", "thumbnail", "enclosure") and (
                n == "thumbnail" or "image" in tipo or re.search(r"\.(jpe?g|png|webp)", u, re.I)):
            return u
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', blob)
    return html.unescape(m.group(1)) if m else ""


def ler_feed(fonte, url=None):
    raiz = ET.fromstring(baixar(url or fonte["url"]))
    limite = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=fonte.get("horas", OPC["horas_recentes"]))
    itens = []
    for el in raiz.iter():
        if ln(el.tag) not in ("item", "entry"):
            continue
        d = {}
        for c in el:
            d.setdefault(ln(c.tag), c)
        txt = lambda k: (d[k].text or "") if k in d else ""
        link = (d["link"].get("href") or d["link"].text or "").strip() if "link" in d else ""
        blob = txt("encoded") + (txt("description") or next((c.text for c in el.iter() if ln(c.tag) == "description" and c.text), "")) + txt("summary") + txt("content")
        quando = next((data(txt(k)) for k in ("pubDate", "published", "updated", "date") if txt(k)), None)
        if not link or (quando and quando < limite):
            continue
        itens.append({"fonte": fonte["nome"], "titulo": limpar(txt("title"), 200), "link": link,
                      "resumo": limpar(blob, 400), "video": bool(fonte.get("youtube")), "imagem": imagem_do_item(el, blob), "quando": quando})
    itens.sort(key=lambda i: i["quando"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    return itens[:fonte.get("max", OPC["itens_por_fonte"])]


def ler_youtube(fonte):
    canal = fonte.get("canal_id")
    if not canal:
        h = baixar(f"https://www.youtube.com/{fonte['youtube']}", 2000000).decode("utf-8", "replace")
        m = re.search(r"channel_id=(UC[\w-]{22})", h) or re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', h)
        if not m:
            raise ValueError('não achei o canal; coloque "canal_id" no fontes.json')
        canal = m.group(1)
    return ler_feed(fonte, f"https://www.youtube.com/feeds/videos.xml?channel_id={canal}")


def ler_pagina(fonte):
    """Plano B: lê as manchetes direto da página quando o RSS falha."""
    base = fonte["plano_b"]
    h = baixar(base, 1500000).decode("utf-8", "replace")
    itens, vistos = [], set()
    for href, txt in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', h, re.S):
        titulo, link = limpar(txt, 200), urllib.parse.urljoin(base, href)
        if len(titulo) < 30 or link in vistos or urllib.parse.urlparse(link).netloc != urllib.parse.urlparse(base).netloc:
            continue
        vistos.add(link)
        itens.append({"fonte": fonte["nome"], "titulo": titulo, "link": link, "resumo": "", "imagem": "", "quando": None})
    return itens[:fonte.get("max", OPC["itens_por_fonte"])]


def og_image(link):
    if "news.google.com" in link:
        return ""
    try:
        h = baixar(link, 250000, 10).decode("utf-8", "replace")
    except Exception:
        return ""
    for p in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
              r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'):
        m = re.search(p, h)
        if m:
            return html.unescape(m.group(1))
    return ""


SECOES_VALIDAS = "Brasil, Política, Mundo, Economia, Esporte, Tecnologia, Ciência, Espaço, Coreias, Vídeo, Saúde, Cultura, Outros"


def prompt(itens, n):
    orient = OPC.get("orientacao_editorial", "")
    linhas = "\n".join(
        f'[{i["id"]}] ({i["fonte"]}{", VÍDEO" if i.get("video") else ""}) {i["titulo"]} :: {i["resumo"][:220]}'
        for i in itens)
    return f"""Você é o editor-chefe de um jornal diário em português do Brasil. Abaixo estão {len(itens)} itens coletados de várias fontes (a maioria em inglês).
Escolha os {n} mais importantes e relevantes. Elimine duplicatas (o mesmo fato em fontes diferentes: fique com um só), corte ruído, sensacionalismo e trivialidades, e equilibre os assuntos sem deixar uma única fonte dominar.
Orientação do dono do jornal: {orient}
Ordene do mais para o menos importante: o primeiro é a manchete.
Escreva TUDO em português do Brasil, fiel ao texto original, sem inventar fatos. Itens marcados VÍDEO são vídeos do YouTube: use a seção Vídeo e diga do que o vídeo trata.
Para cada item escolhido devolva: id (o número entre colchetes), titulo (até 90 caracteres), resumo (2 frases; para o primeiro, 4 frases), secao (exatamente uma destas: {SECOES_VALIDAS}).
Responda SOMENTE com JSON: {{"itens":[{{"id":0,"titulo":"...","resumo":"...","secao":"..."}}]}}

ITENS:
{linhas}"""


def gemini(texto):
    corpo = json.dumps({"contents": [{"parts": [{"text": texto}]}],
                        "generationConfig": {"responseMimeType": "application/json",
                                             "temperature": 0.3}}).encode()
    for modelo in MODELOS:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
            data=corpo,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()})
        for tentativa in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    saida = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
                itens = json.loads(re.sub(r"^```(?:json)?|```$", "", saida.strip()).strip())["itens"]
                print(f"Modelo usado: {modelo}")
                return itens
            except urllib.error.HTTPError as e:
                print(f"[aviso] {modelo}: HTTP {e.code}")
                if e.code in (500, 503) and tentativa < 2:
                    time.sleep(20 * (tentativa + 1))
                    continue
                break
            except (ValueError, KeyError, IndexError):
                print(f"[aviso] {modelo}: resposta inválida")
                break
    raise RuntimeError("Nenhum modelo do Gemini respondeu; veja os avisos acima.")


SECOES = {"politica": "#1D4ED8", "mundo": "#0F766E", "economia": "#15803D", "esporte": "#C2410C",
          "tecnologia": "#6D28D9", "saude": "#BE185D", "brasil": "#A16207", "cultura": "#7E22CE",
          "ciencia": "#0E7490", "espaco": "#3730A3", "coreias": "#A21CAF", "video": "#B91C1C"}
SANS = '"Libre Franklin",system-ui,-apple-system,"Segoe UI",sans-serif'

CSS = """
:root{--fundo:#F3F5F9;--cartao:#fff;--tinta:#0F172A;--suave:#556070;--linha:#E2E7EF;--marca:#0B1F3A;--alerta:#D7263D}
@media(prefers-color-scheme:dark){:root{--fundo:#0B1220;--cartao:#121B2E;--tinta:#E8EDF6;--suave:#9AA7BC;--linha:#233049;--marca:#070D1A}}
*{box-sizing:border-box}
body{margin:0;background:var(--fundo);color:var(--tinta);font:17px/1.55 "Source Serif 4",Georgia,serif}
a{color:inherit;text-decoration:none}
a:focus-visible{outline:3px solid var(--alerta);outline-offset:2px}
.topo{background:var(--marca);color:#fff;border-bottom:4px solid var(--alerta)}
.faixa{max-width:1200px;margin:0 auto;padding:22px 20px;display:flex;flex-wrap:wrap;gap:12px 24px;align-items:flex-end;justify-content:space-between}
h1{font:800 clamp(1.9rem,5vw,3rem)/1 SANS;letter-spacing:-.03em;margin:0}
.data{display:block;margin-top:8px;color:#B9C4D6;font:500 .95rem SANS}
.metas{display:flex;gap:8px;font:600 .85rem SANS}
.pilula{background:rgba(255,255,255,.12);padding:6px 12px;border-radius:99px}
main{max-width:1200px;margin:0 auto;padding:24px 20px 40px}
.destaque{display:grid;grid-template-columns:1.7fr 1fr;gap:20px;margin-bottom:36px}
.hero{position:relative;min-height:460px;border-radius:16px;overflow:hidden;color:#fff;display:flex;align-items:flex-end;background:linear-gradient(135deg,var(--cor),#0B1F3A)}
.hero img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.hero::after{content:"";position:absolute;inset:0;background:linear-gradient(to top,rgba(6,12,24,.92) 0%,rgba(6,12,24,.55) 45%,rgba(6,12,24,0) 75%)}
.hero .corpo{position:relative;z-index:1;padding:28px}
.hero h2{font:800 clamp(1.7rem,3.6vw,2.7rem)/1.1 SANS;letter-spacing:-.02em;margin:.55rem 0}
.hero h2 a:hover{text-decoration:underline}
.hero p{font-size:1.1rem;color:#E3E9F3;margin:0 0 .6rem;max-width:62ch}
.hero .fonte{color:#C9D3E3}
.lateral{display:grid;gap:14px;align-content:start}
.card,.mini{position:relative;background:var(--cartao);border:1px solid var(--linha);border-top:4px solid var(--cor);border-radius:12px;overflow:hidden;transition:transform .15s,box-shadow .15s}
.card:hover,.mini:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(15,23,42,.14)}
.card img{width:100%;aspect-ratio:16/10;object-fit:cover;display:block}
.corpo{padding:14px 16px 16px}
.mini{display:grid;grid-template-columns:116px 1fr;border-top-width:1px;border-left:4px solid var(--cor)}
.mini.sem{grid-template-columns:1fr}
.mini img{width:100%;height:100%;object-fit:cover}
.chip{display:inline-block;background:var(--cor);color:#fff;font:700 .75rem/1 SANS;padding:5px 9px;border-radius:99px}
h3{font:700 1.12rem/1.25 SANS;letter-spacing:-.01em;margin:.55rem 0 .35rem}
.mini h3{font-size:1rem}
.card p,.mini p{margin:0 0 .5rem;color:var(--suave);font-size:.98rem;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.mini p{-webkit-line-clamp:2}
.fonte{font:600 .8rem SANS;color:var(--suave)}
.card h3 a::after,.mini h3 a::after{content:"";position:absolute;inset:0}
.secao{font:800 1.3rem SANS;margin:0 0 16px;padding-left:12px;border-left:5px solid var(--alerta)}
.grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:20px}
footer{max-width:1200px;margin:0 auto;padding:0 20px 32px;color:var(--suave);font:500 .85rem SANS}
@media(max-width:900px){.destaque{grid-template-columns:1fr}.hero{min-height:380px}}
@media(max-width:520px){.mini{grid-template-columns:96px 1fr}.hero .corpo{padding:20px}}
@media print{.topo{background:#fff;color:#000}.card,.mini{break-inside:avoid;box-shadow:none}}
""".replace("SANS", SANS)


def esc(s):
    return html.escape(s or "", quote=True)


def cor(secao):
    k = unicodedata.normalize("NFD", secao or "").encode("ascii", "ignore").decode().lower().strip()
    return SECOES.get(k, "#475569")


def foto(it):
    return f'<img src="{esc(it["imagem"])}" alt="" loading="lazy" onerror="this.remove()">' if it.get("imagem") else ""


def miolo(it, tag):
    return (f'<div class="corpo"><span class="chip">{esc(it["secao"])}</span>'
            f'<{tag}><a href="{esc(it["link"])}">{esc(it["titulo"])}</a></{tag}>'
            f'<p>{esc(it["resumo"])}</p><span class="fonte">{esc(it["fonte"])}</span></div>')


def cartao(it, classe="card"):
    sem = "" if it.get("imagem") else " sem"
    return f'<article class="{classe}{sem}" style="--cor:{cor(it["secao"])}">{foto(it)}{miolo(it, "h3")}</article>'


def hero(it):
    return f'<article class="hero" style="--cor:{cor(it["secao"])}">{foto(it)}{miolo(it, "h2")}</article>'


def pagina(itens, agora):
    quando = f"{DIAS[agora.weekday()]}, {agora.day} de {MESES[agora.month - 1]} de {agora.year}"
    palavras = sum(len((i["titulo"] + " " + i["resumo"]).split()) for i in itens)
    destaque = (f'<section class="destaque">{hero(itens[0])}'
                f'<div class="lateral">{"".join(cartao(i, "mini") for i in itens[1:4])}</div></section>')
    grade = (f'<h2 class="secao">Mais notícias</h2><section class="grade">'
             f'{"".join(cartao(i) for i in itens[4:])}</section>') if len(itens) > 4 else ""
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>{esc(OPC["titulo"])} – {quando}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@500;600;700;800&family=Source+Serif+4:wght@400;600&display=swap">
<style>{CSS}</style></head><body>
<header class="topo"><div class="faixa"><div><h1>{esc(OPC["titulo"])}</h1><span class="data">{quando}</span></div>
<div class="metas"><span class="pilula">{len(itens)} notícias</span><span class="pilula">{max(1, round(palavras / 200))} min de leitura</span></div></div></header>
<main>{destaque}{grade}</main>
<footer>Edição montada às {agora:%H:%M} com resumos do Gemini. Confira sempre a fonte original.</footer>
</body></html>"""


def main():
    coletados = []
    for f in CFG["fontes"]:
        itens = []
        try:
            itens = ler_youtube(f) if f.get("youtube") else ler_feed(f)
        except Exception as e:
            print(f"[aviso] feed de {f['nome']} falhou: {e}")
        if len(itens) < 3 and f.get("plano_b"):
            try:
                itens = ler_pagina(f)
                print(f"[aviso] {f['nome']}: usando plano B ({len(itens)} manchetes)")
            except Exception as e:
                print(f"[aviso] plano B de {f['nome']} falhou: {e}")
        print(f"{f['nome']}: {len(itens)} itens")
        coletados += itens
    if not coletados:
        sys.exit("Nenhum item coletado; a edição anterior continua no ar.")
    for n, it in enumerate(coletados):
        it["id"] = n
    por_id, usados, finais = {i["id"]: i for i in coletados}, set(), []
    escolhidos = gemini(prompt(coletados, OPC["total_itens"]))
    print(f"Gemini devolveu {len(escolhidos)} itens.")
    for e in escolhidos:
        try:
            base = por_id.get(int(re.sub(r"\D", "", str(e.get("id")))))
        except ValueError:
            base = None
        if base and base["id"] not in usados:
            usados.add(base["id"])
            finais.append({**base, "titulo": e.get("titulo") or base["titulo"],
                           "resumo": e.get("resumo") or base["resumo"], "secao": e.get("secao", "Outros")})
    if len(finais) < 5:
        sys.exit("O Gemini devolveu itens de menos; a edição anterior continua no ar.")
    vistas = set()
    for it in finais:
        if it["imagem"] in vistas:
            it["imagem"] = ""
        if not it["imagem"]:
            it["imagem"] = og_image(it["link"])
            if it["imagem"] in vistas:
                it["imagem"] = ""
        if it["imagem"]:
            vistas.add(it["imagem"])
    agora = datetime.datetime.now(ZoneInfo("America/Sao_Paulo"))
    saida = pagina(finais, agora)
    os.makedirs(os.path.join(RAIZ, "docs", "edicoes"), exist_ok=True)
    for caminho in ("index.html", f"edicoes/{agora:%Y-%m-%d}.html"):
        open(os.path.join(RAIZ, "docs", caminho), "w", encoding="utf-8").write(saida)
    print(f"Edição de {agora:%d/%m/%Y} pronta com {len(finais)} notícias.")


if __name__ == "__main__":
    main()
