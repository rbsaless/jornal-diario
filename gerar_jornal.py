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
DIAS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MESES = ["January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"]


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
                      "resumo": limpar(blob, 400), "texto": limpar(blob, 1600), "video": bool(fonte.get("youtube")), "imagem": imagem_do_item(el, blob), "quando": quando})
    itens.sort(key=lambda i: i["quando"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    lim = fonte.get("max", OPC["itens_por_fonte"])
    for n, it in enumerate(itens):
        it["extra"] = n >= lim
    return itens[:lim * OPC.get("fator_extra", 2)]


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


SECOES_VALIDAS = "Brazil, Politics, World, Economy, Sports, Technology, Science, Space, Koreas, Video, Health, Culture, Other"


def prompt(itens, n, capa):
    orient = OPC.get("orientacao_editorial", "")
    linhas = "\n".join(
        f'[{i["id"]}] ({i["fonte"]}{", VIDEO" if i.get("video") else ""}) {i["titulo"]} :: {i["resumo"][:220]}'
        for i in itens)
    return f"""You are the editor-in-chief of a daily English-language newspaper. Below are {len(itens)} items collected from several sources (some originally in Portuguese).
Pick up to {n} items worth publishing, in strict order of importance. The first {capa} form the front page; the rest fill the section tabs, so they must still be relevant but can be less important.
Remove duplicates (the same event from different sources: keep only one), cut noise, sensationalism and trivia, and balance the topics without letting any single source dominate.
Publisher's guidance for the front page: {orient}
Write EVERYTHING in English (translate Portuguese items), staying faithful to the original and without inventing facts. Items marked VIDEO are YouTube videos: use the Video section and say what the video is about.
For each chosen item return: id (the number in brackets), titulo (headline, up to 90 characters), resumo (4 sentences for the first item, 2 sentences for items 2 to {capa}, 1 sentence for the rest), secao (exactly one of: {SECOES_VALIDAS}).
Reply ONLY with JSON: {{"itens":[{{"id":0,"titulo":"...","resumo":"...","secao":"..."}}]}}

ITEMS:
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


def prompt_detalhe(itens):
    linhas = "\n".join(f'[{i["id"]}] ({i["fonte"]}) {i["titulo"]} :: {i.get("texto") or i["resumo"]}' for i in itens)
    return f"""You are a newspaper editor. For each story below, write "detalhe": a clear reading-page text of 90 to 140 words in English, as 2 short paragraphs separated by a blank line.
Use ONLY facts that appear in the provided text (translate Portuguese). Do not repeat the headline word for word. If the text is short, write fewer words instead of inventing anything. For videos, say what the video covers.
Reply ONLY with JSON: {{"itens":[{{"id":0,"detalhe":"..."}}]}}

STORIES:
{linhas}"""


PT = re.compile(r"[ãõçáéíóúâêôà]| (de|do|da|dos|das|em|para|com|que|uma|não|após|sobre|após|pelo|pela) ", re.I)


def complementar(itens, obrig_ids, lote=30):
    """Traduz títulos para inglês; obrigatórios recebem também resumo e seção. Repete o que falhou."""
    res, pend = {}, list(itens)
    for rodada in range(3):
        for k in range(0, len(pend), lote):
            linhas = "\n".join(
                f'[{i["id"]}] ({i["fonte"]}{", MUST" if i["id"] in obrig_ids else ""}) {i["titulo"]}'
                + (f' :: {i["resumo"][:200]}' if i["id"] in obrig_ids else "") for i in pend[k:k + lote])
            pedido = f"""You are a newspaper editor. Below are news items, one per line.
For every item whose headline is NOT already in English, return its id and "titulo": the headline translated to English (up to 90 characters, faithful, nothing invented).
Items marked MUST are different: ALWAYS return them, with "titulo" (English, up to 90 characters), "resumo" (1 or 2 faithful sentences in English) and "secao" (exactly one of: {SECOES_VALIDAS}).
Do not return English-language items that are not marked MUST.
Reply ONLY with JSON: {{"itens":[{{"id":0,"titulo":"...","resumo":"...","secao":"..."}}]}}

ITEMS:
{linhas}"""
            try:
                for e in gemini(pedido):
                    try:
                        res[int(re.sub(r"\D", "", str(e.get("id"))))] = e
                    except ValueError:
                        pass
            except Exception as ex:
                print(f"[aviso] complemento rodada {rodada + 1} lote {k // lote + 1} falhou: {ex}")
        pend = [i for i in pend if i["id"] not in res and (i["id"] in obrig_ids or PT.search(i["titulo"]))]
        if not pend:
            break
    return res


def video_id(it):
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/)([\w-]{11})", it.get("link", "")) if it.get("video") else None
    return m.group(1) if m else ""


ORDEM = ["Brazil", "Politics", "World", "Economy", "Sports", "Technology", "Science", "Space", "Koreas",
         "Video", "Health", "Culture", "Other"]
ALIAS = {"brasil": "Brazil", "politica": "Politics", "mundo": "World", "economia": "Economy", "esporte": "Sports",
         "tecnologia": "Technology", "ciencia": "Science", "espaco": "Space", "coreias": "Koreas",
         "saude": "Health", "cultura": "Culture", "outros": "Other", "tech": "Technology", "sport": "Sports"}
SECOES = {"politics": "#1D4ED8", "world": "#0F766E", "economy": "#15803D", "sports": "#C2410C",
          "technology": "#6D28D9", "health": "#BE185D", "brazil": "#A16207", "culture": "#7E22CE",
          "science": "#0E7490", "space": "#3730A3", "koreas": "#A21CAF", "video": "#B91C1C"}
SANS = '"Inter",system-ui,-apple-system,"Segoe UI",sans-serif'
DARK = ("--fundo:#0B1220;--cartao:#121B2E;--tinta:#E8EDF6;--suave:#9AA7BC;--linha:#233049;--marca:#070D1A;"
        "--marca2:#0E2347;--sombra:0 1px 2px rgba(0,0,0,.35);--alta:0 12px 30px rgba(0,0,0,.5);color-scheme:dark")

CSS = """
:root{--fundo:#F4F6FA;--cartao:#fff;--tinta:#0F172A;--suave:#5B6678;--linha:#E3E8F0;--marca:#0B1F3A;--marca2:#14325F;--alerta:#D7263D;
--sombra:0 1px 2px rgba(15,23,42,.05),0 4px 14px rgba(15,23,42,.06);--alta:0 2px 4px rgba(15,23,42,.06),0 14px 32px rgba(15,23,42,.16);color-scheme:light}
:root[data-theme="dark"]{@@DARK@@}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){@@DARK@@}}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--fundo);color:var(--tinta);font:18px/1.65 "Source Serif 4",Georgia,serif;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
a:focus-visible,button:focus-visible{outline:3px solid var(--alerta);outline-offset:2px}
.topo{background:linear-gradient(135deg,var(--marca),var(--marca2));color:#fff;border-bottom:4px solid var(--alerta)}
.faixa{max-width:1480px;margin:0 auto;padding:26px clamp(18px,3vw,44px);display:flex;flex-wrap:wrap;gap:14px 24px;align-items:center;justify-content:space-between}
h1{font:800 clamp(1.9rem,5vw,3rem)/1 SANS;letter-spacing:-.03em;margin:0}
.data{display:block;margin-top:8px;color:#B9C4D6;font:500 .95rem SANS}
.metas{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font:600 .85rem SANS}
.pilula{background:rgba(255,255,255,.12);padding:6px 12px;border-radius:99px}
.tema{width:36px;height:36px;border-radius:50%;border:1px solid rgba(255,255,255,.28);background:transparent;color:#fff;font-size:1.05rem;cursor:pointer}
.tema:hover{background:rgba(255,255,255,.14)}
.abas{position:sticky;top:0;z-index:20;background:var(--fundo);border-bottom:1px solid var(--linha)}
@supports(backdrop-filter:blur(8px)){.abas{background:color-mix(in srgb,var(--fundo) 86%,transparent);backdrop-filter:blur(10px)}}
.abas-in{position:relative;max-width:1480px;margin:0 auto;display:flex;gap:8px;overflow-x:auto;padding:10px clamp(18px,3vw,44px);scrollbar-width:none}
.abas-in::-webkit-scrollbar{display:none}
.aba{flex:none;display:inline-flex;align-items:center;gap:8px;border:1px solid var(--linha);background:var(--cartao);color:var(--tinta);font:600 .9rem SANS;padding:8px 14px;border-radius:99px;cursor:pointer;transition:background .15s,border-color .15s,color .15s}
.aba:hover{border-color:var(--cor)}
.aba[aria-selected="true"]{background:var(--cor);border-color:var(--cor);color:#fff}
.ponto{width:8px;height:8px;border-radius:50%;background:var(--cor)}
.aba[aria-selected="true"] .ponto{background:#fff}
.n{font-size:.8rem;font-weight:700;opacity:.6}
.no-js .abas{display:none}
main{max-width:1480px;margin:0 auto;padding:36px clamp(18px,3vw,44px) 60px}
.painel{animation:entra .25s ease}
@keyframes entra{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.destaque{display:grid;grid-template-columns:1.8fr 1fr;gap:30px;margin-bottom:60px}
.hero{position:relative;min-height:600px;border-radius:18px;overflow:hidden;color:#fff;display:flex;align-items:flex-end;background:linear-gradient(135deg,var(--cor),#0B1F3A);box-shadow:var(--alta)}
.hero img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.hero::after{content:"";position:absolute;inset:0;background:linear-gradient(to top,rgba(6,12,24,.93) 0%,rgba(6,12,24,.55) 45%,rgba(6,12,24,0) 78%)}
.hero .corpo{position:relative;z-index:1;padding:42px;width:100%}
.hero h2{font:800 clamp(1.9rem,3.8vw,3.3rem)/1.08 SANS;letter-spacing:-.02em;margin:.55rem 0}
.hero h2 a:hover{text-decoration:underline}
.hero p{font-size:1.22rem;color:#E3E9F3;margin:0 0 .9rem;max-width:68ch}
.hero .rodape{color:#C9D3E3}
.lateral{display:grid;gap:22px;align-content:start}
.card,.mini{position:relative;background:var(--cartao);border:1px solid var(--linha);border-top:3px solid var(--cor);border-radius:14px;overflow:hidden;box-shadow:var(--sombra);transition:transform .18s,box-shadow .18s}
.card:hover,.mini:hover{transform:translateY(-3px);box-shadow:var(--alta)}
.card img{width:100%;aspect-ratio:16/10;object-fit:cover;display:block;background:var(--linha);transition:transform .5s}
.card:hover img{transform:scale(1.04)}
.corpo{padding:22px 24px 24px}
.mini{display:grid;grid-template-columns:150px 1fr;border-top-width:1px;border-left:4px solid var(--cor)}
.mini.sem{grid-template-columns:1fr}
.mini img{width:100%;height:100%;object-fit:cover;background:var(--linha)}
.chip{display:inline-block;background:var(--cor);color:#fff;font:700 .75rem/1 SANS;padding:5px 10px;border-radius:99px}
h3{font:700 1.22rem/1.3 SANS;letter-spacing:-.01em;margin:.6rem 0 .4rem}
.mini h3{font-size:1.1rem}
.card p,.mini p{margin:0 0 .85rem;color:var(--suave);font-size:1.04rem;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.mini p{-webkit-line-clamp:2}
.rodape{display:flex;justify-content:space-between;gap:12px;font:600 .8rem SANS;color:var(--suave)}
.card h3 a::after,.mini h3 a::after{content:"";position:absolute;inset:0}
.card.grande{grid-column:span 2;display:grid;grid-template-columns:1.25fr 1fr}
.card.grande img{height:100%;min-height:330px;aspect-ratio:auto}
.card.grande .corpo{align-self:center;padding:34px}
.card.grande h3{font-size:2rem;line-height:1.2}
.card.grande p{-webkit-line-clamp:6;font-size:1.12rem}
.secao{font:800 1.6rem SANS;margin:0 0 26px;padding-left:12px;border-left:5px solid var(--alerta)}
.cab-secao{display:flex;align-items:baseline;gap:14px;margin-bottom:30px;padding-left:14px;border-left:6px solid var(--cor)}
.cab-secao h2{font:800 2.3rem/1.1 SANS;margin:0;letter-spacing:-.02em}
.cab-secao span{color:var(--suave);font:600 .9rem SANS}
.grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:30px}
footer{max-width:1480px;margin:0 auto;padding:0 clamp(18px,3vw,44px) 40px;color:var(--suave);font:500 .85rem SANS}
@media(max-width:900px){.destaque{grid-template-columns:1fr}.hero{min-height:390px}}
@media(max-width:820px){.card.grande{grid-column:auto;display:block}.card.grande img{aspect-ratio:16/10;min-height:0}}
@media(max-width:520px){.mini{grid-template-columns:96px 1fr}.hero .corpo{padding:20px}.faixa{padding:18px 20px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.painel{animation:none}*{transition:none!important}}
@media print{.topo{background:#fff;color:#000}.data{color:#444}.abas,.tema{display:none}.painel[hidden]{display:block!important}.card,.mini,.hero{break-inside:avoid;box-shadow:none}}
article[data-i]{cursor:pointer}
body.l-aberto{overflow:hidden}
.leitor{position:fixed;inset:0;z-index:50;display:flex;justify-content:center;align-items:flex-start;overflow-y:auto;padding:clamp(0px,3vw,36px)}
.leitor[hidden]{display:none}
.l-fundo{position:fixed;inset:0;background:rgba(6,12,24,.64);backdrop-filter:blur(3px)}
.l-caixa{position:relative;width:min(1180px,100%);min-height:60vh;background:var(--fundo);border-radius:20px;box-shadow:var(--alta);border-top:6px solid var(--cor,#D7263D);animation:entra .22s ease}
.l-barra{position:sticky;top:0;z-index:2;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px clamp(18px,3vw,36px);background:var(--fundo);border-bottom:1px solid var(--linha);border-radius:14px 14px 0 0}
.l-acoes{display:flex;align-items:center;gap:10px}
.btn{font:700 .85rem SANS;background:var(--tinta);color:var(--fundo);padding:10px 18px;border-radius:99px}
.btn:hover{opacity:.88}
#l-x{width:40px;height:40px;border-radius:50%;border:1px solid var(--linha);background:var(--cartao);color:var(--tinta);font-size:1.05rem;cursor:pointer}
#l-x:hover{border-color:var(--cor)}
.l-grade{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:clamp(24px,4vw,56px);padding:clamp(20px,3.5vw,46px)}
.l-main h2{font:800 clamp(1.9rem,4vw,3.1rem)/1.1 SANS;letter-spacing:-.025em;margin:.8rem 0 .9rem}
.l-deck{font:italic 400 clamp(1.15rem,2vw,1.4rem)/1.5 "Source Serif 4",Georgia,serif;color:var(--suave);margin:0 0 1.2rem}
.l-meta{display:flex;flex-wrap:wrap;gap:14px;font:600 .85rem SANS;color:var(--suave);padding-bottom:18px;border-bottom:1px solid var(--linha);margin-bottom:24px}
.l-midia{margin:0 0 28px}
.l-midia img,.l-midia iframe{width:100%;aspect-ratio:16/9;object-fit:cover;border:0;border-radius:14px;display:block;background:var(--linha)}
.l-texto p{font-size:1.22rem;line-height:1.75;margin:0 0 1.2rem;max-width:68ch}
.l-aviso{font:500 .82rem/1.5 SANS;color:var(--suave);border-top:1px solid var(--linha);padding-top:14px;margin:28px 0 0}
.l-mais{align-self:start;position:sticky;top:84px;background:var(--cartao);border:1px solid var(--linha);border-radius:14px;padding:18px 20px}
.l-mais h3{font:800 1rem SANS;margin:0 0 6px}
.l-mais button{display:block;width:100%;text-align:left;background:none;border:0;border-top:1px solid var(--linha);padding:13px 0;color:var(--tinta);font:600 .96rem/1.35 SANS;cursor:pointer}
#l-lista button:first-child{border-top:0}
.l-mais button:hover{color:var(--cor)}
.l-mais small{display:block;color:var(--suave);font:500 .75rem SANS;margin-top:4px}
@media(max-width:900px){.l-grade{grid-template-columns:1fr}.l-mais{position:static}}
@media(max-width:600px){.leitor{padding:0}.l-caixa{border-radius:0;min-height:100vh}.l-barra{border-radius:0}}
@media print{.leitor{display:none!important}}
.topo{background:none;color:var(--tinta);border-bottom:0}
.faixa{padding-bottom:8px}
h1{font-weight:800;letter-spacing:-.04em}
.data{color:var(--suave)}
.pilula{background:var(--cartao);border:1px solid var(--linha);color:var(--tinta)}
.tema{border-color:var(--linha);color:var(--tinta)}
.bento{display:grid;grid-template-columns:repeat(4,1fr);grid-auto-rows:210px;grid-auto-flow:dense;gap:18px}
.grade-t{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));grid-auto-rows:300px;grid-auto-flow:dense;gap:18px}
.tile{position:relative;overflow:hidden;border-radius:24px;color:#fff;display:flex;align-items:flex-end;background:linear-gradient(145deg,var(--cor),#0F172A);box-shadow:var(--sombra);transition:transform .2s,box-shadow .2s}
.tile:hover{transform:translateY(-4px);box-shadow:var(--alta)}
.tile>img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform .5s}
.tile:hover>img{transform:scale(1.06)}
.tile:has(>img)::after{content:"";position:absolute;inset:0;background:linear-gradient(to top,rgba(8,12,24,.9),rgba(8,12,24,.1) 70%)}
.tile .in{position:relative;z-index:1;padding:20px;width:100%}
.tile h3{font:800 1.15rem/1.2 SANS;letter-spacing:-.015em;margin:.55rem 0 .3rem;color:#fff}
.tile h3 a::after{content:"";position:absolute;inset:0;z-index:2}
.tile small{opacity:.85;font:600 .76rem SANS}
.tile p{margin:.2rem 0 .6rem;opacity:.92;max-width:52ch;font-size:1.02rem}
.dest{grid-column:span 2;grid-row:span 2}.dest h3{font-size:clamp(1.6rem,2.8vw,2.4rem);line-height:1.1}
.alto{grid-row:span 2}.largo{grid-column:span 2}
.tile.txt .in{padding:24px}.tile.txt h3{font-size:1.3rem}
@media(max-width:1000px){.bento{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.bento{grid-template-columns:1fr;grid-auto-rows:230px}.dest,.largo{grid-column:auto}.alto{grid-row:auto}.grade-t{grid-template-columns:1fr}}
.fontes-grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:26px;align-items:start}
.fonte-card{background:var(--cartao);border:1px solid var(--linha);border-top:3px solid var(--cor);border-radius:14px;padding:20px 22px;box-shadow:var(--sombra)}
.fonte-card h3{display:flex;justify-content:space-between;align-items:baseline;margin:0 0 8px;font:800 1.1rem SANS}
.fonte-card h3 span{color:var(--suave);font:600 .85rem SANS}
.fonte-card ul{list-style:none;margin:0;padding:0}
.fonte-card li{padding:11px 0;border-top:1px solid var(--linha);font:600 .98rem/1.38 SANS}
.fonte-card li:first-child{border-top:0}
.fonte-card li a:hover{color:var(--cor);text-decoration:underline}
.fonte-card li small{display:block;margin-top:3px;color:var(--suave);font:500 .75rem SANS}
.fonte-card details{border-top:1px solid var(--linha);margin-top:4px}
.fonte-card summary{cursor:pointer;padding:12px 0 4px;font:700 .88rem SANS;color:var(--cor)}
@media(max-width:520px){.fontes-grade{grid-template-columns:1fr}}
:root{--fundo:#fff;--cartao:#fff;--tinta:#111;--suave:#555;--linha:#e5e5e5;--alerta:#e11d48;--sombra:none}
.topo{border-bottom:3px solid var(--tinta)}
.faixa{padding-bottom:14px}
h1{font:800 clamp(1.7rem,4vw,2.4rem)/1 SANS;letter-spacing:-.04em}
h1::after{content:".";color:var(--alerta)}
.abas{border-bottom:1px solid var(--linha)}
.aba{border:0;border-radius:0;background:none;padding:10px 4px;margin-right:14px;font:600 .85rem SANS;border-bottom:2px solid transparent}
.aba:hover{border-color:var(--alerta)}
.aba[aria-selected="true"]{background:none;color:var(--alerta);border-color:var(--alerta)}
.ponto{display:none}
.pilula{border-radius:4px;background:none;font-weight:600}
main{padding-top:28px}
.k{font:800 .72rem SANS;color:var(--alerta);text-transform:uppercase;letter-spacing:.04em}
.meta{font:600 .78rem SANS;color:var(--suave)}
.ed-top{display:grid;grid-template-columns:2fr 1fr;gap:36px;margin-bottom:44px}
.ed-hero,.cd,.row{position:relative;cursor:pointer}
.im{overflow:hidden;border-radius:6px;background:linear-gradient(135deg,var(--cor),#0f172a)}
.im img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .4s}
.ed-hero:hover .im img,.cd:hover .im img{transform:scale(1.04)}
.ed-hero .im{aspect-ratio:16/9}
.ed-hero h2{font:800 clamp(1.9rem,3.6vw,2.8rem)/1.08 SANS;letter-spacing:-.03em;margin:14px 0 10px}
.ed-hero p{color:var(--suave);font-size:1.15rem;line-height:1.55;margin:0 0 10px}
.row{display:grid;grid-template-columns:96px 1fr;gap:14px;padding:14px 0;border-bottom:1px solid var(--linha)}
.row.sem{grid-template-columns:1fr}
.row .im{aspect-ratio:1}
.row h3{font:700 1.02rem/1.28 SANS;margin:3px 0 4px;letter-spacing:-.01em}
.ed-lado .row:first-child{padding-top:0}
.sec-ed{font:800 1.3rem SANS;border-top:3px solid var(--tinta);padding-top:10px;margin:0 0 20px;display:flex;justify-content:space-between;align-items:baseline}
.sec-ed span{font:600 .85rem SANS;color:var(--suave)}
.grade-ed{display:grid;grid-template-columns:repeat(3,1fr);gap:30px 26px;margin-bottom:44px}
.cd .im{aspect-ratio:16/10;margin-bottom:10px}
.cd h3{font:700 1.2rem/1.25 SANS;letter-spacing:-.015em;margin:4px 0 6px}
.cd p{color:var(--suave);font-size:.98rem;line-height:1.5;margin:0 0 8px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.lista-ed{display:grid;grid-template-columns:repeat(2,1fr);column-gap:40px}
:root[data-theme="dark"]{--fundo:#0B1220;--cartao:#121B2E;--tinta:#E8EDF6;--suave:#9AA7BC;--linha:#233049;--alerta:#fb4a6b}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--fundo:#0B1220;--cartao:#121B2E;--tinta:#E8EDF6;--suave:#9AA7BC;--linha:#233049;--alerta:#fb4a6b}}

.h3a::after{content:"";position:absolute;inset:0}
@media(max-width:900px){.ed-top{grid-template-columns:1fr}.grade-ed{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.grade-ed,.lista-ed{grid-template-columns:1fr}}
""".replace("@@DARK@@", DARK).replace("SANS", SANS)

JS = """
(function(){
 var d=document,h=d.documentElement,abas=[].slice.call(d.querySelectorAll('[role=tab]')),pain=[].slice.call(d.querySelectorAll('.painel'));
 function ir(id,foco,topo){
  if(!d.getElementById('p-'+id))id='capa';
  abas.forEach(function(a){
   var on=a.dataset.id===id;a.setAttribute('aria-selected',on?'true':'false');a.tabIndex=on?0:-1;
   if(on){if(foco)a.focus({preventScroll:true});var b=a.parentNode;b.scrollTo({left:a.offsetLeft-(b.clientWidth-a.offsetWidth)/2,behavior:'smooth'})}
  });
  pain.forEach(function(p){p.hidden=p.id!=='p-'+id});
  try{history.replaceState(null,'','#'+id)}catch(e){}
  if(topo)window.scrollTo({top:0,behavior:'smooth'});
 }
 abas.forEach(function(a,i){
  a.addEventListener('click',function(){ir(a.dataset.id,false,true)});
  a.addEventListener('keydown',function(e){var k=e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0;if(k){e.preventDefault();ir(abas[(i+k+abas.length)%abas.length].dataset.id,true)}});
 });
 addEventListener('hashchange',function(){ir(location.hash.slice(1))});
 ir(location.hash.slice(1)||'capa');
 d.getElementById('tema').addEventListener('click',function(){
  var cur=h.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'),nv=cur==='dark'?'light':'dark';
  h.setAttribute('data-theme',nv);try{localStorage.setItem('jm-tema',nv)}catch(e){}
 });
})();
(function(){
 var d=document,L=d.getElementById('leitor');
 if(!L)return;
 var D=[];try{D=JSON.parse(d.getElementById('dados').textContent)}catch(e){}
 var NL=String.fromCharCode(10),ult=null;
 function q(s){return L.querySelector(s)}
 function el(t,c,x){var e=d.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
 function abrir(i){
  var it=D[i];if(!it)return;
  if(L.hidden)ult=d.activeElement;
  q('.l-caixa').style.setProperty('--cor',it.c);
  var sc=q('.l-secao');sc.textContent=it.s;sc.style.background=it.c;
  q('#l-tit').textContent=it.t;
  q('.l-deck').textContent=it.r;
  var m=q('.l-meta');m.textContent='';m.appendChild(el('span','',it.f));if(it.h)m.appendChild(el('span','',it.h));
  var f=q('.l-midia');f.textContent='';
  if(it.v){var fr=d.createElement('iframe');fr.src='https://www.youtube-nocookie.com/embed/'+it.v;fr.title=it.t;fr.allow='encrypted-media; picture-in-picture';fr.allowFullscreen=true;f.appendChild(fr);f.hidden=false}
  else if(it.i){var im=d.createElement('img');im.src=it.i;im.alt='';im.onerror=function(){f.hidden=true};f.appendChild(im);f.hidden=false}
  else f.hidden=true;
  var tx=q('.l-texto');tx.textContent='';
  (it.d||'').split(NL).filter(function(p){return p.trim()}).forEach(function(p){tx.appendChild(el('p','',p))});
  q('#l-orig').href=it.l;
  var ls=q('#l-lista');ls.textContent='';var n=0;
  D.forEach(function(o,j){if(j!==+i&&o.s===it.s&&n<6){n++;var b=el('button','',o.t);b.type='button';b.appendChild(el('small','',o.f));b.addEventListener('click',function(){abrir(j)});ls.appendChild(b)}});
  q('.l-mais').hidden=!n;q('.l-mais h3 span').textContent=it.s;
  L.hidden=false;d.body.classList.add('l-aberto');L.scrollTop=0;q('#l-x').focus({preventScroll:true});
 }
 function fechar(){if(L.hidden)return;L.hidden=true;d.body.classList.remove('l-aberto');q('.l-midia').textContent='';if(ult&&ult.focus)ult.focus({preventScroll:true})}
 d.addEventListener('click',function(e){
  if(e.target.closest('#leitor'))return;
  var a=e.target.closest('article[data-i]');
  if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button)return;
  e.preventDefault();abrir(a.dataset.i);
 });
 q('#l-x').addEventListener('click',fechar);
 q('.l-fundo').addEventListener('click',fechar);
 d.addEventListener('keydown',function(e){
  if(L.hidden)return;
  if(e.key==='Escape'){fechar();return}
  if(e.key==='Tab'){
   var fs=[].slice.call(L.querySelectorAll('a[href],button')).filter(function(x){return x.offsetParent!==null});
   if(!fs.length)return;var p=fs[0],u=fs[fs.length-1];
   if(e.shiftKey&&d.activeElement===p){e.preventDefault();u.focus()}else if(!e.shiftKey&&d.activeElement===u){e.preventDefault();p.focus()}
  }
 });
})();
"""

LEITOR = """<div class="leitor" id="leitor" role="dialog" aria-modal="true" aria-labelledby="l-tit" hidden>
<div class="l-fundo"></div>
<div class="l-caixa">
<div class="l-barra"><span class="chip l-secao"></span><div class="l-acoes"><a class="btn" id="l-orig" href="#" target="_blank" rel="noopener noreferrer">Read original &#8599;</a><button id="l-x" type="button" aria-label="Close">&#10005;</button></div></div>
<div class="l-grade"><div class="l-main"><h2 id="l-tit"></h2><p class="l-deck"></p><div class="l-meta"></div><figure class="l-midia" hidden></figure><div class="l-texto"></div><p class="l-aviso">Summary written by AI from the source's feed. Read the original for the full story.</p></div>
<aside class="l-mais" hidden><h3>More in <span></span></h3><div id="l-lista"></div></aside></div>
</div></div>"""


def esc(s):
    return html.escape(s or "", quote=True)


def sem_acento(t):
    return unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower().strip()


def canonica(secao):
    k = sem_acento(secao)
    for nome in ORDEM:
        if k == nome.lower():
            return nome
    return ALIAS.get(k, "Other")


def cor(secao):
    return SECOES.get(sem_acento(secao), "#475569")


def ha(q, agora):
    if not q:
        return ""
    h = (agora - q).total_seconds() / 3600
    if h < 1:
        return "Just now"
    return f"{int(h)}h ago" if h < 24 else f"{int(h // 24)}d ago"


def foto(it):
    return f'<img src="{esc(it["imagem"])}" alt="" loading="lazy" onerror="this.remove()">' if it.get("imagem") else ""


def miolo(it, tag):
    hora = f'<span>{esc(it["hora"])}</span>' if it.get("hora") else ""
    return (f'<div class="corpo"><span class="chip">{esc(it["secao"])}</span>'
            f'<{tag}><a href="{esc(it["link"])}">{esc(it["titulo"])}</a></{tag}>'
            f'<p>{esc(it["resumo"])}</p><div class="rodape"><span>{esc(it["fonte"])}</span>{hora}</div></div>')


def cartao(it, classe="card"):
    sem = "" if it.get("imagem") else " sem"
    return f'<article class="{classe}{sem}" style="--cor:{cor(it["secao"])}" data-i="{it["_i"]}">{foto(it)}{miolo(it, "h3")}</article>'


def hero(it):
    return f'<article class="hero" style="--cor:{cor(it["secao"])}" data-i="{it["_i"]}">{foto(it)}{miolo(it, "h2")}</article>'


def aba(id_, nome, cor_, n, ativa=False):
    return (f'<button class="aba" type="button" role="tab" id="aba-{id_}" data-id="{id_}" aria-controls="p-{id_}" '
            f'aria-selected="{"true" if ativa else "false"}" style="--cor:{cor_}"><i class="ponto"></i>{esc(nome)}'
            f'<span class="n">{n}</span></button>')


def tile(it, extra="", longo=False):
    sem = "" if it.get("imagem") else " txt"
    p = f'<p>{esc(it["resumo"])}</p>' if longo else ""
    hora = f' · {esc(it["hora"])}' if it.get("hora") else ""
    return (f'<article class="tile {extra}{sem}" style="--cor:{cor(it["secao"])}" data-i="{it["_i"]}">{foto(it)}'
            f'<div class="in"><span class="chip">{esc(it["secao"])}</span>'
            f'<h3><a href="{esc(it["link"])}">{esc(it["titulo"])}</a></h3>{p}<small>{esc(it["fonte"])}{hora}</small></div></article>')


def im(it, cls="im"):
    return f'<div class="{cls}">{foto(it)}</div>' if it.get("imagem") else ""


def meta(it):
    hora = f' · {esc(it["hora"])}' if it.get("hora") else ""
    return f'<div class="meta">{esc(it["fonte"])}{hora}</div>'


def ed_hero(it):
    return (f'<article class="ed-hero" style="--cor:{cor(it["secao"])}" data-i="{it["_i"]}">'
            f'<div class="im">{foto(it)}</div><div class="k" style="margin-top:14px">{esc(it["secao"])}</div>'
            f'<h2><a class="h3a" href="{esc(it["link"])}">{esc(it["titulo"])}</a></h2><p>{esc(it["resumo"])}</p>{meta(it)}</article>')


def ed_row(it):
    sem = "" if it.get("imagem") else " sem"
    return (f'<article class="row{sem}" style="--cor:{cor(it["secao"])}" data-i="{it["_i"]}">{im(it)}'
            f'<div><div class="k">{esc(it["secao"])}</div><h3><a class="h3a" href="{esc(it["link"])}">{esc(it["titulo"])}</a></h3>{meta(it)}</div></article>')


def ed_card(it):
    return (f'<article class="cd" style="--cor:{cor(it["secao"])}" data-i="{it["_i"]}">{im(it)}'
            f'<div class="k">{esc(it["secao"])}</div><h3><a class="h3a" href="{esc(it["link"])}">{esc(it["titulo"])}</a></h3>'
            f'<p>{esc(it["resumo"])}</p>{meta(it)}</article>')


def painel_secao(nome, lista):
    id_ = sem_acento(nome)
    cards = "".join(ed_card(i) for i in lista)
    return (f'<section class="painel" id="p-{id_}" role="tabpanel" aria-labelledby="aba-{id_}" style="--cor:{cor(nome)}">'
            f'<div class="cab-secao"><h2>{esc(nome)}</h2><span>{len(lista)} {"story" if len(lista) == 1 else "stories"}</span></div>'
            f'<div class="grade-ed">{cards}</div></section>')


def painel_todas(todas, agora):
    blocos = []
    for g in todas:
        lis = []
        for it in g["itens"]:
            hora = ha(it["q"], agora)
            peq = f"<small>{esc(hora)}</small>" if hora else ""
            lis.append(f'<li><a href="{esc(it["l"])}" target="_blank" rel="noopener noreferrer">{esc(it["t"])}</a>{peq}</li>')
        resto = lis[10:]
        mais = f'<details><summary>Show {len(resto)} more</summary><ul>{"".join(resto)}</ul></details>' if resto else ""
        blocos.append(f'<section class="fonte-card"><h3>{esc(g["nome"])} <span>{len(lis)}</span></h3>'
                      f'<ul>{"".join(lis[:10])}</ul>{mais}</section>')
    total = sum(len(g["itens"]) for g in todas)
    return (f'<section class="painel" id="p-todas" role="tabpanel" aria-labelledby="aba-todas" style="--cor:#475569">'
            f'<div class="cab-secao"><h2>All headlines</h2><span>{total} headlines from {len(todas)} sources</span></div>'
            f'<div class="fontes-grade">{"".join(blocos)}</div></section>')


def pagina(itens, agora, todas=None):
    quando = f"{DIAS[agora.weekday()]}, {MESES[agora.month - 1]} {agora.day}, {agora.year}"
    for it in itens:
        it["secao"] = canonica(it.get("secao"))
        it["hora"] = ha(it.get("quando"), agora)
    for n, it in enumerate(itens):
        it["_i"] = n
    dados = [{"t": i["titulo"], "r": i["resumo"], "d": i.get("detalhe") or "", "s": i["secao"], "c": cor(i["secao"]),
              "f": i["fonte"], "h": i.get("hora", ""), "l": i["link"], "i": i.get("imagem", ""), "v": video_id(i)}
             for i in itens]
    dados_json = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028")
    capa = itens[:OPC["total_itens"]]
    minutos = max(1, round(sum(len((i["titulo"] + " " + i["resumo"]).split()) for i in capa) / 200))
    capa = itens
    topo = (f'<div class="ed-top">{ed_hero(capa[0])}<div class="ed-lado">' + "".join(ed_row(i) for i in capa[1:6]) + '</div></div>')
    if capa[6:18]:
        topo += '<div class="sec-ed">Top stories</div><div class="grade-ed">' + "".join(ed_card(i) for i in capa[6:18]) + '</div>'
    if capa[18:]:
        topo += (f'<div class="sec-ed">More stories<span>{len(capa) - 18} more</span></div>'
                 '<div class="lista-ed">' + "".join(ed_row(i) for i in capa[18:]) + '</div>')
    grade = ""
    grupos = {n: [i for i in itens if i["secao"] == n] for n in ORDEM}
    grupos = {n: l for n, l in grupos.items() if l}
    abas = aba("capa", "Front page", "#D7263D", len(capa), True) + "".join(
        aba(sem_acento(n), n, cor(n), len(l)) for n, l in grupos.items())
    paineis = (f'<section class="painel" id="p-capa" role="tabpanel" aria-labelledby="aba-capa" style="--cor:#D7263D">'
               f'{topo}{grade}</section>') + "".join(painel_secao(n, l) for n, l in grupos.items())
    if todas:
        abas += aba("todas", "All headlines", "#475569", sum(len(g["itens"]) for g in todas))
        paineis += painel_todas(todas, agora)
    return f"""<!doctype html><html lang="en" class="no-js"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>{esc(OPC["titulo"])} – {quando}</title>
<script>document.documentElement.className="js";try{{var v=localStorage.getItem("jm-tema");if(v)document.documentElement.setAttribute("data-theme",v)}}catch(e){{}}</script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800&family=Source+Serif+4:wght@400;600&display=swap">
<style>{CSS}</style></head><body>
<header class="topo"><div class="faixa"><div><h1>{esc(OPC["titulo"])}</h1><span class="data">{quando}</span></div>
<div class="metas"><span class="pilula">{len(itens)} stories</span><span class="pilula">{minutos} min front page</span>
<button class="tema" id="tema" type="button" aria-label="Switch between light and dark theme">&#9680;</button></div></div></header>
<nav class="abas" aria-label="Sections"><div class="abas-in" role="tablist">{abas}</div></nav>
<main>{paineis}</main>
<footer>Edition assembled at {agora:%H:%M} (Brasília time) with Gemini summaries. Always check the original source.</footer>
{LEITOR}
<script type="application/json" id="dados">{dados_json}</script>
<script>{JS}</script></body></html>"""


def main():
    coletados = []
    for k, f in enumerate(CFG["fontes"]):
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
        print(f"{f['nome']}: {len(itens)} itens ({sum(1 for i in itens if i.get('extra'))} extras)")
        for it in itens:
            it["src"] = k
        coletados += itens
    if not coletados:
        sys.exit("Nenhum item coletado; a edição anterior continua no ar.")
    for n, it in enumerate(coletados):
        it["id"] = n
    por_id, usados, finais = {i["id"]: i for i in coletados}, set(), []
    principais = [i for i in coletados if not i.get("extra")]
    escolhidos = gemini(prompt(principais, OPC.get("total_abas", 80), OPC["total_itens"]))
    print(f"Gemini devolveu {len(escolhidos)} itens.")
    for e in escolhidos:
        try:
            base = por_id.get(int(re.sub(r"\D", "", str(e.get("id")))))
        except ValueError:
            base = None
        if base and base["id"] not in usados:
            usados.add(base["id"])
            finais.append({**base, "titulo": e.get("titulo") or base["titulo"],
                           "resumo": e.get("resumo") or base["resumo"], "secao": canonica(e.get("secao"))})
    if len(finais) < 5:
        sys.exit("O Gemini devolveu itens de menos; a edição anterior continua no ar.")
    # manchetes obrigatórias: as N mais recentes de cada fonte entram sempre
    n_obrig, contagem, obrig = OPC.get("obrigatorias_por_fonte", 2), {}, []
    ids_final = {i["id"] for i in finais}
    for it in principais:
        contagem[it["src"]] = contagem.get(it["src"], 0) + 1
        if contagem[it["src"]] <= n_obrig and it["id"] not in ids_final:
            obrig.append(it)
    obrig_ids = {i["id"] for i in obrig}
    resto = [i for i in coletados if i["id"] not in ids_final and i["id"] not in obrig_ids]
    extra = complementar(obrig + resto, obrig_ids)
    for it in obrig:
        e = extra.get(it["id"], {})
        finais.append({**it, "titulo": e.get("titulo") or it["titulo"],
                       "resumo": e.get("resumo") or (e.get("titulo") if PT.search(it["resumo"]) else it["resumo"][:240]),
                       "secao": canonica(e.get("secao"))})
    titulos = {i["id"]: i["titulo"] for i in finais}
    for it in resto:
        titulos[it["id"]] = extra.get(it["id"], {}).get("titulo") or it["titulo"]
    print(f"Obrigatórias adicionadas: {len(obrig)} | só manchetes: {len(resto)}")
    vistas = set()
    for n, it in enumerate(finais):
        if it["imagem"] in vistas:
            it["imagem"] = ""
        if not it["imagem"] and n < OPC["total_itens"]:
            it["imagem"] = og_image(it["link"])
            if it["imagem"] in vistas:
                it["imagem"] = ""
        if it["imagem"]:
            vistas.add(it["imagem"])
    det = {}
    try:
        for e in gemini(prompt_detalhe(finais[:OPC["total_itens"]])):
            try:
                det[int(re.sub(r"\D", "", str(e.get("id"))))] = (e.get("detalhe") or "").strip()
            except ValueError:
                pass
        print(f"Textos longos gerados para {sum(1 for v in det.values() if v)} notícias.")
    except Exception as e:
        print(f"[aviso] textos longos não gerados: {e}")
    for it in finais:
        it["detalhe"] = det.get(it["id"], "")
    agora = datetime.datetime.now(ZoneInfo("America/Sao_Paulo"))
    por_fonte = {}
    for it in coletados:
        por_fonte.setdefault(it["fonte"], []).append(it)
    sem_data = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    todas = []
    for nome, lst in por_fonte.items():
        vistos_l, linhas = set(), []
        for it in sorted(lst, key=lambda x: x.get("quando") or sem_data, reverse=True):
            if it["link"] not in vistos_l:
                vistos_l.add(it["link"])
                linhas.append({"t": titulos.get(it["id"], it["titulo"]), "l": it["link"], "q": it.get("quando")})
        todas.append({"nome": nome, "itens": linhas})
    por_src, outras = {}, []
    for it in resto:
        por_src.setdefault(it["src"], []).append(it)
    for rodada in range(3):
        for k in sorted(por_src):
            if rodada < len(por_src[k]) and len(outras) < 15:
                outras.append({"t": titulos[por_src[k][rodada]["id"]], "f": por_src[k][rodada]["fonte"]})
    saida = pagina(finais, agora, todas)
    os.makedirs(os.path.join(RAIZ, "docs"), exist_ok=True)
    json.dump({"itens": [{"t": i["titulo"], "r": i["resumo"], "d": i.get("detalhe", ""), "s": i["secao"], "f": i["fonte"]}
                         for i in finais], "outras": outras},
              open(os.path.join(RAIZ, "docs", "noticias.json"), "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.join(RAIZ, "docs", "edicoes"), exist_ok=True)
    for caminho in ("index.html", f"edicoes/{agora:%Y-%m-%d}.html"):
        open(os.path.join(RAIZ, "docs", caminho), "w", encoding="utf-8").write(saida)
    print(f"Edição de {agora:%d/%m/%Y} pronta com {len(finais)} notícias.")


if __name__ == "__main__":
    main()
