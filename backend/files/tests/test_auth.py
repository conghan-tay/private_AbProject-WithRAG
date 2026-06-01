from types import SimpleNamespace

from django.test import RequestFactory
from rest_framework.response import Response
from rest_framework.test import APIClient

from files.models import File
from files.middleware import UserIdMiddleware
from files.views import FileViewSet


def test_missing_user_id_returns_401():
    response = APIClient().get('/api/files/')

    assert response.status_code == 401
    assert response.json() == {'detail': 'UserId header required'}


def test_empty_user_id_returns_400():
    response = APIClient().get('/api/files/', HTTP_USERID='')

    assert response.status_code == 400
    assert response.json() == {'detail': 'UserId must not be empty'}


def test_whitespace_user_id_returns_400():
    response = APIClient().get('/api/files/', HTTP_USERID='   ')

    assert response.status_code == 400
    assert response.json() == {'detail': 'UserId must not be empty'}


def test_valid_user_id_reaches_view_with_stripped_request_user_id(monkeypatch):
    def list_stub(self, request, *args, **kwargs):
        return Response({'ok': True, 'user_id': request.user_id})

    monkeypatch.setattr(FileViewSet, 'list', list_stub)

    response = APIClient().get('/api/files/', HTTP_USERID='  user-123  ')

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'user_id': 'user-123'}


def test_middleware_attaches_user_id_to_request():
    captured = {}

    def get_response(request):
        captured['user_id'] = request.user_id
        return Response({'ok': True})

    request = RequestFactory().get('/api/files/', HTTP_USERID=' user-456 ')
    response = UserIdMiddleware(get_response)(request)

    assert response.status_code == 200
    assert captured == {'user_id': 'user-456'}


def test_file_viewset_queryset_scopes_to_request_user_id():
    view = FileViewSet()
    view.request = SimpleNamespace(user_id='user-789')

    queryset = view.get_queryset()
    user_filter = queryset.query.where.children[0]

    assert queryset.model is File
    assert user_filter.lhs.target.name == 'user_id'
    assert user_filter.rhs == 'user-789'


def test_non_api_paths_bypass_user_id_requirement():
    def get_response(request):
        return Response({'ok': True})

    request = RequestFactory().get('/admin/')
    response = UserIdMiddleware(get_response)(request)

    assert response.status_code == 200
