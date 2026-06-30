import hashlib
import re


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class DeterministicE2EEmbeddings:
    """Local deterministic embeddings for Docker E2E smoke tests only."""

    def __init__(self, dimensions):
        self.dimensions = dimensions

    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)

    def _embed(self, text):
        vector = [0.0] * self.dimensions
        for token in TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        return vector


def stream_fake_answer_tokens(question, retrieved_documents):
    source_count = len(
        {
            document.metadata.get("file_id")
            for document in retrieved_documents
            if document.metadata.get("file_id")
        }
    )
    preview = " ".join(
        " ".join(document.page_content.strip().replace("\n", " ").split())
        for document in retrieved_documents[:1]
    )
    if len(preview) > 120:
        preview = preview[:117].rstrip() + "..."

    text = (
        f"Fake RAG answer for: {question}. "
        f"Grounded in {source_count} source file(s). "
        f"Context preview: {preview}"
    )
    for token in text.split(" "):
        yield f"{token} "
