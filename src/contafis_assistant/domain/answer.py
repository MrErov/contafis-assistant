"""Render determinista de respuestas y validación de grounding."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import (
    EntityRef,
    Source,
    ToolResult,
    Warning,
)
from contafis_assistant.domain.enums import Intent, Status
from contafis_assistant.domain.time import format_date

AnswerComposer = Callable[["AnswerContext"], str]

MAX_RENDERED_ITEMS = 5
MAX_RENDERED_SOURCES = 5
MAX_RENDERED_OPTIONS = 5

_MONETARY_AMOUNT = re.compile(
    r"(?i)(?:[$€£¥]\s*[\d.,]+|[\d.,]+\s*[$€£¥]"
    r"|\b(?:usd|ves|eur|bs\.?|bol[ií]vares?)\b\s*[\d.,]+"
    r"|[\d.,]+\s*\b(?:usd|ves|eur|bs\.?|bol[ií]vares?)\b)"
)
_MONETARY_TOKEN = re.compile(
    r"(?i)(?:[$€£¥]|\b(?:usd|ves|eur|bs\.?|bol[ií]vares?)\b)"
)
_DATE_DDMMYYYY = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_DATE_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TITLE_TOKEN = re.compile(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+")
_ACRONYM = re.compile(r"\b[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9]{1,}\b")
_SENTENCE_BREAK = re.compile(r"(?<=[.?!\n])\s+")

_TOTAL_CLAIM = re.compile(r"(?i)\btotals?\b[^\d]{0,20}(\d+)")
_ID_CLAIM = re.compile(
    r"(?i)\b(?:id|ids|identificador|c[oó]digo)\b[^\d]{0,10}(\d+)"
)
_PAGE_CLAIM = re.compile(r"(?i)\b(?:p[aá]gina|p[aá]g\.?|p\.)\s*(\d+)")
_CITATION_PATTERNS = (
    re.compile(r"(?i)\bart(?:[ií]culo|\.)\s*(\d+[a-z]?)"),
    re.compile(r"(?i)\bgaceta(?:\s+oficial)?(?:\s+n[.º°o]*)?\s*(\d+)"),
    re.compile(r"(?i)\bprovidencia(?:\s+n[.º°o]*)?\s*(\d+)"),
    re.compile(r"(?i)\bdecreto(?:\s+n[.º°o]*)?\s*(\d+)"),
    re.compile(r"(?i)\bresoluci[oó]n(?:\s+n[.º°o]*)?\s*(\d+)"),
)

_UI_WORDS = {
    "no",
    "enviá",
    "envia",
    "probá",
    "proba",
    "intentá",
    "intenta",
    "respondé",
    "responde",
    "te",
    "datos",
    "referencias",
    "normativas",
    "total",
    "mostrando",
    "consulté",
    "consulte",
    "encontré",
    "encontre",
    "la",
    "el",
    "los",
    "las",
    "una",
    "un",
    "esa",
    "este",
    "esta",
    "según",
    "segun",
    "fuente",
    "fuentes",
    "erp",
    "rag",
    "mvp",
    "id",
    "ids",
    "pdf",
    "api",
    "http",
}

_RESULT_LABELS = {
    "obligations": "obligaciones",
    "clients": "clientes",
    "documents": "documentos",
    "alerts": "alertas",
    "companies": "compañías",
}

_ITEM_NAMES = (
    "client_name",
    "name",
    "company_name",
    "document_type_name",
    "obligation_type_name",
    "alert_type",
)
_ITEM_DATES = ("due_date", "expiry_date", "date", "as_of")
_ITEM_STATES = ("effective_state", "state", "compliance_status")


class AnswerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    intent: Intent | None = None
    data: list[ToolResult] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    options: list[EntityRef] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    truncated: bool = False
    message: str = ""


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _clean_dynamic(value: Any) -> str:
    text = str(value)
    text = _MONETARY_AMOUNT.sub(" ", text)
    text = _MONETARY_TOKEN.sub(" ", text)
    return " ".join(text.split())


def _clean_answer(text: str) -> str:
    cleaned = _MONETARY_AMOUNT.sub(" ", text)
    cleaned = _MONETARY_TOKEN.sub(" ", cleaned)
    return "\n".join(line.rstrip() for line in cleaned.strip().splitlines())


def contains_monetary(text: str) -> bool:
    return bool(_MONETARY_AMOUNT.search(text) or _MONETARY_TOKEN.search(text))


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _item_name(item: Any) -> str | None:
    for attribute in _ITEM_NAMES:
        value = getattr(item, attribute, None)
        if value:
            return _clean_dynamic(value)
    return None


def _item_date(item: Any) -> date | None:
    for attribute in _ITEM_DATES:
        value = _as_date(getattr(item, attribute, None))
        if value is not None:
            return value
    return None


def _item_state(item: Any) -> str | None:
    for attribute in _ITEM_STATES:
        value = getattr(item, attribute, None)
        if value is not None:
            return _clean_dynamic(_value(value))
    return None


def _result_lines(result: ToolResult) -> list[str]:
    label = _RESULT_LABELS.get(result.kind, result.kind)
    if result.total is None:
        summary = f"{label}: total no disponible"
    else:
        summary = f"{label}: total {result.total}"
    summary += f", mostrados {result.returned}"
    if result.truncated:
        summary += ", detalle truncado"
    lines = [summary]

    for item in result.items[:MAX_RENDERED_ITEMS]:
        parts: list[str] = []
        name = _item_name(item)
        if name:
            parts.append(name)
        item_date = _item_date(item)
        if item_date is not None:
            parts.append(f"fecha {format_date(item_date)}")
        state = _item_state(item)
        if state:
            parts.append(f"estado {state}")
        if parts:
            lines.append("  - " + " | ".join(parts))

    return lines


def _source_lines(sources: Iterable[Source]) -> list[str]:
    lines = ["Referencias normativas:"]
    for source in list(sources)[:MAX_RENDERED_SOURCES]:
        document = _clean_dynamic(source.document)
        details: list[str] = []
        if source.page is not None:
            details.append(f"página {source.page}")
        if source.section:
            details.append(_clean_dynamic(source.section))
        if source.locator:
            details.append(_clean_dynamic(source.locator))
        line = f"  - {document}"
        if details:
            line += " (" + ", ".join(details) + ")"
        lines.append(line)
    return lines


def _data_and_sources_lines(context: AnswerContext) -> list[str]:
    lines: list[str] = []
    if context.data:
        lines.append("Datos del ERP:")
        for result in context.data:
            lines.extend(_result_lines(result))
    if context.sources:
        if lines:
            lines.append("")
        lines.extend(_source_lines(context.sources))
    return lines


def _options_text(options: Iterable[EntityRef]) -> str:
    names = [_clean_dynamic(option.name) for option in list(options)[:MAX_RENDERED_OPTIONS]]
    return ", ".join(name for name in names if name)


def _render_ok(context: AnswerContext) -> str:
    lines = _data_and_sources_lines(context)
    if not lines:
        lines = ["No obtuve registros para esa consulta."]
    return "\n".join(lines)


def _render_partial(context: AnswerContext) -> str:
    lines = _data_and_sources_lines(context)
    if not lines:
        return (
            "No pude completar toda la consulta con el alcance actual. "
            "Probá acotar la consulta o intentá de nuevo."
        )
    return "\n".join(["La consulta se respondió parcialmente.", *lines])


def _render_iteration_limit(context: AnswerContext) -> str:
    lines = _data_and_sources_lines(context)
    header = "La consulta alcanzó el límite de llamadas por turno."
    if not lines:
        return header + " No alcancé a obtener datos."
    return "\n".join([header + " Te muestro lo disponible.", *lines])


def _render_needs_clarification(context: AnswerContext) -> str:
    options = _options_text(context.options)
    if options:
        return (
            "¿A cuál de estas entidades te referís: "
            f"{options}? Respondé con el nombre exacto."
        )
    return (
        "¿Podés aclarar a qué compañía o cliente te referís? "
        "Indicá el nombre o el ID."
    )


def _render_not_found(context: AnswerContext) -> str:
    options = _options_text(context.options)
    if options:
        return (
            "No encontré una entidad con ese nombre. "
            f"Probá con: {options}."
        )
    return (
        "No encontré una entidad con ese nombre. "
        "Verificá el nombre o el ID e intentá de nuevo."
    )


def render_answer(context: AnswerContext) -> str:
    """Render determinista en es-VE para todos los estados."""
    status = context.status

    if status is Status.invalid_request:
        text = (
            "No pude procesar la solicitud. Enviá un mensaje de texto "
            "no vacío y volvé a intentarlo."
        )
    elif status is Status.out_of_scope:
        text = (
            "Esa consulta está fuera del alcance del MVP. Puedo ayudarte con "
            "consultas de solo lectura sobre compañías, clientes, obligaciones, "
            "documentos, alertas y normativa fiscal venezolana."
        )
    elif status is Status.needs_clarification:
        text = _render_needs_clarification(context)
    elif status is Status.not_found:
        text = _render_not_found(context)
    elif status is Status.insufficient_context:
        text = (
            "No tengo información sobre eso en el corpus normativo disponible. "
            "Probá reformular la pregunta o consultá por obligaciones, "
            "documentos o alertas."
        )
    elif status is Status.upstream_error:
        text = (
            "No pude completar la consulta porque una fuente no está disponible. "
            "Intentá de nuevo en unos minutos."
        )
    elif status is Status.iteration_limit:
        text = _render_iteration_limit(context)
    elif status is Status.partial:
        text = _render_partial(context)
    else:
        text = _render_ok(context)

    return _clean_answer(text)


def _dump_allowed(context: AnswerContext) -> str:
    parts = [context.message]
    for result in context.data:
        parts.append(result.model_dump_json())
    for source in context.sources:
        parts.append(source.model_dump_json())
    for entity in [*context.entities, *context.options]:
        parts.append(entity.model_dump_json())
    return "\n".join(parts)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())


def _allowed_dates(context: AnswerContext) -> set[str]:
    dates: set[str] = set()
    for result in context.data:
        for item in result.items:
            item_date = _item_date(item)
            if item_date is not None:
                dates.add(format_date(item_date))
    return {value for value in dates if value is not None}


def _collect_numbers(context: AnswerContext) -> tuple[set[str], set[str], set[str], set[str]]:
    totals: set[str] = set()
    identifiers: set[str] = set()
    pages: set[str] = set()
    broad: set[str] = set()

    for result in context.data:
        if result.total is not None:
            totals.add(str(result.total))
        pending_count = getattr(result, "pending_count", None)
        if pending_count is not None:
            totals.add(str(pending_count))
        identifiers.add(str(result.returned))
        for item in result.items:
            for attribute in (
                "id",
                "client_id",
                "company_id",
                "obligation_type_id",
                "document_type_id",
                "obligation_id",
                "document_id",
            ):
                value = getattr(item, attribute, None)
                if isinstance(value, int) and not isinstance(value, bool):
                    identifiers.add(str(value))

    for reference in [*context.entities, *context.options]:
        identifiers.add(str(reference.id))

    for source in context.sources:
        if source.page is not None:
            pages.add(str(source.page))

    broad.update(re.findall(r"\d+", _dump_allowed(context)))
    broad.update(totals)
    broad.update(identifiers)
    broad.update(pages)
    return totals, identifiers, pages, broad


def _citation_spans(candidate: str) -> list[re.Match[str]]:
    spans: list[re.Match[str]] = []
    for pattern in _CITATION_PATTERNS:
        spans.extend(pattern.finditer(candidate))
    return spans


def _citation_name(match: re.Match[str]) -> str:
    raw = match.group(0).casefold()
    if raw.startswith("art"):
        kind = "articulo"
    elif raw.startswith("gaceta"):
        kind = "gaceta"
    elif raw.startswith("providencia"):
        kind = "providencia"
    elif raw.startswith("decreto"):
        kind = "decreto"
    else:
        kind = "resolucion"
    return f"{kind} {match.group(1).casefold()}"


def _valid_citations(candidate: str, context: AnswerContext) -> bool:
    matches = _citation_spans(candidate)
    if not matches:
        return True
    if not context.sources:
        return False
    allowed = _normalize(
        "\n".join(
            " ".join(
                part
                for part in (
                    source.document,
                    source.section or "",
                    source.locator or "",
                )
                if part
            )
            for source in context.sources
        )
    )
    return all(_citation_name(match) in allowed for match in matches)


def _valid_dates(candidate: str, context: AnswerContext) -> bool:
    if _DATE_ISO.search(candidate):
        return False
    allowed = _allowed_dates(context)
    return all(match.group(0) in allowed for match in _DATE_DDMMYYYY.finditer(candidate))


def _valid_claimed_numbers(
    candidate: str,
    context: AnswerContext,
) -> bool:
    totals, identifiers, pages, broad = _collect_numbers(context)

    stripped = _DATE_DDMMYYYY.sub(" ", candidate)
    stripped = _DATE_ISO.sub(" ", stripped)
    for match in sorted(_citation_spans(candidate), key=lambda item: -item.start()):
        stripped = stripped[: match.start()] + " " + stripped[match.end() :]

    for value in _TOTAL_CLAIM.findall(stripped):
        if value not in totals:
            return False
    for value in _ID_CLAIM.findall(stripped):
        if value not in identifiers:
            return False
    for value in _PAGE_CLAIM.findall(stripped):
        if value not in pages:
            return False
    return all(value in broad for value in re.findall(r"\d+", stripped))


def _valid_names(candidate: str, context: AnswerContext) -> bool:
    allowed = _normalize(_dump_allowed(context))
    for segment in _SENTENCE_BREAK.split(candidate):
        for match in _TITLE_TOKEN.finditer(segment):
            prefix = segment[: match.start()].strip(" ¿¡\"'([-–—")
            if not prefix:
                continue
            token = _normalize(match.group(0))
            if token in _UI_WORDS:
                continue
            if token not in allowed:
                return False
        for match in _ACRONYM.finditer(segment):
            prefix = segment[: match.start()].strip(" ¿¡\"'([-–—")
            if not prefix:
                continue
            token = match.group(0).casefold()
            if token in _UI_WORDS:
                continue
            if token not in allowed and match.group(0) not in allowed:
                return False
    return True


def validate_grounding(candidate: str, context: AnswerContext) -> bool:
    """Verifica que el texto solo use hechos presentes en el contexto permitido."""
    if not candidate or not candidate.strip():
        return False
    if contains_monetary(candidate):
        return False
    try:
        return (
            _valid_citations(candidate, context)
            and _valid_dates(candidate, context)
            and _valid_claimed_numbers(candidate, context)
            and _valid_names(candidate, context)
        )
    except Exception:
        return False


def compose_answer(
    context: AnswerContext,
    *,
    composer: AnswerComposer | None = None,
) -> str:
    """Usa el compositor solo para `ok`/`partial` y cae al render determinista."""
    deterministic = render_answer(context)
    if composer is None or context.status not in {Status.ok, Status.partial}:
        return deterministic
    try:
        candidate = composer(context)
    except Exception:
        return deterministic
    if not isinstance(candidate, str):
        return deterministic
    if not validate_grounding(candidate, context):
        return deterministic
    return candidate.strip()


__all__ = [
    "AnswerComposer",
    "AnswerContext",
    "compose_answer",
    "contains_monetary",
    "render_answer",
    "validate_grounding",
]
