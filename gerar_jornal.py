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


ORDEM = ["Brazil", "Politics", "World", "Economy", "Sports", "Technology", "Science", "Space", "Koreas",
         "Video", "Health", "Culture", "Other"]
ALIAS = {"brasil": "Brazil", "politica": "Politics", "mundo": "World", "economia": "Economy", "esporte": "Sports",
         "tecnologia": "Technology", "ciencia": "Science", "espaco": "Space", "coreias": "Koreas",
         "saude": "Health", "cultura": "Culture", "outros": "Other", "tech": "Technology", "sport": "Sports"}
SECOES = {"politics": "#1D4ED8", "world": "#0F766E", "economy": "#15803D", "sports": "#C2410C",
          "technology": "#6D28D9", "health": "#BE185D", "brazil": "#A16207", "culture": "#7E22CE",
          "science": "#0E7490", "space": "#3730A3", "koreas": "#A21CAF", "video": "#B91C1C"}
SANS = '"Libre Franklin",system-ui,-apple-system,"Segoe UI",sans-serif'
DARK = ("--fundo:#0B1220;--cartao:#121B2E;--tinta:#E8EDF6;--suave:#9AA7BC;--linha:#233049;--marca:#070D1A;"
        "--marca2:#0E2347;--sombra:0 1px 2px rgba(0,0,0,.35);--alta:0 12px 30px rgba(0,0,0,.5);color-scheme:dark")

CSS = """
:root{--fundo:#F4F6FA;--cartao:#fff;--tinta:#0F172A;--suave:#5B6678;--linha:#E3E8F0;--marca:#0B1F3A;--marca2:#14325F;--alerta:#D7263D;
--sombra:0 1px 2px rgba(15,23,42,.05),0 4px 14px rgba(15,23,42,.06);--alta:0 2px 4px rgba(15,23,42,.06),0 14px 32px rgba(15,23,42,.16);color-scheme:light}
:root[data-theme="dark"]{@@DARK@@}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){@@DARK@@}}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--fundo);color:var(--tinta);font:17px/1.6 "Source Serif 4",Georgia,serif;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
a:focus-visible,button:focus-visible{outline:3px solid var(--alerta);outline-offset:2px}
.topo{background:linear-gradient(135deg,var(--marca),var(--marca2));color:#fff;border-bottom:4px solid var(--alerta)}
.faixa{max-width:1200px;margin:0 auto;padding:24px 20px;display:flex;flex-wrap:wrap;gap:14px 24px;align-items:center;justify-content:space-between}
h1{font:800 clamp(1.9rem,5vw,3rem)/1 SANS;letter-spacing:-.03em;margin:0}
.data{display:block;margin-top:8px;color:#B9C4D6;font:500 .95rem SANS}
.metas{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font:600 .85rem SANS}
.pilula{background:rgba(255,255,255,.12);padding:6px 12px;border-radius:99px}
.tema{width:36px;height:36px;border-radius:50%;border:1px solid rgba(255,255,255,.28);background:transparent;color:#fff;font-size:1.05rem;cursor:pointer}
.tema:hover{background:rgba(255,255,255,.14)}
.abas{position:sticky;top:0;z-index:20;background:var(--fundo);border-bottom:1px solid var(--linha)}
@supports(backdrop-filter:blur(8px)){.abas{background:color-mix(in srgb,var(--fundo) 86%,transparent);backdrop-filter:blur(10px)}}
.abas-in{position:relative;max-width:1200px;margin:0 auto;display:flex;gap:8px;overflow-x:auto;padding:10px 20px;scrollbar-width:none}
.abas-in::-webkit-scrollbar{display:none}
.aba{flex:none;display:inline-flex;align-items:center;gap:8px;border:1px solid var(--linha);background:var(--cartao);color:var(--tinta);font:600 .9rem SANS;padding:8px 14px;border-radius:99px;cursor:pointer;transition:background .15s,border-color .15s,color .15s}
.aba:hover{border-color:var(--cor)}
.aba[aria-selected="true"]{background:var(--cor);border-color:var(--cor);color:#fff}
.ponto{width:8px;height:8px;border-radius:50%;background:var(--cor)}
.aba[aria-selected="true"] .ponto{background:#fff}
.n{font-size:.8rem;font-weight:700;opacity:.6}
.no-js .abas{display:none}
main{max-width:1200px;margin:0 auto;padding:26px 20px 44px}
.painel{animation:entra .25s ease}
@keyframes entra{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.destaque{display:grid;grid-template-columns:1.7fr 1fr;gap:20px;margin-bottom:38px}
.hero{position:relative;min-height:470px;border-radius:18px;overflow:hidden;color:#fff;display:flex;align-items:flex-end;background:linear-gradient(135deg,var(--cor),#0B1F3A);box-shadow:var(--alta)}
.hero img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.hero::after{content:"";position:absolute;inset:0;background:linear-gradient(to top,rgba(6,12,24,.93) 0%,rgba(6,12,24,.55) 45%,rgba(6,12,24,0) 78%)}
.hero .corpo{position:relative;z-index:1;padding:30px;width:100%}
.hero h2{font:800 clamp(1.7rem,3.6vw,2.7rem)/1.1 SANS;letter-spacing:-.02em;margin:.55rem 0}
.hero h2 a:hover{text-decoration:underline}
.hero p{font-size:1.1rem;color:#E3E9F3;margin:0 0 .8rem;max-width:62ch}
.hero .rodape{color:#C9D3E3}
.lateral{display:grid;gap:14px;align-content:start}
.card,.mini{position:relative;background:var(--cartao);border:1px solid var(--linha);border-top:3px solid var(--cor);border-radius:14px;overflow:hidden;box-shadow:var(--sombra);transition:transform .18s,box-shadow .18s}
.card:hover,.mini:hover{transform:translateY(-3px);box-shadow:var(--alta)}
.card img{width:100%;aspect-ratio:16/10;object-fit:cover;display:block;background:var(--linha);transition:transform .5s}
.card:hover img{transform:scale(1.04)}
.corpo{padding:15px 17px 17px}
.mini{display:grid;grid-template-columns:116px 1fr;border-top-width:1px;border-left:4px solid var(--cor)}
.mini.sem{grid-template-columns:1fr}
.mini img{width:100%;height:100%;object-fit:cover;background:var(--linha)}
.chip{display:inline-block;background:var(--cor);color:#fff;font:700 .75rem/1 SANS;padding:5px 10px;border-radius:99px}
h3{font:700 1.12rem/1.27 SANS;letter-spacing:-.01em;margin:.6rem 0 .4rem}
.mini h3{font-size:1rem}
.card p,.mini p{margin:0 0 .65rem;color:var(--suave);font-size:.98rem;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.mini p{-webkit-line-clamp:2}
.rodape{display:flex;justify-content:space-between;gap:12px;font:600 .8rem SANS;color:var(--suave)}
.card h3 a::after,.mini h3 a::after{content:"";position:absolute;inset:0}
.card.grande{grid-column:span 2;display:grid;grid-template-columns:1.25fr 1fr}
.card.grande img{height:100%;min-height:270px;aspect-ratio:auto}
.card.grande .corpo{align-self:center;padding:24px}
.card.grande h3{font-size:1.65rem;line-height:1.2}
.card.grande p{-webkit-line-clamp:5;font-size:1.05rem}
.secao{font:800 1.3rem SANS;margin:0 0 18px;padding-left:12px;border-left:5px solid var(--alerta)}
.cab-secao{display:flex;align-items:baseline;gap:14px;margin-bottom:20px;padding-left:14px;border-left:6px solid var(--cor)}
.cab-secao h2{font:800 1.8rem/1.1 SANS;margin:0;letter-spacing:-.02em}
.cab-secao span{color:var(--suave);font:600 .9rem SANS}
.grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px}
footer{max-width:1200px;margin:0 auto;padding:0 20px 34px;color:var(--suave);font:500 .85rem SANS}
@media(max-width:900px){.destaque{grid-template-columns:1fr}.hero{min-height:390px}}
@media(max-width:820px){.card.grande{grid-column:auto;display:block}.card.grande img{aspect-ratio:16/10;min-height:0}}
@media(max-width:520px){.mini{grid-template-columns:96px 1fr}.hero .corpo{padding:20px}.faixa{padding:18px 20px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.painel{animation:none}*{transition:none!important}}
@media print{.topo{background:#fff;color:#000}.data{color:#444}.abas,.tema{display:none}.painel[hidden]{display:block!important}.card,.mini,.hero{break-inside:avoid;box-shadow:none}}
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
"""


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
    return f'<article class="{classe}{sem}" style="--cor:{cor(it["secao"])}">{foto(it)}{miolo(it, "h3")}</article>'


def hero(it):
    return f'<article class="hero" style="--cor:{cor(it["secao"])}">{foto(it)}{miolo(it, "h2")}</article>'


def aba(id_, nome, cor_, n, ativa=False):
    return (f'<button class="aba" type="button" role="tab" id="aba-{id_}" data-id="{id_}" aria-controls="p-{id_}" '
            f'aria-selected="{"true" if ativa else "false"}" style="--cor:{cor_}"><i class="ponto"></i>{esc(nome)}'
            f'<span class="n">{n}</span></button>')


def painel_secao(nome, lista):
    id_ = sem_acento(nome)
    cards = "".join(
        cartao(i, "card grande" if (n == 0 and i.get("imagem") and len(lista) > 1) else "card")
        for n, i in enumerate(lista))
    return (f'<section class="painel" id="p-{id_}" role="tabpanel" aria-labelledby="aba-{id_}" style="--cor:{cor(nome)}">'
            f'<div class="cab-secao"><h2>{esc(nome)}</h2><span>{len(lista)} {"story" if len(lista) == 1 else "stories"}</span></div>'
            f'<div class="grade">{cards}</div></section>')


def pagina(itens, agora):
    quando = f"{DIAS[agora.weekday()]}, {MESES[agora.month - 1]} {agora.day}, {agora.year}"
    for it in itens:
        it["secao"] = canonica(it.get("secao"))
        it["hora"] = ha(it.get("quando"), agora)
    capa = itens[:OPC["total_itens"]]
    minutos = max(1, round(sum(len((i["titulo"] + " " + i["resumo"]).split()) for i in capa) / 200))
    topo = (f'<section class="destaque">{hero(capa[0])}'
            f'<div class="lateral">{"".join(cartao(i, "mini") for i in capa[1:4])}</div></section>')
    grade = (f'<h2 class="secao">More stories</h2><div class="grade">{"".join(cartao(i) for i in capa[4:])}</div>'
             if len(capa) > 4 else "")
    grupos = {n: [i for i in itens if i["secao"] == n] for n in ORDEM}
    grupos = {n: l for n, l in grupos.items() if l}
    abas = aba("capa", "Front page", "#D7263D", len(capa), True) + "".join(
        aba(sem_acento(n), n, cor(n), len(l)) for n, l in grupos.items())
    paineis = (f'<section class="painel" id="p-capa" role="tabpanel" aria-labelledby="aba-capa" style="--cor:#D7263D">'
               f'{topo}{grade}</section>') + "".join(painel_secao(n, l) for n, l in grupos.items())
    return f"""<!doctype html><html lang="en" class="no-js"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>{esc(OPC["titulo"])} – {quando}</title>
<script>document.documentElement.className="js";try{{var v=localStorage.getItem("jm-tema");if(v)document.documentElement.setAttribute("data-theme",v)}}catch(e){{}}</script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@500;600;700;800&family=Source+Serif+4:wght@400;600&display=swap">
<style>{CSS}</style></head><body>
<header class="topo"><div class="faixa"><div><h1>{esc(OPC["titulo"])}</h1><span class="data">{quando}</span></div>
<div class="metas"><span class="pilula">{len(itens)} stories</span><span class="pilula">{minutos} min front page</span>
<button class="tema" id="tema" type="button" aria-label="Switch between light and dark theme">&#9680;</button></div></div></header>
<nav class="abas" aria-label="Sections"><div class="abas-in" role="tablist">{abas}</div></nav>
<main>{paineis}</main>
<footer>Edition assembled at {agora:%H:%M} (Brasília time) with Gemini summaries. Always check the original source.</footer>
<script>{JS}</script></body></html>"""


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
    escolhidos = gemini(prompt(coletados, OPC.get("total_abas", 80), OPC["total_itens"]))
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
    agora = datetime.datetime.now(ZoneInfo("America/Sao_Paulo"))
    saida = pagina(finais, agora)
    os.makedirs(os.path.join(RAIZ, "docs", "edicoes"), exist_ok=True)
    for caminho in ("index.html", f"edicoes/{agora:%Y-%m-%d}.html"):
        open(os.path.join(RAIZ, "docs", caminho), "w", encoding="utf-8").write(saida)
    print(f"Edição de {agora:%d/%m/%Y} pronta com {len(finais)} notícias.")


if __name__ == "__main__":
    main()
