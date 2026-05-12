from django.db.models import Sum
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

    @staticmethod
    def get_storage_stats(user_id):
        total_storage_used = (
            File.objects.filter(user_id=user_id, is_reference=False).aggregate(total=Sum('size'))[
                'total'
            ]
            or 0
        )
        original_storage_used = (
            File.objects.filter(user_id=user_id).aggregate(total=Sum('size'))['total'] or 0
        )
        storage_savings = original_storage_used - total_storage_used
        savings_percentage = (
            (storage_savings / original_storage_used) * 100
            if original_storage_used > 0
            else 0.0
        )

        return {
            'user_id': user_id,
            'total_storage_used': total_storage_used,
            'original_storage_used': original_storage_used,
            'storage_savings': storage_savings,
            'savings_percentage': savings_percentage,
        }

    @staticmethod
    def get_file_types(user_id):
        return list(
            File.objects.filter(user_id=user_id)
            .order_by('file_type')
            .values_list('file_type', flat=True)
            .distinct()
        )
