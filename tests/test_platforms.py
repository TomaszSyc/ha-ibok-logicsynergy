"""Every platform must say how many updates may run at once.

The quality scale asks for the limit explicitly; without it Home Assistant
derives one from whether an entity has a sync ``update``. The button must not
use a semaphore: the ledger refuses a concurrent submission at once, and a
semaphore would turn that refusal into a wait.
"""


def test_platforms_declare_how_many_updates_may_run_at_once() -> None:
    from custom_components.ibok import button, number, sensor

    # Data comes from the coordinator; the typed value stays local.
    assert sensor.PARALLEL_UPDATES == 0
    assert number.PARALLEL_UPDATES == 0
    # The ledger refuses concurrent submissions itself.
    assert button.PARALLEL_UPDATES == 0
