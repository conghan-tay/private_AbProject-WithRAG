from django.db import models
from django.db.models.deletion import SET_NULL

from files.models import File


def get_field(name):
    return File._meta.get_field(name)


def test_file_model_fields_match_prd_contract():
    expected_fields = [
        'id',
        'user_id',
        'file',
        'original_filename',
        'file_type',
        'size',
        'file_hash',
        'is_reference',
        'original_file',
        'reference_count',
        'uploaded_at',
        'encryption_iv',
    ]

    assert [field.name for field in File._meta.fields] == expected_fields
    assert isinstance(get_field('id'), models.UUIDField)
    assert isinstance(get_field('user_id'), models.CharField)
    assert get_field('user_id').max_length == 128
    assert isinstance(get_field('file'), models.FileField)
    assert isinstance(get_field('original_filename'), models.CharField)
    assert get_field('original_filename').max_length == 255
    assert isinstance(get_field('file_type'), models.CharField)
    assert get_field('file_type').max_length == 128
    assert isinstance(get_field('size'), models.BigIntegerField)
    assert isinstance(get_field('file_hash'), models.CharField)
    assert get_field('file_hash').max_length == 64
    assert isinstance(get_field('is_reference'), models.BooleanField)
    assert isinstance(get_field('original_file'), models.ForeignKey)
    assert get_field('original_file').remote_field.model is File
    assert get_field('original_file').remote_field.on_delete is SET_NULL
    assert isinstance(get_field('reference_count'), models.PositiveIntegerField)
    assert isinstance(get_field('uploaded_at'), models.DateTimeField)
    assert isinstance(get_field('encryption_iv'), models.BinaryField)
    assert get_field('encryption_iv').max_length == 16


def test_file_model_nullability_and_defaults_match_prd_contract():
    nullable_fields = {field.name for field in File._meta.fields if field.null}
    blank_fields = {field.name for field in File._meta.fields if field.blank}

    assert nullable_fields == {'file', 'original_file', 'encryption_iv'}
    assert {'file', 'original_file', 'encryption_iv'}.issubset(blank_fields)
    assert get_field('is_reference').default is False
    assert get_field('reference_count').default == 1
    assert get_field('uploaded_at').auto_now_add is True


def test_file_model_indexes_match_prd_contract():
    expected_indexes = {
        'files_file_user_id': ['user_id'],
        'files_file_file_type': ['file_type'],
        'files_file_size': ['size'],
        'files_file_uploaded_at': ['uploaded_at'],
        'files_file_file_hash': ['file_hash'],
        'files_file_original_filename': ['original_filename'],
        'files_file_user_uploaded_at': ['user_id', 'uploaded_at'],
        'files_file_user_is_reference': ['user_id', 'is_reference'],
    }

    actual_indexes = {index.name: list(index.fields) for index in File._meta.indexes}

    assert actual_indexes == expected_indexes


def test_file_model_has_step_7_dedup_unique_constraint_for_originals_only():
    assert len(File._meta.constraints) == 1
    constraint = File._meta.constraints[0]

    assert constraint.name == 'files_file_unique_original_hash_per_user'
    assert list(constraint.fields) == ['user_id', 'file_hash']
    assert constraint.condition.children == [('is_reference', False)]


def test_file_model_default_ordering_newest_first():
    assert File._meta.ordering == ['-uploaded_at']
