import logging

from django.conf import settings

from files import rag_protocol as protocol


logger = logging.getLogger(__name__)


class RagSessionIndex:
    """Session-scoped ephemeral Chroma index for Ask the Vault."""

    def __init__(self, session_id):
        import chromadb
        from langchain_chroma import Chroma

        self.session_id = session_id
        self.chroma_client = chromadb.EphemeralClient()
        self.embedding_function = self._embedding_function()
        self.vector_store = Chroma(
            client=self.chroma_client,
            collection_name=f"askvault-{session_id}",
            embedding_function=self.embedding_function,
            collection_configuration={"hnsw": {"space": "cosine"}},
        )

    def index_chunks(self, chunks):
        from langchain_core.documents import Document

        documents = [
            Document(
                page_content=chunk[protocol.FIELD_PAGE_CONTENT],
                metadata=chunk[protocol.FIELD_METADATA],
            )
            for chunk in chunks
        ]
        ids = [
            (
                f"{chunk[protocol.FIELD_METADATA][protocol.FIELD_FILE_ID]}:"
                f"{chunk[protocol.FIELD_METADATA][protocol.FIELD_CHUNK_INDEX]}"
            )
            for chunk in chunks
        ]

        if documents:
            self.vector_store.add_documents(documents, ids=ids)

    def retrieve(self, question):
        scored_results = self.vector_store.similarity_search_with_score(
            question,
            k=1,
        )
        if not scored_results:
            return self._retrieval_result(answerable=False, documents=[])

        _, top_distance = scored_results[0]
        if top_distance > settings.RAG_MAX_DISTANCE:
            return self._retrieval_result(answerable=False, documents=[])

        retriever = self.vector_store.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k": settings.RAG_RETRIEVAL_K,
                "fetch_k": settings.RAG_RETRIEVAL_FETCH_K,
            },
        )
        documents = retriever.invoke(question)[: settings.RAG_MAX_CONTEXT_CHUNKS]
        return self._retrieval_result(answerable=True, documents=documents)

    @staticmethod
    def _retrieval_result(answerable, documents):
        sources = sorted(
            {
                document.metadata[protocol.FIELD_FILE_ID]
                for document in documents
                if protocol.FIELD_FILE_ID in document.metadata
            }
        )
        return {
            "answerable": answerable,
            "documents": documents,
            protocol.FIELD_SOURCES: sources,
        }

    def cleanup(self):
        try:
            if self.vector_store is not None:
                self.vector_store.delete_collection()
        except Exception:
            logger.exception(
                "rag chroma collection cleanup failed for session %s",
                self.session_id,
            )
            pass
        finally:
            self.vector_store = None
            self.chroma_client = None
            self.embedding_function = None

    @staticmethod
    def _embedding_function():
        if settings.ASKVAULT_RAG_E2E_FAKE:
            from files.services.rag_fake import DeterministicE2EEmbeddings

            return DeterministicE2EEmbeddings(
                dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
            )

        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.RAG_EMBEDDING_MODEL,
            dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
        )
