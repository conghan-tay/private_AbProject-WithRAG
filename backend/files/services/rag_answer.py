from django.conf import settings
from langchain_openai import ChatOpenAI

from files import rag_protocol as protocol


SYSTEM_PROMPT = """You are a retrieval-grounded assistant for a secure forensic file vault.
Answer using ONLY the provided context excerpts.

Rules:
- If the context does not contain enough information to answer, say exactly that.
- Do not use outside knowledge to fill gaps.
- Do not speculate or infer beyond what the excerpts state.
- If excerpts conflict, surface the conflict rather than resolving it silently.
- Be concise and precise."""


def build_answer_messages(question, retrieved_documents):
    excerpts = []
    for document in retrieved_documents:
        file_id = document.metadata.get(protocol.FIELD_FILE_ID, "")
        excerpts.append(
            "---\n"
            f"{document.page_content}\n"
            f"(source: {file_id})\n"
            "---"
        )

    context = "\n".join(excerpts)
    user_prompt = f"Context excerpts:\n{context}\n\nQuestion: {question}"
    return [
        ("system", SYSTEM_PROMPT),
        ("human", user_prompt),
    ]


class RagAnswerService:
    """Build grounded prompts and stream answer tokens from the configured LLM."""

    def stream_answer_tokens(self, question, retrieved_documents):
        llm = ChatOpenAI(
            model=settings.RAG_LLM_MODEL,
            temperature=0,
            streaming=True,
        )
        for chunk in llm.stream(build_answer_messages(question, retrieved_documents)):
            text = chunk_content_to_text(chunk)
            if text:
                yield text


def chunk_content_to_text(chunk):
    content = getattr(chunk, "content", chunk)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    return ""
