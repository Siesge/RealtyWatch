"""Нормализация текста и правила отсева (обременение, доля, аукцион, ремонт)."""
import re

from . import geo

# Авито подмешивает латиницу, похожую на кириллицу: «Квapтиpа»
_HOMO = str.maketrans("aeopcxyKMHTBAEOPCX", "аеорсхуКМНТВАЕОРСХ")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")


def fix_homoglyphs(text):
    if not text:
        return text
    return _WORD.sub(lambda m: m.group(0).translate(_HOMO) if re.search("[А-Яа-яЁё]", m.group(0)) else m.group(0), text)


def norm(text):
    return fix_homoglyphs(text or "").lower().replace("ё", "е")


_NEG_BEFORE = re.compile(r"\b(без|нет|не|отсутств\w*|снят\w*|никаких)[\s,:\-]*(\w+[\s,]+)?$")
_NEG_AFTER = re.compile(r"^\w*[\s,:\-]*(нет|отсутств|не\s+имеет|сняты?)")


def _hits(text, pattern):
    """Совпадения без отрицания рядом («без обременений», «обременений нет»)."""
    out = []
    for m in re.finditer(pattern, text):
        before = text[max(0, m.start() - 25):m.start()]
        after = text[m.end():m.end() + 25]
        if _NEG_BEFORE.search(before) or _NEG_AFTER.search(after):
            continue
        out.append(text[max(0, m.start() - 30):m.end() + 30].strip())
    return out


ENCUMBRANCE = r"обремен\w*|под\s+арест\w*|арестован\w*|в\s+залоге|залог\s+у\s+банка|под\s+залог\w*"
SHARE = r"\bдол(я|ю|и)\b|\d+\s*/\s*\d+\s+дол|долев\w+\s+собственност"
AUCTION = r"аукцион\w*|\bторг(и|ах|ов)\b|банкрот\w*|конкурсн\w+\s+управляющ"

RENOVATION_LABELS = {
    "design": "дизайнерский",
    "euro": "евроремонт",
    "cosmetic": "косметический",
    "old": "старый («бабушкин»)",
    "finish": "чистовая отделка",
    "none": "без ремонта / требует",
}

_RENO_TEXT = [
    ("none", r"требует\s+(капитального\s+)?ремонт|без\s+ремонта|без\s+отделки|под\s+ремонт|черновая|предчистовая|white\s*box|вайт\s*бокс"),
    ("design", r"дизайнерск\w+\s+ремонт"),
    ("euro", r"евроремонт|евро\s+ремонт"),
    ("finish", r"чистов\w+\s+отделк|под\s+ключ"),
    ("cosmetic", r"косметическ\w+\s+ремонт|свежий\s+ремонт|с\s+ремонтом|хороший\s+ремонт|современный\s+ремонт"),
]


def renovation_from_text(text):
    t = norm(text)
    for code, pat in _RENO_TEXT:
        if re.search(pat, t):
            return code
    return None


def evaluate(item, f):
    """Возвращает список причин отсева (пустой — объявление проходит)."""
    reasons = []
    if item.get("rooms") is not None and f.get("rooms") and item["rooms"] not in f["rooms"]:
        reasons.append(f"комнат: {item['rooms']}")
    if item.get("area") is not None and f.get("area_min") and item["area"] < f["area_min"]:
        reasons.append(f"площадь {item['area']} м²")
    p = item.get("price")
    if p is not None and (p < f.get("price_min", 0) or p > f.get("price_max", 10**12)):
        reasons.append(f"цена {p:,}".replace(",", " "))
    if geo.outside_district(item):
        reasons.append("вне Ялтинского округа")
    if item.get("is_share"):
        reasons.append("доля")
    if item.get("is_apartments_studio"):
        reasons.append("студия/апартаменты-студия")

    text = norm(" ".join(filter(None, [item.get("title"), item.get("description")])))
    flags = item.setdefault("flags", {})
    enc, share, auc = _hits(text, ENCUMBRANCE), _hits(text, SHARE), _hits(text, AUCTION)
    if enc:
        flags["encumbrance"] = enc[:2]
    if share:
        flags["share"] = share[:2]
    if auc:
        flags["auction"] = auc[:2]
    if f.get("exclude_encumbrance") and enc:
        reasons.append("обременение")
    if f.get("exclude_share") and share and "доля" not in reasons:
        reasons.append("доля")
    if f.get("exclude_auction") and auc:
        reasons.append("аукцион/торги")

    mode = f.get("renovation", "exclude_none")
    reno = item.get("renovation")
    if mode != "off":
        if reno == "none":
            reasons.append("без ремонта")
        elif mode == "strict" and not reno:
            reasons.append("ремонт не указан")
    return reasons
