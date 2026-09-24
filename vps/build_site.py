#!/usr/bin/env python3
"""Archive pluiesextremes.meteo.fr (Wayback 09/11/2022) -> site statique evenements-majeurs.alertes-meteo.com"""
import os, re, html, json, time, subprocess, sys
TS = '20221109122403'
BASE = 'http://pluiesextremes.meteo.fr/france-metropole/'
WB = f'https://web.archive.org/web/{TS}id_/'
ROOT = sys.argv[1] if len(sys.argv) > 1 else '/var/www/evenements-majeurs'
CACHE = os.path.join(ROOT, '_cache'); EV = os.path.join(ROOT, 'evenements')
for d in (CACHE, EV): os.makedirs(d, exist_ok=True)

def get(url, dest, tries=5):
    if os.path.exists(dest) and os.path.getsize(dest) > 0: return True
    for k in range(tries):
        if subprocess.run(['curl', '-sSLf', '-m', '120', '-A', 'alertes-meteo archive', '-o', dest, url]).returncode == 0 and os.path.getsize(dest) > 0:
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

def liste(slug):
    c = contenu(page(slug)) or page(slug)
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

CSS = """:root{--bg:#f6f8fb;--fg:#15202b;--mut:#5b6b7b;--acc:#0b5cad;--card:#fff;--bd:#dde3ea}
@media(prefers-color-scheme:dark){:root{--bg:#0f1720;--fg:#e6edf3;--mut:#9aa8b6;--acc:#5aa9ff;--card:#17212c;--bd:#2a3744}}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header,main,footer{max-width:1100px;margin:auto;padding:16px}header h1{margin:.2em 0;color:var(--acc)}a{color:var(--acc)}
table{width:100%;border-collapse:collapse;background:var(--card)}th,td{padding:8px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top}
.maj{font-weight:700}.badge{background:#c62828;color:#fff;border-radius:4px;padding:1px 6px;font-size:.75em;margin-left:6px}
input{width:100%;padding:10px;margin:8px 0 16px;border:1px solid var(--bd);border-radius:6px;background:var(--card);color:var(--fg)}
.fiche{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:16px;overflow-x:auto}.fiche img{max-width:100%;height:auto}
footer{color:var(--mut);font-size:.85em}nav a{margin-right:14px}"""
PIED = f"""<footer>Source : Météo-France – pluiesextremes.meteo.fr, archive Internet Archive du 09/11/2022. Contenus reproduits à titre documentaire ; les données et cartes appartiennent à Météo-France. Mise en forme : <a href="https://www.alertes-meteo.com">Alertes Météo</a>.</footer>"""
def doc(titre, corps, pre=''):
    return f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(titre)}</title><link rel="stylesheet" href="{pre}style.css"></head><body><header><nav><a href="{pre}index.html">Événements majeurs</a><a href="{pre}tous.html">Tous les événements</a><a href="https://www.alertes-meteo.com">alertes-meteo.com</a></nav><h1>{html.escape(titre)}</h1></header><main>{corps}</main>{PIED}</body></html>'
open(os.path.join(ROOT, 'style.css'), 'w').write(CSS)

index = []
for i, e in enumerate(tous, 1):
    n = f'{i:03d}'; slug = e['slug'][:-5]; d = os.path.join(EV, f'{n}_{slug}'); os.makedirs(d + '/fichiers', exist_ok=True)
    c = contenu(page(e['slug']))
    if not c: print('ABSENT', n, flush=True); continue
    def loc(m):
        attr, u = m.group(1), html.unescape(m.group(2))
        if not re.match(r'(https?://pluiesextremes\.meteo\.fr/france-metropole/)?IMG/', u): return m.group(0)
        full = u if u.startswith('http') else BASE + u
        nom = os.path.basename(u.split('?')[0])
        ok = get(WB + full, os.path.join(d, 'fichiers', nom))
        return f'{attr}="fichiers/{nom}"' if ok else f'{attr}="https://web.archive.org/web/{TS}/{full}"'
    c = re.sub(r'(src|href)="([^"]+)"', loc, c)
    c = re.sub(r'href="([^"/:#]+\.html)"', lambda m: f'href="https://web.archive.org/web/{TS}/{BASE}{m.group(1)}"', c)
    c = re.sub(r'<script.*?</script>', '', c, flags=re.S)
    docs = sorted(os.listdir(d + '/fichiers'))
    lst = ''.join(f'<li><a href="fichiers/{f}">{f}</a></li>' for f in docs)
    corps = f'<p><b>{html.escape(e["date"])}</b>{" <span class=badge>Événement majeur</span>" if e["slug"] in majeurs else ""}</p><div class="fiche">{c}</div><h2>Fichiers ({len(docs)})</h2><ul>{lst}</ul><p><a href="https://web.archive.org/web/{TS}/{BASE}{e["slug"]}">Page originale archivée</a></p>'
    open(os.path.join(d, 'index.html'), 'w', encoding='utf-8').write(doc(f'{e["date"]} – {e["titre"]}', corps, '../../'))
    index.append({**e, 'n': n, 'url': f'evenements/{n}_{slug}/', 'majeur': e['slug'] in majeurs, 'fichiers': len(docs)})
    print('ok', n, len(docs), flush=True)

json.dump(index, open(os.path.join(ROOT, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
def table(rows):
    tr = ''.join(f'<tr><td>{r["n"]}</td><td>{html.escape(r["date"])}</td><td class="{"maj" if r["majeur"] else ""}"><a href="{r["url"]}">{html.escape(r["titre"])}</a>{" <span class=badge>majeur</span>" if r["majeur"] else ""}</td><td>{r["fichiers"]}</td></tr>' for r in rows)
    return ('<input id=q placeholder="Rechercher une date, une région, un département…" oninput="for(const t of document.querySelectorAll(\'tbody tr\'))t.hidden=!t.textContent.toLowerCase().includes(this.value.toLowerCase())">'
            f'<table><thead><tr><th>N°</th><th>Date</th><th>Événement</th><th>Fichiers</th></tr></thead><tbody>{tr}</tbody></table>')
maj = [r for r in index if r['majeur']]
open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8').write(doc('Pluies extrêmes en France : les événements majeurs', f'<p>{len(maj)} événements majeurs sélectionnés par Météo-France, sur {len(index)} épisodes de pluies extrêmes documentés en France métropolitaine.</p>' + table(maj)))
open(os.path.join(ROOT, 'tous.html'), 'w', encoding='utf-8').write(doc(f'Tous les événements ({len(index)})', table(index)))
print('TERMINÉ', len(index), 'pages,', len(maj), 'majeurs')
