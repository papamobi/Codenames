# Created by Thomas Jones on 16/05/2016 - thomas@tomtecsolutions.com
# autorestart.py, a plugin for minqlx to automatically restart a server at a certain time if no-one's connected.
# This plugin is released to everyone, for any purpose. It comes with no warranty, no guarantee it works, it's released AS IS.
# You can modify everything, except for lines 1-4 and the !tomtec_versions code. They're there to indicate I whacked this together originally. Please make it better :D

"""
    Times are specified in 24-hour time syntax, 13:00 for 1:00pm, 23:00 for 11:00pm, 02:00 for 2:00am etc.

    v2.0: Removed the external `schedule` library dependency and the unmanaged
    background thread that called into minqlx's C layer from outside the main
    game thread (a likely source of intermittent heap corruption). All time
    checks now happen on the main game thread via the `frame` event,
    rate-limited to once per 30 seconds.
"""

import minqlx
import time
import datetime


class autorestart(minqlx.Plugin):
    def __init__(self):
        super().__init__()

        self.set_cvar_once("qlx_autoRestartTime", "21:59")

        self.add_command("tomtec_versions", self.cmd_showversion)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("frame", self.handle_frame)

        self.plugin_version = "2.0"
        self.restart = False
        self.last_check_monotonic = 0.0

        # If the server starts after today's target time, mark today as already
        # triggered so we wait for tomorrow's target rather than firing now.
        self.last_triggered_date = self._today_if_past_target()

    def _parse_target(self):
        """Returns (hour, minute) tuple from the cvar, or None if invalid."""
        try:
            h, m = self.get_cvar("qlx_autoRestartTime").split(":")
            return int(h), int(m)
        except (ValueError, AttributeError):
            return None

    def _today_if_past_target(self):
        """Returns today's date if current time is past target, else None."""
        target = self._parse_target()
        if target is None:
            return None
        h, m = target
        now = datetime.datetime.now()
        target_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return now.date() if now >= target_today else None

    def handle_frame(self):
        # Rate-limit: at most one check per 30 seconds.
        now_mono = time.monotonic()
        if now_mono - self.last_check_monotonic < 30:
            return
        self.last_check_monotonic = now_mono

        if self.restart:
            return  # already flagged, nothing more to do here

        target = self._parse_target()
        if target is None:
            return
        h, m = target

        now_dt = datetime.datetime.now()
        today = now_dt.date()
        target_today = now_dt.replace(hour=h, minute=m, second=0, microsecond=0)

        # Trigger once per day when current time crosses the configured target.
        if now_dt >= target_today and self.last_triggered_date != today:
            self.last_triggered_date = today
            self.restart = True
            if len(self.players()) < 1:
                minqlx.console_command("quit")

    @minqlx.delay(5)
    def handle_player_disconnect(self, *args, **kwargs):
        if self.restart and len(self.players()) <= 1:
            minqlx.console_command("quit")

    def cmd_showversion(self, player, msg, channel):
        channel.reply(
            "^4autorestart.py^7 - version {}, created by Thomas Jones on 16/05/2016, modified by mobi.".format(self.plugin_version)
        )
