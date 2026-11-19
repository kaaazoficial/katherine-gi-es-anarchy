"""Topic detection shared by NEXUS routing and the AI tool guard."""
from __future__ import annotations

import re
import unicodedata


GENSHIN_TOPIC_HINTS = frozenset({
    "genshin", "teyvat", "primogema", "primogemas", "primogem",
    "protogema", "protogemas", "fatui", "hilichurl", "hilichurls",
    "celestia", "khaenriah", "khaenri'ah", "gnosis",
    "resina original", "resina fragil", "resina debil", "reaccion elemental",
    "reacciones elementales", "abismo espiral", "orden del abismo",
    "siete naciones", "arconte", "arcontes", "vision elemental",
})

GENSHIN_REGION_HINTS = frozenset({
    "mondstadt", "liyue", "inazuma", "sumeru", "fontaine",
    "natlan", "snezhnaya", "nod-krai", "khaenriah",
    "dragonspine", "enkanomiya", "the chasm", "chasm",
})

GENSHIN_ELEMENT_HINTS = frozenset({
    "pyro", "hydro", "electro", "cryo", "dendro", "anemo", "geo",
})

GENSHIN_ENTITY_HINTS = frozenset({
    "albedo", "alhaitham", "amber", "arataki itto", "ayaka", "ayato",
    "baizhu", "barbara", "beidou", "bennett", "candace", "charlotte",
    "chasca", "chevreuse", "chiori", "chongyun", "clorinde", "collei",
    "cyno", "dainsleif", "dehya", "diluc", "diona", "dori", "eula",
    "faruzan", "fischl", "freminet", "furina", "ganyu", "gorou",
    "hu tao", "hutao", "jean", "kaeya", "kaveh", "kazuha", "keqing",
    "kirara", "klee", "kokomi", "layla", "lynette", "lyney", "mika",
    "mona", "mualani", "nahida", "navia", "neuvillette", "nilou",
    "ningguang", "noelle", "ororon", "qiqi", "raiden shogun", "razor",
    "rosaria", "sangonomiya kokomi", "sayu", "sethos", "shenhe",
    "shikanoin heizou", "sigewinne", "sucrose", "tartaglia", "thoma",
    "tighnari", "venti", "wanderer", "wriothesley", "xiangling",
    "xianyun", "xiao", "xingqiu", "xinyan", "yae miko", "yanfei",
    "yaoyao", "yelan", "yoimiya", "yun jin", "zhongli", "hermanubis",
})


def normalize_topic_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = normalize_topic_text(phrase)
    return bool(normalized_phrase) and f" {normalized_phrase} " in f" {text} "


def is_genshin_related_request(value: str) -> bool:
    """Only explicit Genshin context or a recognizable game entity counts."""
    text = normalize_topic_text(value)
    if not text:
        return False
    return any(
        _contains_phrase(text, hint)
        for hint in (
            *GENSHIN_TOPIC_HINTS, *GENSHIN_ENTITY_HINTS,
            *GENSHIN_REGION_HINTS, *GENSHIN_ELEMENT_HINTS,
        )
    )
