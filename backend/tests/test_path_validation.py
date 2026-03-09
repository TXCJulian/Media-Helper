"""Tests for path traversal prevention."""
import os
import pytest
from fastapi import HTTPException
from app.main import validate_path


class TestValidatePath:
    def test_valid_subdirectory(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        result = validate_path(str(tmp_path), "subdir")
        assert result == str(sub.resolve())

    def test_valid_nested_subdirectory(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        result = validate_path(str(tmp_path), "a/b")
        assert os.path.realpath(result) == str(nested.resolve())

    def test_path_traversal_parent(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            validate_path(str(tmp_path), "../../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_path_traversal_dotdot(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        with pytest.raises(HTTPException) as exc_info:
            validate_path(str(sub), "../")
        assert exc_info.value.status_code == 400

    def test_path_traversal_encoded_stays_within_base(self, tmp_path):
        # URL-encoded traversal is decoded by FastAPI before reaching validate_path,
        # so the literal "..%2F" is treated as a directory name and stays in base.
        result = validate_path(str(tmp_path), "..%2F..%2Fetc%2Fpasswd")
        base_resolved = os.path.realpath(str(tmp_path))
        assert result.startswith(base_resolved)

    def test_base_path_itself(self, tmp_path):
        result = validate_path(str(tmp_path), ".")
        assert result == str(tmp_path.resolve())

    def test_empty_input(self, tmp_path):
        result = validate_path(str(tmp_path), "")
        assert result == str(tmp_path.resolve())
