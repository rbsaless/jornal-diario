#!/usr/bin/env python3
"""Gera a primeira página do jornal do dia em docs/index.html (só biblioteca padrão)."""
import datetime, html, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

RAIZ = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(RAIZ, "fontes.json"), encoding="utf-8"))
OPC = CFG["configuracoes"]
MODELOS = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"] if m]
MODELO = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
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


def ler_feed(fonte):
    raiz = ET.fromstring(baixar(fonte["url"]))
    limite = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=OPC["horas_recentes"])
    itens = []
    for el in raiz.iter():
        if ln(el.tag) not in ("item", "entry"):
            continue
        d = {}
        for c in el:
            d.setdefault(ln(c.tag), c)
        txt = lambda k: (d[k].text or "") if k in d else ""
        link = (d["link"].get("href") or d["link"].text or "").strip() if "link" in d else ""
        blob = txt("encoded") + txt("description") + txt("summary") + txt("content")
        quando = next((data(txt(k)) for k in ("pubDate", "published", "updated", "date") if txt(k)), None)
        if not link or (quando and quando < limite):
            continue
        itens.append({"fonte": fonte["nome"], "titulo": limpar(txt("title"), 200), "link": link,
                      "resumo": limpar(blob, 400), "imagem": imagem_do_item(el, blob), "quando": quando})
    itens.sort(key=lambda i: i["quando"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    return itens[:OPC["itens_por_fonte"]]


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
    return itens[:OPC["itens_por_fonte"]]


def og_image(link):
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


def prompt(itens, n):
    linhas = "\n".join(f'[{i["id"]}] ({i["fonte"]}) {i["titulo"]} :: {i["resumo"][:300]}' for i in itens)
    return f"""Você é o editor-chefe de um jornal diário em português do Brasil. Abaixo estão {len(itens)} notícias coletadas de várias fontes (algumas em inglês).
Escolha as {n} mais importantes e relevantes. Elimine duplicatas (o mesmo fato em fontes diferentes: fique com uma), corte ruído, sensacionalismo e trivialidades, e equilibre os assuntos (política, mundo, economia, esporte, tecnologia etc.), sem deixar uma única fonte dominar.
Ordene da mais para a menos importante: a primeira é a manchete.
Escreva TUDO em português do Brasil, fiel ao texto original, sem inventar fatos.
Para cada notícia escolhida devolva: id (o número entre colchetes), titulo (manchete clara, até 90 caracteres), resumo (2 frases; para a primeira, 4 frases), secao (Brasil, Política, Mundo, Economia, Esporte, Tecnologia, Saúde, Cultura ou Outros).
Responda SOMENTE com JSON: {{"itens":[{{"id":0,"titulo":"...","resumo":"...","secao":"..."}}]}}

NOTÍCIAS:
{linhas}"""


def gemini(texto):
    corpo = {"contents": [{"parts": [{"text": texto}]}],
             "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent",
        data=json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()})
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                saida = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(?:json)?|```$", "", saida.strip()).strip())["itens"]
        except (urllib.error.HTTPError, ValueError, KeyError, IndexError) as e:
            if tentativa == 3 or (isinstance(e, urllib.error.HTTPError) and e.code not in (429, 500, 503)):
                raise
            time.sleep(20 * (tentativa + 1))


CSS = """
:root{--papel:#E9E8E4;--tinta:#1C2128;--suave:#59606A;--regua:#B8B6AE;--azul:#1F3A5F}
*{box-sizing:border-box}
body{margin:0;background:var(--papel);color:var(--tinta);font:18px/1.55 Newsreader,Georgia,serif}
.pagina{max-width:1180px;margin:0 auto;padding:24px 20px 48px}
header{text-align:center;border-bottom:3px double var(--tinta);padding-bottom:14px;margin-bottom:24px}
.nome{font:900 clamp(2.4rem,7vw,4.8rem)/1 "Bodoni Moda",Georgia,serif;margin:0}
.data{margin-top:8px;color:var(--suave);font-style:italic}
.topo{display:grid;grid-template-columns:2fr 1fr;gap:28px;border-bottom:1px solid var(--regua);padding-bottom:26px;margin-bottom:26px}
.manchete img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;margin-bottom:12px}
.manchete h2{font:700 clamp(1.9rem,4vw,3rem)/1.08 "Bodoni Moda",Georgia,serif;margin:.15em 0 .25em}
.manchete p{font-size:1.12rem}
.laterais{border-left:1px solid var(--regua);padding-left:24px}
.colunas{column-count:3;column-gap:32px;column-rule:1px solid var(--regua)}
.item{break-inside:avoid;margin:0 0 24px;padding-bottom:20px;border-bottom:1px solid var(--regua)}
.item:last-child{border-bottom:0}
.item img{width:100%;aspect-ratio:3/2;object-fit:cover;display:block;margin-bottom:8px}
h3{font:700 1.28rem/1.18 "Bodoni Moda",Georgia,serif;margin:.1em 0 .3em}
.sec{color:var(--azul);font-style:italic;font-size:.95rem}
p{margin:.3em 0}.fonte{color:var(--suave);font-size:.9rem}
a{color:inherit;text-decoration-color:var(--regua)}
a:hover,a:focus-visible{text-decoration-color:var(--azul)}
footer{margin-top:8px;color:var(--suave);font-size:.9rem;text-align:center}
@media(max-width:900px){.topo{grid-template-columns:1fr}.laterais{border-left:0;padding-left:0}.colunas{column-count:2}}
@media(max-width:600px){.colunas{column-count:1}body{font-size:17px}}
@media print{body{background:#fff;font-size:11pt}.pagina{padding:0}}
"""


def esc(s):
    return html.escape(s or "", quote=True)


def bloco(it, tag="h3", classe="item"):
    img = f'<img src="{esc(it["imagem"])}" alt="" loading="lazy" onerror="this.remove()">' if it.get("imagem") else ""
    return (f'<article class="{classe}">{img}<span class="sec">{esc(it["secao"])}</span>'
            f'<{tag}><a href="{esc(it["link"])}">{esc(it["titulo"])}</a></{tag}>'
            f'<p>{esc(it["resumo"])}</p><p class="fonte">{esc(it["fonte"])}</p></article>')


def pagina(itens, agora):
    quando = f"{DIAS[agora.weekday()]}, {agora.day} de {MESES[agora.month - 1]} de {agora.year}"
    topo = (f'<section class="topo">{bloco(itens[0], "h2", "manchete")}'
            f'<div class="laterais">{"".join(bloco(i) for i in itens[1:4])}</div></section>')
    resto = f'<section class="colunas">{"".join(bloco(i) for i in itens[4:])}</section>'
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(OPC["titulo"])} – {quando}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@700;900&family=Newsreader:ital,wght@0,400;0,600;1,400&display=swap">
<style>{CSS}</style></head><body><div class="pagina">
<header><h1 class="nome">{esc(OPC["titulo"])}</h1><div class="data">{quando}</div></header>
{topo}{resto}
<footer>Edição montada às {agora:%H:%M} com resumos do Gemini. Confira sempre a fonte original.</footer>
</div></body></html>"""


def main():
    coletados = []
    for f in CFG["fontes"]:
        itens = []
        try:
            itens = ler_feed(f)
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
    for e in gemini(prompt(coletados, OPC["total_itens"])):
        base = por_id.get(e.get("id"))
        if base and base["id"] not in usados:
            usados.add(base["id"])
            finais.append({**base, "titulo": e["titulo"], "resumo": e["resumo"], "secao": e.get("secao", "Outros")})
    if len(finais) < 5:
        sys.exit("O Gemini devolveu itens de menos; a edição anterior continua no ar.")
    for it in finais:
        it["imagem"] = it["imagem"] or og_image(it["link"])
    agora = datetime.datetime.now(ZoneInfo("America/Sao_Paulo"))
    saida = pagina(finais, agora)
    os.makedirs(os.path.join(RAIZ, "docs", "edicoes"), exist_ok=True)
    for caminho in ("index.html", f"edicoes/{agora:%Y-%m-%d}.html"):
        open(os.path.join(RAIZ, "docs", caminho), "w", encoding="utf-8").write(saida)
    print(f"Edição de {agora:%d/%m/%Y} pronta com {len(finais)} notícias.")


if __name__ == "__main__":
    main()
