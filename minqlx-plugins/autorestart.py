# Created by Thomas Jones on 16/05/2016 - thomas@tomtecsolutions.com
# autorestart.py, a plugin for minqlx to automatically restart a server at a certain time if no-one's connected.
# This plugin is released to everyone, for any purpose. It comes with no warranty, no guarantee it works, it's released AS IS.
# You can modify everything, except for lines 1-4 and the !tomtec_versions code. They're there to indicate I whacked this together originally. Please make it better :D

"""
    Times are specified in 24-hour time syntax, 13:00 for 1:00pm, 23:00 for 11:00pm, 02:00 for 2:00am etc.

    v2.0: Removed external `schedule` lib dependency and the unmanaged
    background thread that called into minqlx's C layer off the main game
    thread (likely source of intermittent heap corruption). All time checks
    now run on the main game thread via the `frame` event, rate-limited to
    once per 30 seconds.

    v2.1: Fixed `<= 1` -> `== 0` in handle_player_disconnect so the server
    can't quit while one player is still connected. Added a player-count
    recheck inside handle_frame as a safety net for the case where
    self.restart is set but no further disconnect event fires.

    Note: `== 0` is the right check here only because handle_player_disconnect
    is decorated with `@minqlx.delay(5)` (added in v2.0) -- by the time it
    fires, the engine has removed the leaver from `self.players()`.

    v2.2: Added optional weekly scheduling via a new cvar
    qlx_autoRestartDayOfWeek. Existing setups don't need to do anything
    -- if this cvar is left unset or empty (the default), the plugin
    continues to restart every day, exactly as in v2.1. To switch to
    weekly restarts, set qlx_autoRestartDayOfWeek to a single weekday
    ("0"=Mon, "1"=Tue, "2"=Wed, "3"=Thu, "4"=Fri, "5"=Sat, "6"=Sun);
    the restart will then only fire on that day.
"""

import minqlx
import time
import datetime


class autorestart(minqlx.Plugin):
    def __init__(self):
        super().__init__()

        self.set_cvar_once("qlx_autoRestartTime", "21:59")
        # Empty string = fire every day (backward-compatible default).
        # "0".."6" = fire only on that weekday (0=Monday, 6=Sunday).
        self.set_cvar_once("qlx_autoRestartDayOfWeek", "")

        self.add_command("tomtec_versions", self.cmd_showversion)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("frame", self.handle_frame)

        self.plugin_version = "2.2"
        self.restart = False
        self.last_check_monotonic = 0.0

        # If the server starts after today's target time (and today is a
        # valid trigger day), mark today as already triggered so we wait
        # for the next scheduled occurrence rather than firing immediately.
        self.last_triggered_date = self._today_if_past_target()

    def _parse_target(self):
        """Returns (hour, minute) tuple from the cvar, or None if invalid."""
        try:
            h, m = self.get_cvar("qlx_autoRestartTime").split(":")
            return int(h), int(m)
        except (ValueError, AttributeError):
            return None

    def _parse_target_weekday(self):
        """Returns the target weekday as an int 0-6 (0=Monday, 6=Sunday),
        or None if the cvar is empty/unset (meaning: fire every day)."""
        raw = self.get_cvar("qlx_autoRestartDayOfWeek")
        if not raw or not raw.strip():
            return None  # Every day
        try:
            day = int(raw.strip())
            if 0 <= day <= 6:
                return day
            return None  # Out of range -- treat as unset rather than error
        except (ValueError, TypeError):
            return None  # Garbage -- treat as unset rather than error

    def _is_target_weekday(self, date):
        """Returns True if `date` is a day we should fire on (i.e. matches the
        configured weekday, OR no weekday filter is set)."""
        target_weekday = self._parse_target_weekday()
        if target_weekday is None:
            return True  # No filter -- every day is a target day
        return date.weekday() == target_weekday

    def _today_if_past_target(self):
        """Returns today's date if (current time is past target time AND today
        is a valid trigger day), else None. The "valid trigger day" check
        prevents an immediate firing if the server starts past the target
        time on the configured weekday -- we want it to wait until next
        week's occurrence instead."""
        target = self._parse_target()
        if target is None:
            return None
        h, m = target
        now = datetime.datetime.now()
        target_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= target_today and self._is_target_weekday(now.date()):
            return now.date()
        return None

    def handle_frame(self):
        # Rate-limit: at most one check per 30 seconds.
        now_mono = time.monotonic()
        if now_mono - self.last_check_monotonic < 30:
            return
        self.last_check_monotonic = now_mono

        if self.restart:
            # Already flagged for restart. No further player_disconnect event
            # may ever fire (server was already empty, or the last player just
            # sits connected) -- so recheck here as a safety net.
            if len(self.players()) == 0:
                minqlx.console_command("quit")
            return

        target = self._parse_target()
        if target is None:
            return
        h, m = target

        now_dt = datetime.datetime.now()
        today = now_dt.date()

        # Weekday filter: if a target weekday is set and today isn't it, skip.
        # This is checked before the time comparison so we don't waste work
        # on the other 6 days of the week.
        if not self._is_target_weekday(today):
            return

        target_today = now_dt.replace(hour=h, minute=m, second=0, microsecond=0)

        # Trigger once per qualifying day when current time crosses target.
        if now_dt >= target_today and self.last_triggered_date != today:
            self.last_triggered_date = today
            self.restart = True
            if len(self.players()) == 0:
                minqlx.console_command("quit")

    @minqlx.delay(5)
    def handle_player_disconnect(self, *args, **kwargs):
        if self.restart and len(self.players()) == 0:
            minqlx.console_command("quit")

    def cmd_showversion(self, player, msg, channel):
        channel.reply(
            "^4autorestart.py^7 - version {}, created by Thomas Jones on 16/05/2016, modified by mobi.".format(self.plugin_version)
        )
