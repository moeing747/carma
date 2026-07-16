from carma.application.polling import PollSchedule

SCHEDULE = PollSchedule(interval_seconds=30.0, max_backoff_seconds=300.0)


def test_steady_state_deducts_poll_duration_from_interval() -> None:
    assert SCHEDULE.next_delay(elapsed_seconds=4.0, consecutive_failures=0) == 26.0


def test_slow_poll_never_yields_negative_delay() -> None:
    assert SCHEDULE.next_delay(elapsed_seconds=45.0, consecutive_failures=0) == 0.0


def test_first_failure_backs_off_by_one_interval() -> None:
    assert SCHEDULE.next_delay(elapsed_seconds=1.0, consecutive_failures=1) == 30.0


def test_backoff_doubles_per_consecutive_failure() -> None:
    assert SCHEDULE.next_delay(elapsed_seconds=1.0, consecutive_failures=2) == 60.0
    assert SCHEDULE.next_delay(elapsed_seconds=1.0, consecutive_failures=3) == 120.0


def test_backoff_is_capped() -> None:
    assert SCHEDULE.next_delay(elapsed_seconds=1.0, consecutive_failures=10) == 300.0
