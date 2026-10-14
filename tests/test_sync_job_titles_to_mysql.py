from scripts import sync_job_titles_to_mysql as sync


class _FailingCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        raise ValueError("invalid job-title payload")


class _FailingConnection:
    def cursor(self):
        return _FailingCursor()

    def rollback(self):
        pass


def test_failed_batch_keeps_checkpoint_and_reports_last_error(monkeypatch, capsys):
    connection = _FailingConnection()
    chunk = [(101, "one@example.com", "Manager")]
    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: None)

    returned, ok, errors = sync.execute_chunk_with_retry(connection, chunk)

    assert returned is connection
    assert ok == 0
    assert errors == 1
    assert sync.next_checkpoint_id(100, chunk, ok) == 100
    assert "invalid job-title payload" in capsys.readouterr().err


def test_successful_batch_advances_checkpoint():
    chunk = [(101, "one@example.com", "Manager"), (102, "two@example.com", "Director")]

    assert sync.next_checkpoint_id(100, chunk, len(chunk)) == 102
