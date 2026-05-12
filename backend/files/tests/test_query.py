import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import django
from django.test import TestCase
from django.test.utils import setup_databases, teardown_databases
from django.urls import reverse
from rest_framework.test import APIClient

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from files.models import File


TEST_DATABASE_CONFIG = None


def setup_module():
    global TEST_DATABASE_CONFIG
    TEST_DATABASE_CONFIG = setup_databases(verbosity=0, interactive=False)


def teardown_module():
    if TEST_DATABASE_CONFIG is not None:
        teardown_databases(TEST_DATABASE_CONFIG, verbosity=0)


class FileQueryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('file-list')

    def create_file(
        self,
        user_id='query-user',
        filename='document.txt',
        file_type='text/plain',
        size=100,
        uploaded_at=None,
    ):
        hash_source = f'{user_id}:{filename}:{file_type}:{size}'.encode('utf-8')
        record = File.objects.create(
            user_id=user_id,
            original_filename=filename,
            file_type=file_type,
            size=size,
            file_hash=hashlib.sha256(hash_source).hexdigest(),
            is_reference=False,
            reference_count=1,
        )
        if uploaded_at is not None:
            File.objects.filter(pk=record.pk).update(uploaded_at=uploaded_at)
            record.refresh_from_db(fields=['uploaded_at'])
        return record

    def list_files(self, params=None, user_id='query-user'):
        return self.client.get(self.url, params or {}, HTTP_USERID=user_id)

    def result_names(self, response):
        return [item['original_filename'] for item in response.json()['results']]

    def test_list_returns_paginated_envelope(self):
        uploaded = self.create_file(filename='document.txt')

        response = self.list_files()

        assert response.status_code == 200
        payload = response.json()
        assert set(payload.keys()) == {'count', 'next', 'previous', 'results'}
        assert payload['count'] == 1
        assert payload['next'] is None
        assert payload['previous'] is None
        assert payload['results'][0]['id'] == str(uploaded.id)

    def test_search_filters_filename_case_insensitively(self):
        self.create_file(filename='Doc_Report.pdf', file_type='application/pdf')
        self.create_file(filename='notes.txt')

        response = self.list_files({'search': 'doc'})

        assert response.status_code == 200
        assert self.result_names(response) == ['Doc_Report.pdf']

    def test_file_type_filters_by_exact_mime_type(self):
        self.create_file(filename='photo.jpg', file_type='image/jpeg')
        self.create_file(filename='photo.png', file_type='image/png')

        response = self.list_files({'file_type': 'image/jpeg'})

        assert response.status_code == 200
        assert self.result_names(response) == ['photo.jpg']

    def test_size_range_filters_between_min_and_max_size(self):
        self.create_file(filename='small.txt', size=999)
        self.create_file(filename='middle.txt', size=2500)
        self.create_file(filename='large.txt', size=5001)

        response = self.list_files({'min_size': '1000', 'max_size': '5000'})

        assert response.status_code == 200
        assert self.result_names(response) == ['middle.txt']

    def test_date_range_filters_by_uploaded_at(self):
        base_time = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
        self.create_file(filename='old.txt', uploaded_at=base_time - timedelta(days=2))
        self.create_file(filename='inside.txt', uploaded_at=base_time)
        self.create_file(filename='new.txt', uploaded_at=base_time + timedelta(days=2))

        response = self.list_files(
            {
                'start_date': (base_time - timedelta(hours=1)).isoformat(),
                'end_date': (base_time + timedelta(hours=1)).isoformat(),
            }
        )

        assert response.status_code == 200
        assert self.result_names(response) == ['inside.txt']

    def test_multiple_filters_use_and_semantics(self):
        self.create_file(filename='doc-small.txt', file_type='text/plain', size=100)
        self.create_file(filename='doc-target.txt', file_type='text/plain', size=2500)
        self.create_file(filename='doc-image.jpg', file_type='image/jpeg', size=2500)
        self.create_file(filename='notes-target.txt', file_type='text/plain', size=2500)

        response = self.list_files(
            {
                'search': 'doc',
                'file_type': 'text/plain',
                'min_size': '1000',
                'max_size': '5000',
            }
        )

        assert response.status_code == 200
        assert self.result_names(response) == ['doc-target.txt']

    def test_no_matching_results_return_empty_page(self):
        self.create_file(filename='document.txt')

        response = self.list_files({'search': 'missing'})

        assert response.status_code == 200
        assert response.json()['count'] == 0
        assert response.json()['results'] == []

    def test_pagination_uses_page_and_page_size_query_params(self):
        base_time = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
        for index in range(6):
            self.create_file(
                filename=f'file-{index}.txt',
                uploaded_at=base_time + timedelta(minutes=index),
            )

        response = self.list_files({'page': '2', 'page_size': '5'})

        assert response.status_code == 200
        payload = response.json()
        assert payload['count'] == 6
        assert payload['next'] is None
        assert payload['previous'] is not None
        assert self.result_names(response) == ['file-0.txt']

    def test_filters_are_scoped_to_request_user(self):
        owned = self.create_file(user_id='query-user', filename='shared-doc.txt')
        self.create_file(user_id='other-user', filename='shared-doc.txt')

        response = self.list_files({'search': 'shared'}, user_id='query-user')

        assert response.status_code == 200
        payload = response.json()
        assert payload['count'] == 1
        assert payload['results'][0]['id'] == str(owned.id)
        assert payload['results'][0]['user_id'] == 'query-user'

    def test_invalid_numeric_filter_returns_400(self):
        response = self.list_files({'min_size': 'not-a-number'})

        assert response.status_code == 400
        assert 'min_size' in response.json()

    def test_invalid_date_filter_returns_400(self):
        response = self.list_files({'start_date': 'not-a-date'})

        assert response.status_code == 400
        assert 'start_date' in response.json()
