# Copyright (c) 2024 Codenames, cyku
#
# https://github.com/papamobi/Codenames/
#
# AFK detection plugin for team based modes
# detects afk players even if they did not move across rounds and deaths
# and puts them to spec
#
#
# Uses:
# - qlx_afk_warning_seconds "10"
# - qlx_afk_detection_seconds "20"


import minqlx
import time

from enum import Enum


VERSION = "v0.16"

VAR_WARNING = "qlx_afk_warning_seconds"
VAR_DETECTION = "qlx_afk_detection_seconds"

# Interval for the thread to update positions. Default = 0.33
interval = 0.33


class AfkState(Enum):
    NONE = 0
    WARN = 1
    AFK = 2


class Player:
    def __init__(self, player):
        # minqlx player
        self.__player = player
        self.reset()

    def id(self):
        return self.__player.id

    def steam_id(self):
        return self.__player.steam_id

    def name(self):
        return self.__player.name

    def afk_time(self):
        return self.__afkTime

    def position(self):
        return self.__player.position()

    def spectate(self):
        self.__player.put("spectator")

    def reset(self):
        self.__savedPos = self.position()
        self.__afkTime = 0
        self.__moveCount = 0

    def has_moved(self):
        return self.__savedPos != self.position()

    def inc_afk_time(self, amount):
        self.__afkTime += amount
        self.__moveCount = 0

    def move_detected(self):
        self.__moveCount += 1
        self.__savedPos = self.position()

        # player is not afk if he kept moving for three checks
        # in case he changed position because of round end or freeze
        if self.__moveCount >= 3:
            self.reset()

    def check_afk(self, logger, warning, detection):
        # If position stayed the same, add the time difference and check for thresholds
        if not self.has_moved():
            secs = self.afk_time()

            self.inc_afk_time(interval)

            if self.afk_time() > 10:
                logger.info(
                    "Player {} [{}]({}) afk for {} seconds".format(
                        self.name(), self.id(), self.steam_id(), int(self.afk_time())
                    )
                )

            if self.afk_time() >= warning and secs < warning:
                return AfkState.WARN
            elif self.afk_time() >= detection and secs < detection:
                return AfkState.AFK
        else:
            self.move_detected()

        return AfkState.NONE


# Start plugin
class afk(minqlx.Plugin):

    def __init__(self):
        # Set required cvars once. DONT EDIT THEM HERE BUT IN SERVER.CFG
        self.set_cvar_once(VAR_WARNING, "10")
        self.set_cvar_once(VAR_DETECTION, "20")

        self.afk_logger = minqlx.get_logger("afk")

        # Get required cvars
        self.warning = int(self.get_cvar(VAR_WARNING))
        self.detection = int(self.get_cvar(VAR_DETECTION))

        # steamid : Player
        self.positions = {}

        # keep looking for AFK players
        self.running = False

        self.add_hook("round_start", self.handle_round_start)
        self.add_hook("team_switch", self.handle_player_switch)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("unload", self.handle_unload)
        self.add_hook("new_game", self.handle_new_game)

        # in case we reload during game
        if self.game and self.game.state == "in_progress":
            self.afk_thread()

    def handle_new_game(self, *args, **kwargs):
        self.positions = {}

    def handle_unload(self, plugin):
        if plugin == self.__class__.__name__:
            self.running = False

    def handle_round_start(self, round_number):
        if round_number == 1:
            self.afk_thread()

    def handle_player_switch(self, player, old, new):
        if new == "spectator":
            if player.steam_id in self.positions:
                del self.positions[player.steam_id]

        if new in ["red", "blue"]:
            self.positions[player.steam_id] = Player(player)

    def handle_player_disconnect(self, player):
        if player.steam_id in self.positions:
            del self.positions[player.steam_id]

    @minqlx.thread
    def afk_thread(self):
        if self.running:
            self.afk_logger.warn("Starting afk_thread while it's already running...")
            return

        self.running = True

        self.afk_logger.info("Starting afk thread")

        while self.running and self.game and self.game.state == "in_progress":
            for p in self.minqlx_players():
                pid = p.steam_id

                if not p.is_alive or p.is_frozen:
                    continue

                player = self.positions.setdefault(pid, Player(p))

                afk_state = player.check_afk(
                    self.afk_logger, self.warning, self.detection
                )

                if afk_state == AfkState.WARN:
                    self.warn_player(player)
                elif afk_state == AfkState.AFK:
                    self.spectate_player(player)

            time.sleep(interval)

        self.afk_logger.info("Stopping afk thread")
        self.running = False

    def minqlx_players(self):
        teams = self.teams()
        return teams["red"] + teams["blue"]

    @minqlx.next_frame
    def warn_player(self, player):
        message = "You have been inactive for {} seconds...".format(self.warning)
        minqlx.send_server_command(player.id(), 'cp "\n\n\n{}"'.format(message))

    @minqlx.next_frame
    def spectate_player(self, player):
        self.msg(
            "^1{} ^1has been inactive for {} seconds! Commencing punishment!".format(
                player.name(), int(player.afk_time())
            )
        )
        player.spectate()
        del self.positions[player.steam_id()]
