import logging

import pytest
from django.conf import settings
from django.test import override_settings

from files.services import rag_answer
from files.services.rag_answer import (
    RagAnswerService,
    build_answer_messages,
    chunk_content_to_text,
)
from files.tests._rag_test_helpers import FILE_A, FILE_B, doc


class FakeChunk:
    def __init__(self, content):
        self.content = content


class FakeChatOpenAI:
    instances = []
    stream_calls = []
    chunks = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def stream(self, messages):
        self.__class__.stream_calls.append(messages)
        return iter(self.__class__.chunks)


def reset_fake_chat():
    FakeChatOpenAI.instances = []
    FakeChatOpenAI.stream_calls = []
    FakeChatOpenAI.chunks = []


def test_rag_llm_model_default_is_configured():
    assert settings.RAG_LLM_MODEL == "gpt-4.1-mini"


def test_build_answer_messages_includes_grounding_rules_context_sources_and_question():
    messages = build_answer_messages(
        "What indicators were found?",
        [
            doc("alpha indicator 1.2.3.4", FILE_A),
            doc("beta indicator malware.exe", FILE_B),
        ],
    )

    assert messages[0][0] == "system"
    assert "Answer using ONLY the provided context excerpts." in messages[0][1]
    assert "Do not use outside knowledge to fill gaps." in messages[0][1]

    assert messages[1][0] == "human"
    user_prompt = messages[1][1]
    assert "Context excerpts:" in user_prompt
    assert "alpha indicator 1.2.3.4" in user_prompt
    assert f"(source: {FILE_A})" in user_prompt
    assert "beta indicator malware.exe" in user_prompt
    assert f"(source: {FILE_B})" in user_prompt
    assert "Question: What indicators were found?" in user_prompt


@override_settings(RAG_LLM_MODEL="test-llm")
def test_stream_answer_tokens_uses_chat_openai_with_configured_model_and_streaming(
    monkeypatch,
):
    reset_fake_chat()
    FakeChatOpenAI.chunks = [FakeChunk("The "), FakeChunk("answer")]
    monkeypatch.setattr(rag_answer, "ChatOpenAI", FakeChatOpenAI)

    tokens = list(
        RagAnswerService().stream_answer_tokens(
            "Question?",
            [doc("alpha", FILE_A)],
        )
    )

    assert tokens == ["The ", "answer"]
    assert len(FakeChatOpenAI.instances) == 1
    assert FakeChatOpenAI.instances[0].kwargs == {
        "model": "test-llm",
        "temperature": 0,
        "streaming": True,
    }
    assert len(FakeChatOpenAI.stream_calls) == 1
    assert FakeChatOpenAI.stream_calls[0] == build_answer_messages(
        "Question?",
        [doc("alpha", FILE_A)],
    )


def test_stream_answer_tokens_yields_only_non_empty_content_chunks(monkeypatch):
    reset_fake_chat()
    FakeChatOpenAI.chunks = [
        FakeChunk(""),
        FakeChunk("alpha"),
        FakeChunk(None),
        FakeChunk(
            [
                {"type": "text", "text": ""},
                {"type": "text", "text": " beta"},
            ]
        ),
        FakeChunk({"ignored": "shape"}),
    ]
    monkeypatch.setattr(rag_answer, "ChatOpenAI", FakeChatOpenAI)

    tokens = list(
        RagAnswerService().stream_answer_tokens(
            "Question?",
            [doc("alpha", FILE_A)],
        )
    )

    assert tokens == ["alpha", " beta"]


def test_chunk_content_to_text_ignores_non_text_blocks():
    assert (
        chunk_content_to_text(
            FakeChunk(
                [
                    {"type": "reasoning", "text": "hidden reasoning"},
                    {"type": "tool_use_input", "text": "hidden tool input"},
                    {"text": "legacy shape without type"},
                    {"type": "text", "text": "visible"},
                    " literal",
                ]
            )
        )
        == "visible literal"
    )


def test_chunk_content_to_text_rejects_unexpected_chunk_shape():
    with pytest.raises(TypeError, match="Unexpected stream chunk type: str"):
        chunk_content_to_text("bare chunk")


@override_settings(DEBUG=False, OPENAI_API_KEY="")
def test_missing_openai_key_logs_runtime_error_once_when_debug_false(caplog):
    rag_answer._missing_openai_key_logged = False
    caplog.set_level(logging.ERROR, logger="files.services.rag_answer")

    rag_answer.log_missing_openai_key_for_production()
    rag_answer.log_missing_openai_key_for_production()

    records = [
        record
        for record in caplog.records
        if "without OPENAI_API_KEY" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is RuntimeError

    rag_answer._missing_openai_key_logged = False
