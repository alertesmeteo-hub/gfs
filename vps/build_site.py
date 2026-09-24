#!/usr/bin/env python3
"""Archive pluiesextremes.meteo.fr (Wayback 09/11/2022) -> site statique evenements-majeurs.alertes-meteo.com

Seul le texte des fiches est repris, sous forme de résumé factuel ; la page originale reste accessible sur la Wayback Machine.
"""
import os, re, html, json, time, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from departements import NOMS, REGIONS, detecter
from resume import resumer

REDIGES = {}
for _f in sorted(__import__('glob').glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resumes', '*.json'))):
    REDIGES.update(json.load(open(_f, encoding='utf-8')))
TS = '20221109122403'
BASE = 'http://pluiesextremes.meteo.fr/france-metropole/'
WB = f'https://web.archive.org/web/{TS}id_/'
ARCH = f'https://web.archive.org/web/{TS}/'
ROOT = sys.argv[1] if len(sys.argv) > 1 else '/var/www/evenements-majeurs'
CACHE = os.path.join(ROOT, '_cache'); EV = os.path.join(ROOT, 'evenements')
for d in (CACHE, EV): os.makedirs(d, exist_ok=True)

def get(url, dest, tries=5):
    if os.path.exists(dest) and os.path.getsize(dest) > 0: return True
    if os.environ.get('OFFLINE'): return False
    for k in range(tries):
        if subprocess.run(['curl', '-sSLf', '-m', '120', '-o', dest, url]).returncode == 0 and os.path.getsize(dest) > 0:
            time.sleep(1); return True
        time.sleep(5 * 2 ** k)
    if os.path.exists(dest): os.remove(dest)
    return False

def page(slug):
    f = os.path.join(CACHE, slug)
    return open(f, encoding='utf-8', errors='replace').read() if get(WB + BASE + slug, f) else ''

def contenu(s):
    m = re.search(r'id="a_content">(.*?)<div class="sidebar', s, re.S)
    return m.group(1) if m else ''

def texte(c):
    c = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', c, flags=re.S)
    c = re.sub(r'<br[^>]*>|</p>|</li>|</h\d>|</tr>', '\n', c)
    c = html.unescape(re.sub(r'<[^>]+>', '', c))
    return re.sub(r'\n\s*\n+', '\n\n', re.sub(r'[ \t]+', ' ', c)).strip()


VALEUR = re.compile(r"\d+(?:[,.]\d+)?\s*(?:mm|km/h|cm|m3/s|m³/s)\b")
LIGNE = re.compile(r"^[-*•]?\s*(?P<v>\d+(?:[,.]\d+)?\s*(?:mm|km/h|cm|m3/s|m³/s))\s+(?:à|a|au|aux|sur|en)\s+(?P<l>[^()/;]+?)\s*(?:\((?P<d>\d{2}|2A|2B)\s*\))?(?P<p>(?:\s*[,;]|\s+(?:dont|en|le|entre|record|soit)\b).*)?$")
INLINE = re.compile(r"(?P<v>\d+(?:[,.]\d+)?\s*(?:mm|km/h))\s+(?:à|au|aux)\s+(?P<l>(?:l['’]|la |le |les )?[A-ZÉÈÀÂÎ][\w'’.-]*(?:[ -](?:de|du|des|la|le|les|d['’]|sur|en|[A-ZÉÈÀÂÎ][\w'’.-]*))*)\s*(?:\((?P<d>\d{2}|2A|2B)\))?")
PERIODE = re.compile(r"(?i)(?:en|sur)\s+(?:les\s+|la\s+)?\d+\s*(?:h\b|heures?|jours?|min(?:utes)?)|sur la journée|en 24 ?h")
DUREE = re.compile(r"(\d+(?:[,.]\d+)?\s*mm)\s+en\s+(\d+\s*(?:h\b|heures?|min(?:utes)?|jours?))")
LIEU = re.compile(r"(?:station(?: automatique| météorologique)?(?: de| d['’]| du)?|poste(?: de| d['’])?|\bà|\bA)\s+((?:l['’]|la |le |les )?[A-ZÉÈÀÂÎ][\w'’.-]*(?:[ -](?:de|du|des|la|le|les|sur|en|[A-ZÉÈÀÂÎ][\w'’.-]*))*)")
def titre_neutre(h):
    h = h.rstrip(' :').strip('* ')
    h = re.sub(r"(?i)^(valeurs|hauteurs|cumuls|intensités)\s+(remarquables|maximales)?\s*(relevées|mesurées|recueillies)?\s*", "", h)
    h = re.sub(r"(?i)^(de pluie|des précipitations)\s*", "", h)
    h = re.sub(r"(?i)^en (\d+) jours?", r"cumuls sur \1 jours", h)
    h = h.replace("Intensités remarquables", "").strip(" ,:")
    return (h[:1].upper() + h[1:]) if h else "Relevés"
def detail(c):
    """Relevés factuels de la fiche (valeurs, stations), présentés sous forme de tableaux ; aucune prose recopiée."""
    c = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', c, flags=re.S)
    c = re.sub(r'<img[^>]*>', '', c)
    c = re.sub(r'<tr[^>]*>.*?</tr>', lambda m: re.sub(r'</?br[^>]*>|</?center>|</?p[^>]*>|</?strong>|\s+', ' ', m.group(0)), c, flags=re.S)
    c = re.sub(r'\n', ' ', c)
    c = re.sub(r'</?br[^>]*>|<p[^>]*>|</p>|<li[^>]*>|</li>|</?ul[^>]*>|</?center>|</h\d>|</tr>|</?div[^>]*>', '\n', c)
    c = re.sub(r'<td[^>]*>', ' | ', c)
    lignes = [re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', l))).strip(' |') for l in c.split('\n')]
    blocs, titre, rows = [], 'Relevés', []
    principal = ''
    def flush():
        nonlocal titre
        if rows:
            if len(titre) > 70: titre = 'Rafales relevées' if 'km/h' in rows[0][0] else 'Relevés'
            tr = ''.join(f'<tr><td class="chiffre">{html.escape(v)}</td><td>{html.escape(l)}</td><td>{html.escape(d)}</td><td>{html.escape(p)}</td></tr>' for v, l, d, p in rows)
            blocs.append(f'<h3>{html.escape(titre)}</h3><table><thead><tr><th>Valeur</th><th>Lieu</th><th>Dép.</th><th>Précision</th></tr></thead><tbody>{tr}</tbody></table>')
            rows.clear()
    tab = []
    def flush_tab():
        if len(tab) >= 2 and any(re.search(r'\d', ' '.join(r)) for r in tab[1:]):
            head, body = tab[0], tab[1:]
            th = ''.join(f'<th>{html.escape(h)}</th>' for h in head)
            tb = ''.join('<tr>' + ''.join(f'<td>{html.escape(c)}</td>' for c in r) + '</tr>' for r in body)
            blocs.append(f'<h3>{html.escape(titre)}</h3><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>')
        tab.clear()
    for l in lignes:
        if not l: continue
        if l.endswith(':') and len(l) < 160:
            flush(); flush_tab(); t = titre_neutre(l)
            if len(t) < 30 and principal: t = principal + ' – ' + t[:1].lower() + t[1:]
            else: principal = t
            titre = t; continue
        m = LIGNE.match(l)
        if m and len(l) < 200:
            p = (m.group('p') or '').strip(' ,;.')
            rows.append((m.group('v'), m.group('l').strip(' ,'), m.group('d') or '', p))
            continue
        if l.count('|') >= 2:
            cells = [x.strip() for x in l.split('|') if x.strip()]
            if cells: tab.append(cells)
            continue
        elif tab:
            flush_tab()
        inl = list(INLINE.finditer(l))
        if inl and not (l.endswith(':')):
            per = PERIODE.search(l)
            for x in inl:
                rows.append((x.group('v'), x.group('l').strip(' ,.'), x.group('d') or '', per.group(0) if per else ''))
            continue
        dur = list(DUREE.finditer(l))
        if dur and len(l) < 400:
            lieu = LIEU.findall(l[:dur[0].start()])
            for x in dur:
                rows.append((x.group(1), lieu[-1] if lieu else '', '', 'en ' + x.group(2)))
            continue
        m2 = re.match(r"^[-*•]?\s*(?P<l>[A-ZÉÈÀÂÎ][^:/]{1,50}?)\s*(?:\((?P<d>\d{2}|2A|2B)\))?\s*:\s*(?P<v>\d+(?:[,.]\d+)?\s*mm)(?P<p>.*)$", l)
        if m2:
            rows.append((m2.group('v'), m2.group('l'), m2.group('d') or '', m2.group('p').strip(' ,;.')))
    flush(); flush_tab()
    out = []
    for b in blocs:
        if out and b.startswith('<h3>Relevés</h3>') and out[-1].startswith('<h3>Relevés</h3>') and '<th>Valeur</th>' in b and '<th>Valeur</th>' in out[-1]:
            out[-1] = out[-1].replace('</tbody></table>', b.split('<tbody>',1)[1].replace('</tbody></table>','') + '</tbody></table>')
        else: out.append(b)
    blocs = out
    return ''.join(blocs) or '<p>Les valeurs de cette fiche figurent dans le résumé et les chiffres clés ci-dessus.</p>'

def liste(slug):
    s = page(slug); c = contenu(s) or s
    out, vus = [], set()
    for h, t in re.findall(r'<a href="([^"/:#?]+\.html)"[^>]*>(.*?)</a>', c, re.S):
        if h.startswith('-') or h in vus: continue
        vus.add(h); t = html.unescape(re.sub(r'<[^>]+>|\s+', ' ', t)).strip()
        d, _, ti = t.partition(' - ')
        out.append({'slug': h, 'date': d.strip() if ti else '', 'titre': (ti or t).strip()})
    return out

tous = liste('-Tous-les-evenements-.html')
majeurs = {e['slug'] for e in liste('-Selection-d-evenements-majeurs-.html')}
print(len(tous), 'événements,', len(majeurs), 'majeurs', flush=True)

CSS = """:root{--bg:#f6f8fb;--fg:#15202b;--mut:#5b6b7b;--acc:#0b5cad;--card:#fff;--bd:#dde3ea;--dep:#dfe7f0;--sel:#0b5cad;--hit:#e53935}
@media(prefers-color-scheme:dark){:root{--bg:#0f1720;--fg:#e6edf3;--mut:#9aa8b6;--acc:#5aa9ff;--card:#17212c;--bd:#2a3744;--dep:#253241;--sel:#3d8fe0;--hit:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header,main,footer{max-width:1100px;margin:auto;padding:16px}header h1{margin:.2em 0;color:var(--acc)}a{color:var(--acc)}
table{width:100%;border-collapse:collapse;background:var(--card)}th,td{padding:8px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top}
.maj{font-weight:700}.badge{background:#c62828;color:#fff;border-radius:4px;padding:1px 6px;font-size:.75em;margin-left:6px}
input[type=search]{width:100%;padding:10px;margin:8px 0 16px;border:1px solid var(--bd);border-radius:6px;background:var(--card);color:var(--fg)}
.fiche h3{font-size:1em;margin:1em 0 .3em}.fiche ul{margin:.2em 0 .8em}.fiche{margin-bottom:16px;background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:16px}.chiffre{font-size:1.15em;font-weight:700;color:var(--acc)}
footer{color:var(--mut);font-size:.85em}nav a{margin-right:14px}
.carte{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px}@media(max-width:760px){.carte{grid-template-columns:1fr}}
svg path{fill:var(--dep);stroke:var(--card);stroke-width:1.5;cursor:pointer}svg path.on{fill:var(--sel)}svg path:hover{opacity:.75}
.regions{display:flex;flex-wrap:wrap;gap:6px}.regions button{border:1px solid var(--bd);background:var(--card);color:var(--fg);border-radius:14px;padding:3px 10px;cursor:pointer;font-size:.85em}
.regions button.on{background:var(--sel);color:#fff}.annees{display:flex;gap:8px;align-items:center;margin:12px 0}.annees input{width:90px;padding:6px}"""
PIED = """<footer>Source : Météo-France – pluiesextremes.meteo.fr, archive Internet Archive du 09/11/2022. Résumés établis à partir des fiches originales, départements déduits automatiquement du texte ; les données appartiennent à Météo-France. Mise en forme : <a href="https://www.alertes-meteo.com">Alertes Météo</a>.</footer>"""
def doc(titre, corps, pre=''):
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(titre)}</title><link rel="stylesheet" href="{pre}style.css"></head><body><header><nav>'
            f'<a href="{pre}index.html">Événements majeurs</a><a href="{pre}carte.html">Recherche sur carte</a><a href="{pre}tous.html">Tous les événements</a>'
            f'<a target="_blank" rel="noopener" href="https://www.alertes-meteo.com">alertes-meteo.com</a></nav><h1>{html.escape(titre)}</h1></header><main>{corps}</main>{PIED}</body></html>')
open(os.path.join(ROOT, 'style.css'), 'w').write(CSS)

def annee(date):
    a = re.findall(r'\b(1[5-9]\d\d|20\d\d)\b', date)
    return int(a[-1]) if a else None

index = []
for i, e in enumerate(tous, 1):
    n = f'{i:03d}'; slug = e['slug'][:-5]; d = os.path.join(EV, f'{n}_{slug}'); os.makedirs(d, exist_ok=True)
    c = contenu(page(e['slug']))
    if not c: print('ABSENT', n, flush=True); continue
    t = texte(c)
    deps = detecter(e['titre'] + '\n' + t + ' ' + ' '.join(re.findall(r'dept[\d-]+', c)))
    r = resumer(t)
    maj = e['slug'] in majeurs
    cum = ''.join(f'<tr><td class="chiffre">{v:g} mm</td><td>{html.escape(l)}{f" ({dp})" if dp else ""}</td></tr>' for v, l, dp in r['cumuls'])
    raf = r['rafale']
    rr = REDIGES.get(slug)
    if rr:
        ch = ''.join(f'<tr><th>{html.escape(k)}</th><td class="chiffre">{html.escape(v)}</td></tr>' for k, v in rr['chiffres'])
        corps = (f'<p><b>{html.escape(e["date"])}</b>{" <span class=badge>Événement majeur</span>" if maj else ""}</p><div class="fiche">'
                 f'<h2>{html.escape(rr["titre"])}</h2><p>{html.escape(rr["texte"])}</p><h2>Chiffres clés</h2><table>{ch}</table>'
                 f'<h2>Départements concernés</h2><p>{", ".join(f"{NOMS[x]} ({x})" for x in deps) or "non déterminés"}</p></div>'
                 f'<p><a target="_blank" rel="noopener" href="{ARCH}{BASE}{e["slug"]}">Consulter la fiche complète de Météo-France (archive)</a></p>')
    else:
      corps = (f'<p><b>{html.escape(e["date"])}</b>{" <span class=badge>Événement majeur</span>" if maj else ""}</p><div class="fiche">'
             f'<h2>Résumé</h2><p>{html.escape(r["contexte"]) or "—"}</p>'
             + (f'<h2>Cumuls les plus élevés</h2><table>{cum}</table>' if cum else '')
             + (f'<h2>Rafale maximale</h2><p class="chiffre">{raf[0]} km/h <small>à {html.escape(raf[1])}{f" ({raf[2]})" if raf[2] else ""}</small></p>' if raf else '')
             + f'<h2>Départements concernés</h2><p>{", ".join(f"{NOMS[x]} ({x})" for x in deps) or "non déterminés"}</p></div>'
             f'<p><a target="_blank" rel="noopener" href="{ARCH}{BASE}{e["slug"]}">Consulter la fiche complète de Météo-France (archive)</a></p>')
    corps = corps.replace('<p><a target="_blank" rel="noopener" href="' + ARCH, '<div class="fiche"><h2>Relevés détaillés</h2>' + detail(c) + '</div><p><a target="_blank" rel="noopener" href="' + ARCH, 1)
    open(os.path.join(d, 'index.html'), 'w', encoding='utf-8').write(doc(f'{e["date"]} – {e["titre"]}', corps, '../../'))
    index.append({'n': n, 'date': e['date'], 'titre': e['titre'], 'annee': annee(e['date']), 'url': f'evenements/{n}_{slug}/',
                  'majeur': maj, 'deps': deps, 'max_mm': r['cumuls'][0][0] if r['cumuls'] else None})
    print('ok', n, len(deps), flush=True)

json.dump(index, open(os.path.join(ROOT, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

def fmt(v): return f'{v:g} mm' if v else ''
def table(rows):
    tr = ''.join(f'<tr><td>{r["n"]}</td><td>{html.escape(r["date"])}</td><td class="{"maj" if r["majeur"] else ""}"><a href="{r["url"]}">{html.escape(r["titre"])}</a>'
                 f'{" <span class=badge>majeur</span>" if r["majeur"] else ""}</td><td>{fmt(r["max_mm"])}</td></tr>' for r in rows)
    return ('<input type=search placeholder="Rechercher une date, une région, un département…" oninput="for(const t of document.querySelectorAll(\'tbody tr\'))t.hidden=!t.textContent.toLowerCase().includes(this.value.toLowerCase())">'
            f'<table><thead><tr><th>N°</th><th>Date</th><th>Événement</th><th>Cumul max</th></tr></thead><tbody>{tr}</tbody></table>')
maj = [r for r in index if r['majeur']]
open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8').write(doc('Pluies extrêmes en France : les événements majeurs',
    f'<p>{len(maj)} événements majeurs sélectionnés par Météo-France, sur {len(index)} épisodes de pluies extrêmes documentés en France métropolitaine. '
    f'<a href="carte.html">Rechercher par département et par période</a>.</p>' + table(maj)))
open(os.path.join(ROOT, 'tous.html'), 'w', encoding='utf-8').write(doc(f'Tous les événements ({len(index)})', table(index)))

# Page carte : sélection des départements, raccourcis régions, années
chemins = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'carte_departements.json')))
svg = ''.join(f'<path id="d{c}" d="{p}"><title>{html.escape(NOMS[c])} ({c})</title></path>' for c, p in chemins.items())
ans = [r['annee'] for r in index if r['annee']]
amin, amax = (min(ans), max(ans)) if ans else (1766, 2026)
boutons = '<button data-r="*">Tous les départements</button>' + ''.join(f'<button data-r="{html.escape(k)}">{html.escape(k)}</button>' for k in REGIONS)
data = json.dumps([{k: r[k] for k in ('n', 'date', 'titre', 'annee', 'url', 'majeur', 'deps', 'max_mm')} for r in index], ensure_ascii=False)
regions = json.dumps(REGIONS, ensure_ascii=False)
carte = f"""<p>Seuls les départements cochés sur la carte sont pris en compte. Cliquez sur un département pour le cocher ou le décocher, ou utilisez l'aide à la sélection des régions.</p>
<div class="carte"><div><svg viewBox="0 0 1060 1020" role="img" aria-label="Carte des départements">{svg}</svg></div>
<div><h2>Aide à la sélection des régions</h2><div class="regions">{boutons}</div>
<div class="annees">Années de <input type=number id=a1 value={amin} min={amin} max={amax}> à <input type=number id=a2 value={amax} min={amin} max={amax}></div>
<label><input type=checkbox id=mj> Événements majeurs uniquement</label></div></div>
<h2 id=nb></h2><table><thead><tr><th>Date</th><th>Événement</th><th>Cumul max</th></tr></thead><tbody id=res></tbody></table>
<script>
const EV={data},REG={regions},sel=new Set();
const P=[...document.querySelectorAll('svg path')];
function maj(){{P.forEach(p=>p.classList.toggle('on',sel.has(p.id.slice(1))));
 document.querySelectorAll('.regions button').forEach(b=>{{const c=b.dataset.r=='*'?P.map(p=>p.id.slice(1)):REG[b.dataset.r];b.classList.toggle('on',c.every(x=>sel.has(x))&&c.some(x=>x!='AD'))}});
 const a1=+document.getElementById('a1').value,a2=+document.getElementById('a2').value,mj=document.getElementById('mj').checked;
 const r=EV.filter(e=>(!sel.size||e.deps.some(d=>sel.has(d)))&&(!e.annee||e.annee>=a1&&e.annee<=a2)&&(!mj||e.majeur));
 document.getElementById('nb').textContent=r.length+' événement'+(r.length>1?'s':'');
 document.getElementById('res').innerHTML=r.map(e=>`<tr><td>${{e.date}}</td><td class="${{e.majeur?'maj':''}}"><a href="${{e.url}}">${{e.titre}}</a>${{e.majeur?' <span class=badge>majeur</span>':''}}</td><td>${{e.max_mm?e.max_mm+' mm':''}}</td></tr>`).join('')}}
P.forEach(p=>p.onclick=()=>{{const c=p.id.slice(1);sel.has(c)?sel.delete(c):sel.add(c);maj()}});
document.querySelectorAll('.regions button').forEach(b=>b.onclick=()=>{{const c=b.dataset.r=='*'?P.map(p=>p.id.slice(1)):REG[b.dataset.r];const tout=c.every(x=>sel.has(x));c.forEach(x=>tout?sel.delete(x):sel.add(x));maj()}});
['a1','a2','mj'].forEach(i=>document.getElementById(i).oninput=maj);maj();
</script>"""
open(os.path.join(ROOT, 'carte.html'), 'w', encoding='utf-8').write(doc('Événements mémorables : recherche sur carte', carte))
print('TERMINÉ', len(index), 'pages,', len(maj), 'majeurs')
