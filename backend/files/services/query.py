from rest_framework.exceptions import ValidationError

from files.filters import FileFilter
from files.models import File


class FileQueryService:
    """Read-side file queries and filtering."""

    @staticmethod
    def build_queryset(user_id, filters):
        queryset = File.objects.filter(user_id=user_id)
        file_filter = FileFilter(data=filters, queryset=queryset)

        if not file_filter.is_valid():
            raise ValidationError(file_filter.errors)

        return file_filter.qs
