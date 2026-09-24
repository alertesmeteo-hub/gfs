"""Résumé factuel d'une fiche pluiesextremes : contexte, cumuls maximaux, rafales."""
import re
VAL = re.compile(r"(\d{2,4}(?:[,.]\d)?)\s*mm\s+(?:à|a|au|aux|sur|en)\s+([A-ZÉÈÂÎ][^;:,()\n]{1,40}?)\s*(?:\((\d{2}|2A|2B)\))?(?=\s*(?:[;:,.()\n]|dont|en\s|entre|le\s|du\s))")
RAF = re.compile(VAL.pattern.replace(r"(\d{2,4}(?:[,.]\d)?)\s*mm", r"(\d{2,3})\s*km/h"))
def phrases(t):
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-ZÉÈÀ])", t) if len(p.strip()) > 40]
def resumer(texte):
    corps = texte.split('\n', 3)[-1] if texte.startswith(('1','2','3','4','5','6','7','8','9','J','F','M','A','S','O','N','D')) else texte
    lignes = [l for l in corps.split('\n') if l.strip() and not re.match(r"\s*(Source|Accueil|Cumul des|Rafales maximales)", l)]
    prose = ' '.join(l.strip() for l in lignes if len(l) > 80 and 'mm à' not in l[:25])
    vals, vus = [], set()
    for v, lieu, dep in VAL.findall(corps):
        x = float(v.replace(',', '.')); lieu = lieu.strip()
        if lieu in vus or x < 20: continue
        vus.add(lieu); vals.append((x, lieu, dep))
    vals.sort(reverse=True)
    raf = sorted(((int(v), l.strip(), d) for v, l, d in RAF.findall(corps)), reverse=True)[:1]
    return {'contexte': ' '.join(phrases(prose)[:2])[:600], 'cumuls': vals[:3], 'rafale': raf[0] if raf else None}
