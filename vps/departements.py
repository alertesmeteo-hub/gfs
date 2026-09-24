"""Départements, anciennes régions (découpage pluiesextremes) et détection dans un texte."""
import re, unicodedata
NOMS = {'01':'Ain','02':'Aisne','03':'Allier','04':'Alpes-de-Haute-Provence','05':'Hautes-Alpes','06':'Alpes-Maritimes','07':'Ardèche','08':'Ardennes','09':'Ariège','10':'Aube','11':'Aude','12':'Aveyron','13':'Bouches-du-Rhône','14':'Calvados','15':'Cantal','16':'Charente','17':'Charente-Maritime','18':'Cher','19':'Corrèze','2A':'Corse-du-Sud','2B':'Haute-Corse','21':"Côte-d'Or",'22':"Côtes-d'Armor",'23':'Creuse','24':'Dordogne','25':'Doubs','26':'Drôme','27':'Eure','28':'Eure-et-Loir','29':'Finistère','30':'Gard','31':'Haute-Garonne','32':'Gers','33':'Gironde','34':'Hérault','35':'Ille-et-Vilaine','36':'Indre','37':'Indre-et-Loire','38':'Isère','39':'Jura','40':'Landes','41':'Loir-et-Cher','42':'Loire','43':'Haute-Loire','44':'Loire-Atlantique','45':'Loiret','46':'Lot','47':'Lot-et-Garonne','48':'Lozère','49':'Maine-et-Loire','50':'Manche','51':'Marne','52':'Haute-Marne','53':'Mayenne','54':'Meurthe-et-Moselle','55':'Meuse','56':'Morbihan','57':'Moselle','58':'Nièvre','59':'Nord','60':'Oise','61':'Orne','62':'Pas-de-Calais','63':'Puy-de-Dôme','64':'Pyrénées-Atlantiques','65':'Hautes-Pyrénées','66':'Pyrénées-Orientales','67':'Bas-Rhin','68':'Haut-Rhin','69':'Rhône','70':'Haute-Saône','71':'Saône-et-Loire','72':'Sarthe','73':'Savoie','74':'Haute-Savoie','75':'Paris','76':'Seine-Maritime','77':'Seine-et-Marne','78':'Yvelines','79':'Deux-Sèvres','80':'Somme','81':'Tarn','82':'Tarn-et-Garonne','83':'Var','84':'Vaucluse','85':'Vendée','86':'Vienne','87':'Haute-Vienne','88':'Vosges','89':'Yonne','90':'Territoire de Belfort','91':'Essonne','92':'Hauts-de-Seine','93':'Seine-Saint-Denis','94':'Val-de-Marne','95':"Val-d'Oise",'AD':'Andorre'}
REGIONS = {
 'Alsace':'67 68','Aquitaine':'24 33 40 47 64','Auvergne':'03 15 43 63','Basse-Normandie':'14 50 61','Bourgogne':'21 58 71 89',
 'Bretagne':'22 29 35 56','Centre':'18 28 36 37 41 45','Champagne-Ardenne':'08 10 51 52','Corse':'2A 2B','Franche-Comté':'25 39 70 90',
 'Haute-Normandie':'27 76','Île-de-France':'75 77 78 91 92 93 94 95','Languedoc-Roussillon':'11 30 34 48 66','Limousin':'19 23 87',
 'Lorraine':'54 55 57 88','Midi-Pyrénées':'09 12 31 32 46 65 81 82','Nord-Pas-de-Calais':'59 62',"Provence-Alpes-Côte d'Azur":'04 05 06 13 83 84',
 'Pays de la Loire':'44 49 53 72 85','Picardie':'02 60 80','Poitou-Charentes':'16 17 79 86','Rhône-Alpes':'01 07 26 38 42 69 73 74','Andorre':'AD'}
REGIONS = {k: v.split() for k, v in REGIONS.items()}
# alias -> codes (adjectifs, villes, zones géographiques usuelles des fiches)
ALIAS = {
 'héraultais':'34','gardois':'30','audois':'11','ardéchois':'07','lozérien':'48','aveyronnais':'12','varois':'83','roussillon':'66','catalan':'66',
 'montpellier':'34','nîmes':'30','ales':'30','alès':'30','béziers':'34','narbonne':'11','carcassonne':'11','perpignan':'66','côte vermeille':'66',
 'marseille':'13','toulon':'83','nice':'06','cannes':'06','draguignan':'83','avignon':'84','vaison':'84','ajaccio':'2A','bastia':'2B','cap corse':'2B',
 'cévennes':'30 48','vivarais':'07','lyon':'69','lyonnais':'69','grenoble':'38','nancy':'54','metz':'57','strasbourg':'67','mulhouse':'68','dijon':'21',
 'besançon':'25','rennes':'35','brest':'29','quimper':'29','quimperlé':'29','morlaix':'29','redon':'35','nantes':'44','angers':'49','le mans':'72','tours':'37','orléans':'45',
 'bordeaux':'33','bordelais':'33','bayonne':'64','pau':'64','pays basque':'64','béarn':'64','toulouse':'31','montauban':'82','albi':'81','castres':'81','tarbes':'65','lourdes':'65',
 'agen':'47','périgueux':'24','cahors':'46','limoges':'87','clermont':'63','nevers':'58','auxerre':'89','troyes':'10','reims':'51','amiens':'80','lille':'59',
 'rouen':'76','caen':'14','cherbourg':'50','poitiers':'86','niort':'79','la rochelle':'17','angoulême':'16','paris':'75','versailles':'78',
 'vendéen':'85','breton':'22 29 35 56','normand':'14 27 50 61 76','alsacien':'67 68','corse':'2A 2B','savoyard':'73 74','vosgien':'88','dordogne':'24',
 'vaucluse':'84','camargue':'13 30','crau':'13','estérel':'83','maures':'83','grasse':'06','menton':'06','sète':'34','lodève':'34','ganges':'34',
 'nîmois':'30','uzès':'30','anduze':'30','vallée du rhône':'07 26 84','drôme':'26','privas':'07','aubenas':'07','mende':'48','millau':'12','rodez':'12',
}
def _n(s, low=True): return unicodedata.normalize('NFD', s.lower() if low else s).encode('ascii','ignore').decode().replace('’',"'")
_PATS = []
for c, nom in NOMS.items():
    _PATS.append((re.compile(r"(?<![\w-])" + re.escape(_n(nom, False)).replace(r"\-", r"[- ]") + r"(?![\w-])"), [c], False))
for r, cs in REGIONS.items():
    _PATS.append((re.compile(r"(?<![\w-])" + re.escape(_n(r, False)).replace(r"\-", r"[- ]") + r"(?![\w-])"), cs, False))
for a, cs in ALIAS.items():
    _PATS.append((re.compile(r"(?<![\w-])" + re.escape(_n(a)) + r"s?e?s?(?![\w-])"), cs.split(), True))
# "Loire" seule ne doit pas écraser Haute-Loire / Loire-Atlantique etc. : les noms composés sont testés d'abord et retirés
_PATS[:] = [p for p in _PATS if p[1] != ['59']] + [(re.compile(r"\b(?:le|du|dans le) Nord\b(?![- ](?:de|du|des|et|est|ouest|Est|Ouest))"), ['59'], False)]
_PATS.sort(key=lambda p: -len(p[0].pattern))
CODE = re.compile(r"\((0[1-9]|[1-8]\d|9[0-5]|2A|2B)\)|dept(\d{2}(?:-\d{2})*)")
def detecter(texte):
    lo, up = _n(texte), _n(texte, False); out = set()
    for p, cs, low in _PATS:
        t = lo if low else up
        if p.search(t):
            out.update(cs)
            if low: lo = p.sub(' ', lo)
            else: up = p.sub(' ', up)
    for a, b in CODE.findall(texte):
        if a: out.add(a)
        if b: out.update(b.split('-'))
    return sorted(out & set(NOMS))
