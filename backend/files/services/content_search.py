import re
from collections import defaultdict


MIN_WORD_LENGTH = 3
WORD_PATTERN = re.compile(r'[A-Za-z]+')


class ContentSearchService:
    """In-memory exact-word search over caller-supplied document text."""

    _index = defaultdict(lambda: defaultdict(set))

    @classmethod
    def clear(cls):
        cls._index.clear()

    @classmethod
    def index_text(cls, user_id, file_id, text):
        if not isinstance(text, str):
            return

        for word in cls._tokenize(text):
            cls._index[word][str(user_id)].add(str(file_id))

    @classmethod
    def search(cls, user_id, text):
        normalized = cls._normalize_search_term(text)
        if normalized is None:
            return []

        return sorted(cls._index.get(normalized, {}).get(str(user_id), set()))

    @classmethod
    def _tokenize(cls, text):
        for match in WORD_PATTERN.finditer(text):
            word = match.group(0).casefold()
            if len(word) >= MIN_WORD_LENGTH:
                yield word

    @staticmethod
    def _normalize_search_term(text):
        if not isinstance(text, str):
            return None

        word = text.casefold()
        if len(word) < MIN_WORD_LENGTH or not word.isalpha():
            return None

        return word
