"""Tests for structured logging configuration."""

from __future__ import annotations

import json
import logging

from w2v_common.observability.request_context import (
    clear_request_id,
    set_request_id,
)

from src.logging_config import (
    ContextFormatter,
    JSONFormatter,
    configure_logging,
    generate_request_id,
    job_id_var,
    tenant_id_var,
)


def test_generate_request_id_length():
    """Test that generated request IDs are 12 chars."""
    rid = generate_request_id()
    assert len(rid) == 12


def test_generate_request_id_unique():
    """Test that generated request IDs are unique."""
    ids = {generate_request_id() for _ in range(100)}
    assert len(ids) == 100


def test_json_formatter_basic():
    """Test JSONFormatter produces valid JSON."""
    formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "hello world"
    assert parsed["logger"] == "test"
    assert "timestamp" in parsed


def test_json_formatter_with_context_vars():
    """Test JSONFormatter includes context vars when set."""
    formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    set_request_id("req-abc")
    token_tid = tenant_id_var.set("tenant-xyz")
    token_jid = job_id_var.set("job-123")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["request_id"] == "req-abc"
        assert parsed["tenant_id"] == "tenant-xyz"
        assert parsed["job_id"] == "job-123"
    finally:
        clear_request_id()
        tenant_id_var.reset(token_tid)
        job_id_var.reset(token_jid)


def test_json_formatter_with_exception():
    """Test JSONFormatter includes exception info."""
    formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    try:
        raise ValueError("test error")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


def test_context_formatter_with_context():
    """Test ContextFormatter includes context prefix."""
    formatter = ContextFormatter(
        fmt="%(asctime)s - %(ctx)s%(message)s",
        datefmt="%Y-%m-%d",
    )
    set_request_id("req-123")
    token_tid = tenant_id_var.set("t1")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "req=req-123" in output
        assert "tenant=t1" in output
    finally:
        clear_request_id()
        tenant_id_var.reset(token_tid)


def test_context_formatter_without_context():
    """Test ContextFormatter without context vars."""
    formatter = ContextFormatter(
        fmt="%(ctx)s%(message)s",
        datefmt="%Y-%m-%d",
    )
    clear_request_id()
    token_tid = tenant_id_var.set("")
    token_jid = job_id_var.set("")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="no context",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert output == "no context"
    finally:
        clear_request_id()
        tenant_id_var.reset(token_tid)
        job_id_var.reset(token_jid)


def test_configure_logging_text():
    """Test configure_logging with text format."""
    configure_logging(json_logs=False, level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) >= 1


def test_configure_logging_json():
    """Test configure_logging with JSON format."""
    configure_logging(json_logs=True, level="WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) >= 1
    handler = root.handlers[-1]
    assert isinstance(handler.formatter, JSONFormatter)
