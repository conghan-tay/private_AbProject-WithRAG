import os
import sys
from pathlib import Path

import django

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from files.services.content_search import ContentSearchService


class TestContentSearchService:
    def setup_method(self):
        ContentSearchService.clear()

    def teardown_method(self):
        ContentSearchService.clear()

    def test_search_returns_file_ids_for_case_insensitive_exact_word_match(self):
        ContentSearchService.index_text(
            user_id='search-user',
            file_id='file-1',
            text='This document outlines the API Contract for the project.',
        )

        assert ContentSearchService.search('search-user', 'Contract') == ['file-1']
        assert ContentSearchService.search('search-user', 'contract') == ['file-1']
        assert ContentSearchService.search('search-user', 'cOnTrAcT') == ['file-1']

    def test_search_rejects_short_words_numbers_punctuation_and_multi_word_queries(self):
        ContentSearchService.index_text(
            user_id='search-user',
            file_id='file-1',
            text='API Contract 429 report.',
        )

        assert ContentSearchService.search('search-user', 'API') == ['file-1']
        assert ContentSearchService.search('search-user', 'to') == []
        assert ContentSearchService.search('search-user', '429') == []
        assert ContentSearchService.search('search-user', '.') == []
        assert ContentSearchService.search('search-user', 'Contract!') == []
        assert ContentSearchService.search('search-user', 'API Contract') == []

    def test_search_is_scoped_by_user_id(self):
        ContentSearchService.index_text(
            user_id='search-user',
            file_id='file-1',
            text='API Contract',
        )
        ContentSearchService.index_text(
            user_id='other-user',
            file_id='file-2',
            text='API Contract',
        )

        assert ContentSearchService.search('search-user', 'Contract') == ['file-1']
        assert ContentSearchService.search('other-user', 'Contract') == ['file-2']
        assert ContentSearchService.search('missing-user', 'Contract') == []
