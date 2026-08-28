"""Who may ask: the config ceiling, the runtime overlay, and the panic exemption.

These are tests for the *sixth* gate.  The other five decide whether an action
may happen at all; this one decides whether a particular client may ask for it,
and the two questions are kept apart on purpose -- so most of what is checked
here is that turning a source off does not turn an interlock off, and that
turning one on does not turn an interlock on.
"""

from __future__ import annotations

import json
import time

import pytest

from lschart.config import ConfigError, load
from lschart.instruments.ls33x import LS33x
from lschart.instruments.sim import Sim33x, SimulatedCryostat
from lschart.ipc.commands import CommandSpool
from lschart.ipc.service import IpcService
from lschart.ipc.sources import SourcePolicy, source_key
from lschart.ipc.status import read_status
from lschart.model import Frame, Reading
from lschart.transport import LoopbackTransport


class _Poller:
    """Stands in for the poller, which is where `hold` finds its temperatures."""

    def __init__(self, frame):
        self.last_frame = frame


def instrument(name="ls336", *, allow_writes=True) -> LS33x:
    return LS33x(
        LoopbackTransport(Sim33x(SimulatedCryostat(None, start_k=96.0), model="336"),
                          inter_command_delay=0.0),
        model="336", name=name, allow_writes=allow_writes,
        channels={"A": f"{name}-A"},
    )


def service(tmp_path, *instruments, **kw) -> IpcService:
    kw.setdefault("accept_commands", True)
    kw.setdefault("sources_path", str(tmp_path / "sources.json"))
    svc = IpcService(
        status_path=tmp_path / "status.json",
        spool=CommandSpool(tmp_path / "commands"),
        instruments=list(instruments) or [instrument()],
        **kw,
    )
    svc.start()
    return svc


def tick(svc: IpcService) -> dict:
    svc.on_frame(Frame(t_wall=time.time(), t_mono=time.monotonic(),
                       readings={"Sample": Reading("Sample", 96.0)}))
    return read_status(svc.writer.path)


def ack(status: dict, cid: str) -> dict:
    for entry in status["commands"]["recent"]:
        if entry["id"] == cid:
            return entry
    raise AssertionError(f"no acknowledgement for {cid}")


def send(svc: IpcService, kind: str, **kw) -> dict:
    """Queue one command, run a cycle, and hand back its acknowledgement.

    Written as one call because the two halves have to happen in that order,
    and passing a submit() call as an argument to tick()'s result would
    evaluate them in the other.
    """
    cid = svc.spool.submit(kind, **kw)
    return ack(tick(svc), cid)


def overlay(tmp_path, mapping) -> None:
    (tmp_path / "sources.json").write_text(json.dumps(mapping), encoding="utf-8")


# -- the key ----------------------------------------------------------------


def test_a_source_is_matched_on_the_part_before_the_slash():
    """The CLI stamps its pid in, so no fixed key could match the whole label."""
    assert source_key("lschart-cli/12345") == "lschart-cli"
    assert source_key("matlab") == "matlab"
    assert source_key("") == ""


# -- the config ceiling -----------------------------------------------------


def test_with_no_policy_configured_every_source_may_ask(tmp_path):
    """Every config written before this existed has to keep working."""
    svc = service(tmp_path)
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0, source="anything")
    assert ack(tick(svc), cid)["ok"]


def test_a_source_the_policy_omits_is_refused(tmp_path):
    """`default` is false once a policy exists: a typo must fail closed."""
    svc = service(tmp_path, sources={"matlab": True})
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0, source="lschart-gui")
    entry = ack(tick(svc), cid)
    assert not entry["ok"]
    assert "lschart-gui" in entry["message"]
    assert "['matlab']" in entry["message"]


def test_a_source_the_policy_names_may_ask(tmp_path):
    inst = instrument()
    svc = service(tmp_path, inst, sources={"matlab": True})
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0, source="matlab")
    assert ack(tick(svc), cid)["ok"]
    assert inst.setpoint(1) == pytest.approx(77.0, abs=0.01)


def test_a_default_of_true_permits_everything_but_what_it_names(tmp_path):
    svc = service(tmp_path, sources={"default": True, "lschart-gui": False})
    good = svc.spool.submit("ping", source="matlab")
    bad = svc.spool.submit("ping", source="lschart-gui")
    status = tick(svc)
    assert ack(status, good)["ok"]
    assert not ack(status, bad)["ok"]


def test_the_cli_matches_despite_its_pid(tmp_path):
    svc = service(tmp_path, sources={"lschart-cli": True})
    cid = svc.spool.submit("ping", source="lschart-cli/98765")
    assert ack(tick(svc), cid)["ok"]


def test_an_unlabelled_command_is_refused_by_name_not_silently(tmp_path):
    svc = service(tmp_path, sources={"matlab": True})
    cid = svc.spool.submit("ping")
    entry = ack(tick(svc), cid)
    assert not entry["ok"]
    assert "(unlabelled)" in entry["message"]


def test_the_refusal_says_a_restart_is_needed(tmp_path):
    """The config ceiling and the overlay fail with different remedies."""
    svc = service(tmp_path, sources={"matlab": True})
    cid = svc.spool.submit("ping", source="somebody")
    assert "restart" in ack(tick(svc), cid)["message"]


# -- the runtime overlay ----------------------------------------------------


def test_the_overlay_switches_a_permitted_source_off(tmp_path):
    svc = service(tmp_path, sources={"default": True})
    assert send(svc, "ping", source="matlab")["ok"]

    overlay(tmp_path, {"matlab": False})
    entry = send(svc, "ping", source="matlab")
    assert not entry["ok"]
    assert "no restart needed" in entry["message"]


def test_the_overlay_may_not_widen_the_configured_policy(tmp_path):
    """The whole point: a file on disk cannot grant what the config refuses."""
    svc = service(tmp_path, sources={"matlab": True})
    overlay(tmp_path, {"lschart-gui": True})
    entry = send(svc, "ping", source="lschart-gui")
    assert not entry["ok"]
    assert "configuration" in entry["message"]


def test_deleting_the_overlay_clears_the_lockout(tmp_path):
    svc = service(tmp_path, sources={"default": True})
    overlay(tmp_path, {"matlab": False})
    assert not send(svc, "ping", source="matlab")["ok"]

    (tmp_path / "sources.json").unlink()
    assert send(svc, "ping", source="matlab")["ok"]


def test_the_nested_shape_reads_too(tmp_path):
    """`{"sources": {...}}` is what somebody writes from memory at 2 a.m."""
    svc = service(tmp_path, sources={"default": True})
    overlay(tmp_path, {"sources": {"matlab": False}})
    assert not send(svc, "ping", source="matlab")["ok"]


def test_a_torn_overlay_keeps_the_last_good_one_rather_than_widening(tmp_path):
    """Half a file is not permission.  Failing open here is the wrong direction."""
    svc = service(tmp_path, sources={"default": True})
    overlay(tmp_path, {"matlab": False})
    assert not send(svc, "ping", source="matlab")["ok"]

    (tmp_path / "sources.json").write_text('{"matlab": fal', encoding="utf-8")
    entry = send(svc, "ping", source="matlab")
    assert not entry["ok"]


def test_a_nonsense_overlay_value_is_ignored_not_obeyed(tmp_path):
    svc = service(tmp_path, sources={"default": True})
    overlay(tmp_path, {"matlab": "no thanks"})
    assert send(svc, "ping", source="matlab")["ok"]


def test_the_overlay_is_not_re_read_when_nothing_changed(tmp_path):
    """It is read every cycle; it must not be *parsed* every cycle."""
    policy = SourcePolicy({"default": True}, overlay_path=tmp_path / "sources.json")
    overlay(tmp_path, {"matlab": False})
    policy.refresh()
    signature = policy._last_signature
    policy.refresh()
    assert policy._last_signature is signature


# -- what it does not do ----------------------------------------------------


def test_permitting_a_source_does_not_open_a_power_gate(tmp_path):
    """Six gates, not one with six names."""
    svc = service(tmp_path, sources={"default": True})
    entry = send(svc, "range", output=1, value=3, source="matlab")
    assert not entry["ok"]
    assert "ipc.allow_heater_range" in entry["message"]


def test_permitting_a_source_does_not_make_a_read_only_box_writable(tmp_path):
    svc = service(tmp_path, instrument(allow_writes=False),
                  sources={"default": True})
    entry = send(svc, "setpoint", loop=1, kelvin=77.0, source="matlab")
    assert not entry["ok"]


def test_a_refused_source_still_reaches_hold(tmp_path):
    """Both panic kinds, not just the one that was there first."""
    inst = instrument()
    inst.read_frame()
    svc = service(tmp_path, inst, sources={"matlab": True})
    svc.poller = _Poller(Frame(
        t_wall=time.time(), t_mono=time.monotonic(),
        readings={inst.channels["A"]: Reading(inst.channels["A"], 88.0)},
    ))
    entry = send(svc, "hold", source="lschart-gui")
    assert entry["ok"], entry["message"]
    assert inst.setpoint(1) == pytest.approx(88.0, abs=0.01)


def test_arm_is_not_a_panic_kind_and_obeys_the_source_policy(tmp_path):
    """Arming applies power, so the exemption for stopping does not cover it."""
    svc = service(tmp_path, sources={"matlab": True}, allow_analog_output=True)
    entry = send(svc, "arm", source="lschart-gui")
    assert not entry["ok"]
    assert "not accepted by this recorder's configuration" in entry["message"]


def test_a_refused_source_still_reaches_the_panic_button(tmp_path):
    """The exemption belongs to the kind, so MATLAB gets it too -- deliberately."""
    inst = instrument()
    inst.set_heater_range(1, 3)
    svc = service(tmp_path, inst, sources={"matlab": True})
    cid = svc.spool.submit("heaters_off", source="lschart-gui")
    assert ack(tick(svc), cid)["ok"]
    assert inst.heater_range(1) == 0


def test_the_panic_exemption_does_not_reach_a_read_only_box(tmp_path):
    """It bypasses the source policy, not `allow_writes`."""
    svc = service(tmp_path, instrument(allow_writes=False),
                  sources={"matlab": True})
    entry = send(svc, "heaters_off", source="lschart-gui")
    assert not entry["ok"]
    assert "writable" in entry["message"]


def test_a_source_policy_does_not_bypass_accept_commands(tmp_path):
    svc = service(tmp_path, accept_commands=False, sources={"matlab": True})
    entry = send(svc, "ping", source="matlab")
    assert not entry["ok"]
    assert "accept_commands" in entry["message"]


# -- what the status file publishes -----------------------------------------


def test_the_status_file_publishes_the_policy_as_an_array(tmp_path):
    """An object keyed by source would reach MATLAB as `lschart_cli`."""
    svc = service(tmp_path, sources={"matlab": True, "lschart-cli": False})
    overlay(tmp_path, {"matlab": False})
    cmds = tick(svc)["commands"]
    assert cmds["source_policy"] is True
    assert cmds["source_default"] is False
    by_name = {e["name"]: e for e in cmds["sources"]}
    assert set(by_name) == {"matlab", "lschart-cli"}
    assert by_name["matlab"] == {
        "name": "matlab", "allowed": False,
        "configured": True, "disabled_at_runtime": True,
    }
    assert by_name["lschart-cli"]["configured"] is False


def test_a_recorder_with_no_policy_says_so_in_the_status_file(tmp_path):
    cmds = tick(service(tmp_path))["commands"]
    assert cmds["source_policy"] is False
    assert cmds["source_default"] is True
    assert cmds["sources"] == []


# -- the viewer's half ------------------------------------------------------


def test_the_viewer_degrades_open_against_a_recorder_with_no_policy():
    from lschart.gui.source import StatusSource

    src = StatusSource("nowhere.json")
    src.status = {"commands": {"accepted": True}}
    assert src.source_allowed("lschart-gui")
    assert src.source_note("lschart-gui") == ""


def test_the_viewer_knows_when_it_has_been_switched_off_at_runtime():
    from lschart.gui.source import StatusSource

    src = StatusSource("nowhere.json")
    src.status = {"commands": {
        "accepted": True, "source_policy": True, "source_default": True,
        "sources": [{"name": "lschart-gui", "allowed": False,
                     "configured": True, "disabled_at_runtime": True}],
    }}
    assert not src.source_allowed("lschart-gui")
    assert "sources.json" in src.source_note("lschart-gui")
    assert "no restart" in src.source_note("lschart-gui")


def test_the_viewer_distinguishes_a_config_lockout_from_a_runtime_one():
    from lschart.gui.source import StatusSource

    src = StatusSource("nowhere.json")
    src.status = {"commands": {
        "accepted": True, "source_policy": True, "source_default": False,
        "sources": [{"name": "matlab", "allowed": True,
                     "configured": True, "disabled_at_runtime": False}],
    }}
    assert not src.source_allowed("lschart-gui")
    assert "restart" in src.source_note("lschart-gui")
    assert src.source_allowed("matlab")


# -- config ------------------------------------------------------------------


def test_the_config_rejects_a_source_policy_that_is_not_yes_or_no(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "instruments:\n  - model: '336'\n    driver: sim\n"
        "ipc:\n  sources:\n    matlab: maybe\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="true or false"):
        load(str(path))


def test_the_config_rejects_a_policy_that_permits_nothing(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "instruments:\n  - model: '336'\n    driver: sim\n"
        "ipc:\n  sources:\n    matlab: false\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="permits nothing"):
        load(str(path))


def test_a_config_carries_the_policy_through_to_the_recorder(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "instruments:\n  - model: '336'\n    driver: sim\n"
        f"ipc:\n  directory: {tmp_path / 'data'}\n  sources:\n    matlab: true\n",
        encoding="utf-8",
    )
    cfg = load(str(path))
    assert cfg.ipc.sources == {"matlab": True}
    assert cfg.ipc.sources_path().endswith("sources.json")


# -- muting and un-muting by command -----------------------------------------
#
# The overlay is the same file either way; this is the second way to write it.
# The command is exempt from the policy it edits, which is what makes muting
# something other than a one-way door -- the one client that needs to undo a
# lockout is the one it just silenced.


def overlay_now(tmp_path) -> dict:
    return json.loads((tmp_path / "sources.json").read_text())


def test_a_source_command_mutes_a_client(tmp_path):
    svc = service(tmp_path, sources={"default": True})
    entry = send(svc, "source", name="matlab", allowed=False, source="lschart-gui")
    assert entry["ok"], entry["message"]
    assert overlay_now(tmp_path) == {"matlab": False}
    assert not send(svc, "ping", source="matlab")["ok"]


def test_a_muted_client_can_un_mute_itself(tmp_path):
    """The whole reason the command is exempt from the policy it edits."""
    svc = service(tmp_path, sources={"default": True})
    overlay(tmp_path, {"lschart-gui": False})
    assert not send(svc, "ping", source="lschart-gui")["ok"]

    entry = send(svc, "source", name="lschart-gui", allowed=True,
                 source="lschart-gui")
    assert entry["ok"], entry["message"]
    assert send(svc, "ping", source="lschart-gui")["ok"]


def test_un_muting_removes_the_entry_rather_than_writing_true(tmp_path):
    """The overlay means "what has been taken away". An entry saying a source
    is allowed says nothing the absence of one does not."""
    svc = service(tmp_path, sources={"default": True})
    overlay(tmp_path, {"matlab": False, "lschart-gui": False})
    send(svc, "source", name="matlab", allowed=True, source="lschart-gui")
    assert overlay_now(tmp_path) == {"lschart-gui": False}


def test_a_source_command_does_not_disturb_other_entries(tmp_path):
    """Read-modify-write: a blind overwrite would be a lockout appearing from
    nowhere, or one vanishing."""
    svc = service(tmp_path, sources={"default": True})
    overlay(tmp_path, {"matlab": False})
    send(svc, "source", name="lschart-cli", allowed=False, source="lschart-gui")
    assert overlay_now(tmp_path) == {"matlab": False, "lschart-cli": False}


def test_the_command_may_not_widen_past_the_config(tmp_path):
    """The overlay's one rule, and it survives having a command behind it."""
    svc = service(tmp_path, sources={"matlab": True})
    entry = send(svc, "source", name="lschart-gui", allowed=True, source="matlab")
    assert not entry["ok"]
    assert "restart" in entry["message"]
    assert not send(svc, "ping", source="lschart-gui")["ok"]


def test_the_cli_can_be_muted_by_its_bare_name(tmp_path):
    """It stamps its pid in, so the name is the part before the slash."""
    svc = service(tmp_path, sources={"default": True})
    send(svc, "source", name="lschart-cli", allowed=False, source="lschart-gui")
    assert not send(svc, "ping", source="lschart-cli/4321")["ok"]


def test_a_source_command_needs_to_say_which_source(tmp_path):
    svc = service(tmp_path, sources={"default": True})
    assert not send(svc, "source", allowed=False)["ok"]


def test_a_source_command_needs_to_say_which_way(tmp_path):
    """Defaulting either way would be a guess about an interlock."""
    svc = service(tmp_path, sources={"default": True})
    entry = send(svc, "source", name="matlab")
    assert not entry["ok"] and "allowed" in entry["message"]


def test_muting_does_not_stop_the_status_file_being_written(tmp_path):
    """Muted is about listening, never about reading -- Jeff's own framing.
    A muted client reads temperatures exactly as before."""
    svc = service(tmp_path, sources={"default": True})
    send(svc, "source", name="matlab", allowed=False, source="lschart-gui")
    status = tick(svc)
    assert status["channels"]
    assert status["links"]
    assert status["running"] is True


def test_a_muted_source_still_reaches_the_panic_kinds(tmp_path):
    inst = instrument()
    inst.set_heater_range(1, 3)
    svc = service(tmp_path, inst, sources={"default": True})
    overlay(tmp_path, {"matlab": False})
    assert send(svc, "heaters_off", source="matlab")["ok"]
    assert inst.heater_range(1) == 0


def test_the_status_file_shows_a_client_it_has_been_told_to_ignore(tmp_path):
    svc = service(tmp_path, sources={"default": True})
    send(svc, "source", name="lschart-gui", allowed=False, source="matlab")
    by_name = {e["name"]: e for e in tick(svc)["commands"]["sources"]}
    assert by_name["lschart-gui"] == {
        "name": "lschart-gui", "allowed": False,
        "configured": True, "disabled_at_runtime": True,
    }
