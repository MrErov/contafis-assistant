"""Answer Composer por LLM: guardas de alcance y redacción desde chunks."""

from __future__ import annotations

from contafis_assistant.config import Settings
from contafis_assistant.domain.answer import AnswerContext
from contafis_assistant.domain.contracts import ObligationsResult, Source
from contafis_assistant.domain.enums import Intent, Status
from contafis_assistant.main import _LazyOpenAIAnswerComposer
from contafis_assistant.observability.redaction import DATA_CLOSE

SOURCE = Source(
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

SOURCE_METADATA_ONLY = SOURCE.model_copy(update={"text": ""})


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.messages: list[tuple[str, str]] | None = None

    def invoke(self, messages: list[tuple[str, str]]) -> _FakeResponse:
        self.messages = messages
        return _FakeResponse(self._content)


def test_composer_redacts_only_from_fragments(settings_env):
    composer = _LazyOpenAIAnswerComposer(Settings(_env_file=None))
    fake = _FakeLLM("Debe declarar por el portal del Seniat.")
    composer._model = fake
    context = AnswerContext(
        status=Status.ok,
        intent=Intent.normativa,
        sources=[SOURCE],
        message="¿quién debe declarar?",
    )

    answer = composer(context)

    assert answer == "Debe declarar por el portal del Seniat."
    assert fake.messages is not None
    human = fake.messages[1][1]
    assert "declarar por el portal del Seniat." in human
    assert DATA_CLOSE in human


def test_composer_returns_empty_without_fragments_and_never_builds_llm(settings_env):
    composer = _LazyOpenAIAnswerComposer(Settings(_env_file=None))
    context = AnswerContext(status=Status.ok, intent=Intent.normativa, sources=[])

    assert composer(context) == ""
    assert composer._model is None


def test_composer_ignores_sources_without_text(settings_env):
    composer = _LazyOpenAIAnswerComposer(Settings(_env_file=None))
    context = AnswerContext(
        status=Status.ok,
        intent=Intent.normativa,
        sources=[SOURCE_METADATA_ONLY],
    )

    assert composer(context) == ""
    assert composer._model is None


def test_composer_defers_to_deterministic_when_erp_data_present(settings_env):
    composer = _LazyOpenAIAnswerComposer(Settings(_env_file=None))
    context = AnswerContext(
        status=Status.ok,
        intent=Intent.mixta,
        sources=[SOURCE],
        data=[ObligationsResult(total=0, returned=0, truncated=False)],
    )

    assert composer(context) == ""
    assert composer._model is None
