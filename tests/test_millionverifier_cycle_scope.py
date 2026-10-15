from datetime import datetime, timezone
from scripts.clean_enriched_export import _current_cycle_scope_sql


def test_current_cycle_scope_matches_batches_ending_in_current_month():
    sql, params = _current_cycle_scope_sql(
        target_date=datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    )

    assert "l.cycle" in sql
    assert "l.batch" in sql
    assert params == [
        "-[[:space:]]*oct[[:space:]]+[0-9]{1,2}",
        "-[[:space:]]*oct[[:space:]]+[0-9]{1,2}",
    ]
