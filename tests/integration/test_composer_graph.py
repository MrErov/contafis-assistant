"""El Answer Composer se conecta al grafo y queda protegido por grounding."""

from __future__ import annotations

from contafis_assistant.domain.contracts import Source
from contafis_assistant.domain.enums import Intent, Status
from contafis_assistant.graph.build import GraphDependencies, run_turn
from contafis_assistant.graph.classify import ClassificationDraft, ClassificationResult
from contafis_assistant.graph.executor import ToolOutcome
from contafis_assistant.graph.response import to_chat_response
from contafis_assistant.graph.state import Slots, ToolArgs, ToolName

SOURCE_WITH_TEXT = Source(
    document_id="doc-2",
    document="Gaceta Oficial",
    page=9,
    section="Artículo 5",
    locator="p.9",
    chunk_id="c2",
    score=1 / 61,
    collection="contafis_normativa_v1",
    text="El contribuyente especial debe declarar por el portal del Seniat.",
)

# Chunk cuyo número de artículo vive SOLO en el texto (metadatos sin section),
# que es el caso real de los chunks ingeridos desde Markdown.
SOURCE_ARTICLE_IN_TEXT = Source(
    document_id="doc-3",
    document="Gaceta Oficial 6.978 Extraordinaria - Ley de Hidrocarburos",
    page=None,
    section=None,
    locator=None,
    chunk_id="c3",
    score=1 / 61,
    collection="contafis_normativa_v1",
    text=(
        "**Artículo 56.** La alícuota del impuesto integrado de hidrocarburos "
        "será de hasta quince por ciento (15%) sobre la base imponible."
    ),
)


class _Classifier:
    def __call__(self, _message: str) -> ClassificationResult:
        return ClassificationResult(
            classification=ClassificationDraft(
                intent=Intent.normativa,
                slots=Slots(query="declarar"),
            )
        )


class _NormativeTool:
    def __call__(self, _args: ToolArgs) -> ToolOutcome:
        return ToolOutcome(status=Status.ok, sources=[SOURCE_WITH_TEXT])


def _deps(composer) -> GraphDependencies:
    return GraphDependencies(
        classifier=_Classifier(),
        tools={ToolName.search_normativa: _NormativeTool()},
        answer_composer=composer,
    )


def test_grounded_composer_answer_reaches_the_response():
    composer = lambda _context: "Debe declarar por el portal del Seniat."

    state = run_turn("¿quién debe declarar?", deps=_deps(composer), thread_id="rag-ok")
    response = to_chat_response(state)

    assert state.status is Status.ok
    assert state.answer == "Debe declarar por el portal del Seniat."
    assert response.answer == "Debe declarar por el portal del Seniat."
    # Los chunks recuperados siguen disponibles para citar al usuario.
    assert len(response.sources) == 1
    assert response.sources[0].chunk_id == "c2"
    assert response.sources[0].text == SOURCE_WITH_TEXT.text


def test_ungrounded_composer_answer_falls_back_to_deterministic():
    composer = lambda _context: "La compañía Inventada declara 999."

    state = run_turn("¿quién debe declarar?", deps=_deps(composer), thread_id="rag-fallo")

    # La salida inventada se descarta y cae al render determinista.
    assert "Inventada" not in state.answer
    assert "999" not in state.answer
    assert "Referencias normativas" in state.answer
    assert "Gaceta Oficial" in state.answer
    # Las fuentes se conservan igualmente (el usuario ve las referencias).
    assert len(state.sources) == 1


class _ArticleInTextTool:
    """Tool normativa cuyo chunk trae el artículo sólo en el texto."""

    def __call__(self, _args: ToolArgs) -> ToolOutcome:
        return ToolOutcome(status=Status.ok, sources=[SOURCE_ARTICLE_IN_TEXT])


def test_composer_answer_citing_article_from_chunk_text_is_kept():
    """Regresión: citar un artículo que vive en el texto del chunk debe pasar.

    Antes, ``_valid_citations`` sólo miraba metadatos y caía al determinista.
    """
    candidate = "La alícuota del impuesto integrado será de hasta quince por ciento."
    deps = GraphDependencies(
        classifier=_Classifier(),
        tools={ToolName.search_normativa: _ArticleInTextTool()},
        answer_composer=lambda _context: candidate,
    )

    state = run_turn(
        "¿cuál es la alícuota del impuesto integrado?",
        deps=deps,
        thread_id="rag-articulo-en-texto",
    )
    response = to_chat_response(state)

    assert state.status is Status.ok
    assert state.answer == candidate
    assert response.answer == candidate
    assert "Referencias normativas" not in response.answer
    assert len(response.sources) == 1
    assert response.sources[0].chunk_id == "c3"
