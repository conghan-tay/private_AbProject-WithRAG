from django.http import JsonResponse


class UserIdMiddleware:
    """Require a non-empty UserId header for API requests."""

    API_PREFIX = "/api/"
    HEADER_META_KEY = "HTTP_USERID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith(self.API_PREFIX):
            return self.get_response(request)

        user_id = request.META.get(self.HEADER_META_KEY)
        if user_id is None:
            return JsonResponse({"detail": "UserId header required"}, status=401)

        user_id = user_id.strip()
        if not user_id:
            return JsonResponse({"detail": "UserId must not be empty"}, status=400)

        request.user_id = user_id
        return self.get_response(request)
