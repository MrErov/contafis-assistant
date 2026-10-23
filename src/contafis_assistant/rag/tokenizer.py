from __future__ import annotations

import re
import unicodedata

FISCAL_ALIASES: dict[str, tuple[str, ...]] = {
    "ISLR": ("islr", "impuesto sobre la renta"),
    "IVA": ("iva", "impuesto al valor agregado"),
    "IGTF": ("igtf", "impuesto a las grandes transacciones financieras"),
    "RIF": ("rif", "registro de informacion fiscal"),
}

_ACRONYM_BY_WORD = {alias: acronym for acronym, aliases in FISCAL_ALIASES.items() for alias in aliases}
_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.lower().split())


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    tokens: list[str] = []
    for match in _WORD_RE.finditer(normalized):
        word = match.group(0)
        acronym = _ACRONYM_BY_WORD.get(word)
        tokens.append(acronym if acronym else word)

    for acronym, aliases in FISCAL_ALIASES.items():
        for alias in aliases[1:]:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized):
                tokens.append(acronym)
    return tokens
