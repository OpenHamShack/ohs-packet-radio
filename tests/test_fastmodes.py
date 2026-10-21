"""The fast and wide modes in the app (0.2.1): a Pluto's link and guard
made from its fast settings, the engine given the mode, window and SREJ
for a Pluto only, arming refused for a wide mode outside the band plan,
and the band-plan facts the window shows. No SDR is opened: PlutoLink is
only constructed (its libiio module a stand-in) and the controller's
Pluto is test_sdr_radio's FakeSdrLink."""
from __future__ import annotations

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import fastmodes, host, links
from shack_packet_app.controller import Controller
from shack_packet_app.settings import FastRadioSettings, SdrRadioSettings, Settings
from test_sdr_radio import FakeSdrLink


@pytest.fixture
def no_libiio(monkeypatch):
    """PlutoLink keeps the libiio module it is given and opens nothing until
    open(): a stand-in lets the link be made where libiio is not installed."""
    monkeypatch.setattr(host, "import_iio", lambda: object(), raising=False)


def _pluto(fast, *, transmit=True, hz=1_299_525_000, sdr=None, tmp_path=None):
    s = Settings()
    s.radio.kind = "pluto"
    return links.make_link(s.radio, station=None, frequency_hz=hz, transmit=transmit,
                           sdr=sdr or SdrRadioSettings(pluto_tx_gain_db=-10.0), secrets={}, data_dir=tmp_path,
                           fast=fast)


def test_a_pluto_link_and_its_guard_take_the_fast_settings_for_the_band(no_libiio, tmp_path):
    fast = FastRadioSettings(mode="gfsk76800", ptt_output="gpo1", relay_lead_ms=30.0, relay_tail_ms=15.0,
                             drive_caps={"23cm": -20.0, "70cm": -5.0})
    sdr = SdrRadioSettings(pluto_uri="pluto://ohs-box?iiod=30431&ssh=30422", pluto_tx_gain_db=-10.0)
    link = _pluto(fast, sdr=sdr, tmp_path=tmp_path)
    assert link.mode == "gfsk76800" and link.ptt_output == "gpo1" and link.relay_lead_s == 0.03
    # The 23 cm cap, not the 70 cm one; the gain asked for is above it, so
    # the link transmits at the cap rather than refusing to open at all.
    assert link.max_tx_gain_db == -20.0 and link.tx_gain_db == -20.0
    assert link.guard.gpo is True and link.guard.max_gain_db == -20.0
    assert (link.guard.runner.host, link.guard.runner.port) == ("ohs-box", 30422)
    assert link.uri == "ip:ohs-box" and link.ifo_hz == 100_000.0


def test_an_adaptive_pluto_carries_its_ladder_and_a_wide_top_moves_the_lo_200_khz(no_libiio, tmp_path):
    link = _pluto(FastRadioSettings(adapt=True, top_mode="4fsk76800", floor_mode="g3ruh9600"), tmp_path=tmp_path)
    assert link.modes[0] == "4fsk76800" and link.modes[-1] == "g3ruh9600"
    assert link.ifo_hz == 200_000.0
    assert fastmodes.ifo_hz(FastRadioSettings(adapt=True).for_frequency(1_299_525_000)) == 200_000.0


def test_afsk_on_a_pluto_is_as_before_and_its_guard_leaves_the_gpos_alone(no_libiio, tmp_path):
    link = _pluto(FastRadioSettings(), hz=144_800_000, tmp_path=tmp_path)
    assert link.mode == "afsk1200" and link.ptt_output == "none" and link.max_tx_gain_db is None
    assert link.guard.gpo is False and link.guard.max_gain_db is None


def test_fast_settings_the_library_refuses_refuse_the_link_by_their_key(no_libiio, tmp_path):
    with pytest.raises(links.LinkError) as e:
        _pluto(FastRadioSettings(mode="g3ruh9600", window=99, window_auto=False), tmp_path=tmp_path)
    assert e.value.reason == "fast-window"
    # Out of the band plan is not a reason to stop receiving: the link opens.
    assert _pluto(FastRadioSettings(mode="gfsk76800"), hz=1_298_500_000, tmp_path=tmp_path).mode == "gfsk76800"


# -- the station core ---------------------------------------------------------------------------

@pytest.fixture
def ctl():
    made = []

    def make(kind="pluto", fast=None, **kw):
        s = station_settings(**kw)
        s.radio.kind = kind
        s.fast = fast or FastRadioSettings()

        def fake(*_a, **k):
            # Set up for the mode the real link would carry: the engine
            # refuses a channel whose mode the link does not carry.
            link = FakeSdrLink(station=k["station"])
            kw = k["fast"].for_frequency(k["frequency_hz"]).pluto_kwargs()
            link.modes = kw.get("modes")
            link.mode = (link.modes or (kw.get("mode"),))[0]
            from shack_packet import modem
            m = modem.mode(link.mode)
            if m.fast:
                link.rate, link.baud = m.baud * 8, m.bitrate
            return link

        def factory(radio, **lk):
            return links.make_link(radio, **lk, sdr_factory=fake)
        c = Controller(s, link_factory=factory)
        made.append(c)
        c.start()
        if kind != "simulator":
            assert wait_for(lambda: c.link_state == "open", 5)
        return c
    yield make
    for c in made:
        c.stop()


def test_a_pluto_on_a_fast_mode_gives_the_engine_its_mode_window_and_srej(ctl):
    c = ctl(channel__frequency_hz=1_299_525_000, fast=FastRadioSettings(mode="g3ruh38400", window=63, paclen=256))
    assert c.engine.channel.config.mode == "g3ruh38400" and c.engine.channel.config.max_frames == 64
    lc = c.engine.link_config
    assert (lc.k, lc.n1, lc.modulo, lc.srej) == (63, 256, 128, True)


def test_an_adaptive_pluto_gets_its_slowest_modes_window_modulo_128_and_its_ladder_by_default(ctl):
    """The link test's adaptive run: not the engine's default k 4, and the
    window one over carries at the slowest mode it may fall to (9600: 31,
    fastmodes.default_window)."""
    c = ctl(channel__frequency_hz=1_299_525_000, fast=FastRadioSettings(adapt=True))
    lc = c.engine.link_config
    assert (lc.k, lc.modulo, lc.n1, lc.srej) == (31, 128, 512, True)
    assert lc.adapt[0] == "4fsk76800" and lc.adapt[-1] == "g3ruh9600"
    assert c.engine.channel.config.mode is None                    # each part of an over at its own mode


def test_the_window_is_one_over_per_mode_unless_set_by_hand(ctl):
    # 0.3.0-0.3.1 used k 63 everywhere: two 10 s overs at 9600.
    assert [fastmodes.default_window(m) for m in fastmodes.MODES[1:]] == [31, 62, 63, 63, 63, 63, 63]
    c = ctl(channel__frequency_hz=432_650_000, fast=FastRadioSettings(mode="g3ruh9600"))
    assert c.engine.link_config.k == 31 and c.engine.channel.config.max_frames == 32
    c2 = ctl(channel__frequency_hz=432_650_000, fast=FastRadioSettings(mode="g3ruh9600", window=40,
                                                                       window_auto=False))
    assert c2.engine.link_config.k == 40


def test_a_settings_file_from_0_3_1_gets_the_window_per_mode():
    from shack_packet_app import settings as settings_mod
    s = settings_mod.Settings()
    s.fast.mode = "g3ruh9600"
    old = settings_mod.to_dict(s)
    del old["fast"]["window_auto"]                  # what 0.3.1 saved: window 63, no window_auto
    assert old["fast"]["window"] == 63
    back = settings_mod.from_dict(old)
    assert back.fast.window_auto and back.fast.window_for() == 31


def test_afsk_on_a_pluto_and_every_other_radio_keep_the_engine_defaults(ctl):
    from shack_packet.channel import ChannelConfig
    from shack_packet.datalink import LinkConfig
    plain = ctl(fast=FastRadioSettings(mode="afsk1200", window=63))
    assert plain.engine.channel.config == ChannelConfig() and plain.engine.link_config == LinkConfig()
    sim = ctl("simulator", fast=FastRadioSettings(mode="g3ruh9600"))
    assert sim.engine.channel.config.mode is None and sim.engine.link_config == LinkConfig()


def test_a_wide_mode_outside_the_band_plan_will_not_arm_and_inside_it_will(ctl):
    c = ctl(channel__frequency_hz=1_298_500_000, sdr_radio__filter_confirmed=True,
            fast=FastRadioSettings(mode="gfsk76800"))
    ok, why, detail = c.arm(1)
    assert not ok and why == "fast-out-of-plan" and "1298.5000" in detail
    c2 = ctl(channel__frequency_hz=1_299_525_000, sdr_radio__filter_confirmed=True,
             fast=FastRadioSettings(mode="gfsk76800"))
    assert c2.arm(1) == (True, "", "")


def test_a_9600_baud_channel_fits_a_20_khz_channel_and_153600_does_not_fit_23_cm(ctl):
    c = ctl(channel__frequency_hz=1_298_500_000, sdr_radio__filter_confirmed=True,
            fast=FastRadioSettings(mode="g3ruh9600"))
    assert c.arm(1)[0]
    c2 = ctl(channel__frequency_hz=1_299_525_000, sdr_radio__filter_confirmed=True,
             fast=FastRadioSettings(mode="gfsk153600"))
    assert c2.arm(1)[1] == "fast-out-of-plan"


# -- what the window shows ---------------------------------------------------------------------

def test_the_plan_check_names_the_lo_and_image_and_whether_they_leave_the_segment():
    at = fastmodes.plan(FastRadioSettings(mode="4fsk76800").for_frequency(fastmodes.SUGGESTED_HZ),
                        fastmodes.SUGGESTED_HZ)
    assert at.fits and at.wide and at.ifo_hz == 200_000.0
    assert (at.lo_hz, at.image_hz) == (1_299_325_000, 1_299_125_000) and not (at.lo_out or at.image_out)
    low = fastmodes.plan(FastRadioSettings(mode="4fsk76800").for_frequency(1_299_225_000), 1_299_225_000)
    assert low.fits and low.image_out and not low.lo_out           # the image at 1298.825
    narrow = fastmodes.plan(FastRadioSettings(mode="g3ruh9600").for_frequency(1_298_500_000), 1_298_500_000)
    assert narrow.fits and not narrow.wide and narrow.segment is None
    wide13 = fastmodes.plan(FastRadioSettings(mode="gfsk153600").for_frequency(2_312_000_000), 2_312_000_000)
    assert wide13.fits and not wide13.lo_out


def test_ptt_outputs_follow_the_firmware_and_an_unchecked_pluto_gets_none():
    assert fastmodes.ptt_outputs("adi") == ("none", "gpo0", "gpo1", "gpo2", "gpo3")
    assert fastmodes.ptt_outputs("tezuka") == ("none", "firmware") == fastmodes.ptt_outputs("plutodvb2")
    assert fastmodes.ptt_outputs("") == ("none",) == fastmodes.ptt_outputs("unknown")
