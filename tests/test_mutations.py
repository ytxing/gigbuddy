from tui.mutations import MutationCommitted, MutationRefreshCoordinator


def _coordinator():
    scheduled = []
    reconciled = []
    coordinator = MutationRefreshCoordinator(
        lambda callback: scheduled.append(callback), reconciled.append)
    return coordinator, scheduled, reconciled


def test_same_revision_events_merge_but_preserve_all_keys_and_operations():
    coordinator, scheduled, reconciled = _coordinator()
    coordinator.receive(MutationCommitted("install", ("tone:1",), 7))
    coordinator.receive(MutationCommitted("install", ("model:2",), 7))

    assert len(scheduled) == 1
    scheduled.pop()()

    assert len(reconciled) == 1
    assert reconciled[0].operation == "batch"
    assert reconciled[0].revision == 7
    assert reconciled[0].keys == ("tone:1", "model:2")
    assert reconciled[0].operations == ("install", "install")


def test_unversioned_events_are_reconciled_in_arrival_order():
    coordinator, scheduled, reconciled = _coordinator()
    coordinator.receive(MutationCommitted("install", ("tone:1",)))
    coordinator.receive(MutationCommitted("uninstall", ("tone:2",)))

    scheduled.pop()()

    assert [(event.operation, event.keys) for event in reconciled] == [
        ("install", ("tone:1",)),
        ("uninstall", ("tone:2",)),
    ]


def test_repeated_same_event_object_is_coalesced_without_revision():
    coordinator, scheduled, reconciled = _coordinator()
    event = MutationCommitted("install", ("tone:1",))
    coordinator.receive(event)
    coordinator.receive(event)

    scheduled.pop()()

    assert len(reconciled) == 1
    assert reconciled[0].keys == ("tone:1",)


def test_non_adjacent_repeated_same_event_object_is_coalesced():
    coordinator, scheduled, reconciled = _coordinator()
    event = MutationCommitted("install", ("tone:1",))
    coordinator.receive(event)
    coordinator.receive(MutationCommitted("uninstall", ("tone:2",)))
    coordinator.receive(event)

    scheduled.pop()()

    assert [(item.operation, item.keys) for item in reconciled] == [
        ("install", ("tone:1",)),
        ("uninstall", ("tone:2",)),
    ]


def test_capture_runs_once_per_schedule_round_before_reconcile():
    """Anchors are captured synchronously at publish time, before any page
    refresh or reconcile can move the viewport (a capture deferred to flush
    could read a table that an unrelated tick already cleared)."""
    scheduled = []
    trace = []
    coordinator = MutationRefreshCoordinator(
        lambda callback: scheduled.append(callback),
        lambda event: trace.append(("reconcile", event.operation)),
        lambda: trace.append(("capture",)),
    )
    coordinator.receive(MutationCommitted("install", ("tone:1",), 7))
    coordinator.receive(MutationCommitted("install", ("model:2",), 7))
    coordinator.receive(MutationCommitted("uninstall", ("tone:3",)))

    assert trace == [("capture",)]
    scheduled.pop()()

    assert trace == [
        ("capture",),
        ("reconcile", "batch"),
        ("reconcile", "uninstall"),
    ]


def test_capture_repeats_for_each_schedule_round():
    """A later mutation starts a new round and captures fresh anchors."""
    scheduled = []
    trace = []
    coordinator = MutationRefreshCoordinator(
        lambda callback: scheduled.append(callback),
        lambda event: trace.append(("reconcile", event.operation)),
        lambda: trace.append(("capture",)),
    )
    coordinator.receive(MutationCommitted("install", ("tone:1",), 7))
    scheduled.pop()()
    assert trace == [("capture",), ("reconcile", "install")]

    coordinator.receive(MutationCommitted("uninstall", ("tone:2",)))
    assert trace == [("capture",), ("reconcile", "install"), ("capture",)]
    scheduled.pop()()
    assert trace == [
        ("capture",), ("reconcile", "install"), ("capture",),
        ("reconcile", "uninstall"),
    ]
