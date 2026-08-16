# minqlx - A Quake Live server administrator bot.
# Copyright (C) 2015 Mino <mino@minomino.org>

# This file is part of minqlx.

# minqlx is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# minqlx is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with minqlx. If not, see <http://www.gnu.org/licenses/>.

"""
Configuration cvars (set these in server.cfg):

    qlx_balanceUseLocal            (default "1")
        "1" = use cached local ratings first and only fetch from the API
        when missing; "0" = always go to the API. Caching reduces load on
        the rating server and is fine for typical use.

    qlx_balanceUrl                 (default "qlstats.net")
        The hostname of the rating-server API. Used together with
        qlx_balanceApi to build the URL the plugin fetches ratings from.

    qlx_balanceApi                 (default "elo")
        The rating endpoint to use on the rating server. Common values:
        "elo", "elo_b" (B-rating variant). Combined with qlx_balanceUrl
        as http://<url>/<api>/ for HTTP fetches.

    qlx_balanceMinimumSuggestionDiff   (default "25")
        Minimum team-ELO difference below which the plugin will not
        suggest a swap at all. Below this threshold, !teams just reports
        the ratings without a swap recommendation. Acts as a noise floor
        so trivial imbalances don't generate suggestions.

    qlx_balanceForceSwapDiff       (no default -- opt-in)
        ELO difference at or above which a swap is performed
        automatically, without players needing to !agree or call /callvote
        do. UNSET / empty / "0" = feature disabled (default behavior:
        regular suggestion flow). Set this in server.cfg to e.g. "125" to
        auto-swap whenever teams differ by 125+ ELO.

        On round-based gametypes (AD, CA, FT) the auto-swap queues for
        the next round_end so it happens between rounds. On continuous
        gametypes (TDM, CTF, DOM) it executes immediately, since those
        gametypes have no mid-game round boundary to wait for.

    qlx_balanceForceSwapDiff is intentionally not registered with a
    default value so a server admin who hasn't deliberately enabled the
    feature never sees automatic swaps. Add the line to server.cfg to
    opt in.
"""

import minqlx
import requests
import itertools
import threading
import random
import time
import os
import re

RATING_KEY = "minqlx:players:{0}:ratings:{1}" # 0 == steam_id, 1 == short gametype.
MAX_ATTEMPTS = 3
CACHE_EXPIRE = 60*10 # 10 minutes TTL.
DEFAULT_QLSTATS_RATING = 900
DEFAULT_SLIPGATE_RATING = 1000
UNTRACKED_RATING = 9999
TEAMS_CALL_COOLDOWN = 5 # can't call !teams more frequently than once in 5 seconds
SUPPORTED_GAMETYPES = ("ad", "ca", "ctf", "dom", "ft", "tdm")
# Externally supported game types. Used by !getrating for game types the API works with.
EXT_SUPPORTED_GAMETYPES = ("ad", "ca", "ctf", "dom", "ft", "tdm", "duel", "ffa")
# Round-based gametypes have a natural "next round" boundary that switches can
# be deferred to. Continuous gametypes (ctf, dom, tdm) do not -- their only
# "round end" is the end of the game itself, so deferring a player switch on
# those gametypes effectively means "wait until the game finishes." cmd_agree
# uses this to decide whether to defer or execute immediately.
ROUND_BASED_GAMETYPES = ("ad", "ca", "ft")

# Built-in QL factories that need force-reset of qlx_balanceApi to "elo".
#
# Why this exists: factory cvars persist across factory switches. Built-in
# id Software factories cannot be edited and never set qlx_balanceApi
# themselves. So if a previous custom factory set the cvar to "elo_b" and
# the engine then switches to one of these built-ins, the cvar stays as
# "elo_b" and balance.py would fetch ratings from the wrong API.
#
# handle_new_game force-resets qlx_balanceApi based on the current built-in
# factory. Custom factories are left untouched -- their own .factories file
# sets qlx_balanceApi explicitly and the existing cache_cvars() flow handles
# them correctly.
DEFAULT_ELO_FACTORIES = frozenset((
    "ad", "ffa", "ca", "ft", "tdm", "duel", "ctf",
))
# Built-in factories that use the B-rating API (Instagib variants).
DEFAULT_ELO_B_FACTORIES = frozenset((
    "ictf", "ift", "iffa",
))

# The newer version of this plugin incorporates elements from mybalance.py directly to avoid having to maintain
# multiple rating APIs in different plugins. For detailed documentation on mybalance.py, see the original file.

BOUNDARIES = []

# If this is True, a message will be printed on the screen of the person who should spec when teams are uneven
CP = True
CP_MESS = "\n\n\nTeams are uneven. You will be forced to spec."

# Default action to be performed when teams are uneven:
# Options: spec, slay, ignore
DEFAULT_LAST_ACTION = "spec"

# Database Keys
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
PLAYER_KEY = "minqlx:players:{}"
LAST_KEY = "minqlx:last"
COMPLETED_KEY = PLAYER_KEY + ":games_completed"
LEFT_KEY = PLAYER_KEY + ":games_left"

# Yep
EXCEPTIONS_FILE = "exceptions.txt"

GRACE_PERIOD_TIME = 120 # allows to reconnect and rejoin within 2 mins and keep match join time

class balance(minqlx.Plugin):
    def __init__(self):
        self.add_hook("round_countdown", self.handle_round_countdown)
        self.add_hook("round_start", self.handle_round_start)
        self.add_hook("round_end", self.handle_round_end)
        self.add_hook("vote_ended", self.handle_vote_ended)
        self.add_hook("player_connect", self.handle_player_connect)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("new_game", self.handle_new_game)
        self.add_hook("team_switch", self.handle_team_switch)
        self.add_command(("setrating", "setelo"), self.cmd_setrating, 3, usage="<id> <rating>")
        self.add_command(("getrating", "getelo", "elo"), self.cmd_getrating, usage="<id> [gametype]")
        self.add_command(("remrating", "remelo"), self.cmd_remrating, 3, usage="<id>")
        self.add_command("rating", self.cmd_rating, 3, usage="<rating_name> (qlstats or slipgate)")
        self.add_command("balance", self.cmd_balance, 1)
        self.add_command(("teams", "teens"), self.cmd_teams)
        self.add_command("do", self.cmd_do, 1)
        self.add_command(("agree", "a"), self.cmd_agree, client_cmd_perm=0)
        self.add_command(("ratings", "elos", "selo"), self.cmd_ratings)

        self.ratings_lock = threading.RLock()
        self.teams_lock = threading.RLock()
        # Keys: steam_id - Items: {"ffa": {"elo": 123, "games": 321, "local": False}, ...}
        self.ratings = {}
        # Keys: request_id - Items: (players, callback, channel, args)
        self.requests = {}
        self.request_counter = itertools.count()
        self.suggested_pair = None
        self.suggested_agree = [False, False]
        self.in_countdown = False
        # Tracked across threads via self.teams_lock at the read/write site
        # in cmd_teams. Initialized to 0 so the first call always passes the
        # cooldown check (any current timestamp - 0 >> TEAMS_CALL_COOLDOWN).
        self.last_teams_call_timestamp = 0

        self.set_cvar_once("qlx_balanceUseLocal", "1")
        self.set_cvar_once("qlx_balanceUrl", "qlstats.net")
        self.set_cvar_once("qlx_slipgateBalanceUrl", "slipgate.gg")
        self.set_cvar_once("qlx_balanceAuto", "1")
        self.set_cvar_once("qlx_balanceMinimumSuggestionDiff", "25")
        # qlx_balanceForceSwapDiff is intentionally NOT registered with a
        # default here. The auto-swap feature is opt-in: leave this cvar
        # unset in server.cfg and no auto-swap will ever trigger (the
        # comparison site treats missing/None/<=0 identically as disabled).
        # To enable, set qlx_balanceForceSwapDiff "<elo_diff>" in server.cfg
        # (e.g. "125" to auto-swap when teams differ by >= 125 elo).
        self.set_cvar_once("qlx_balanceApi", "elo")
        self.set_cvar_once("qlx_slipgateBalanceApi", "api/v1/ratings/bulk")
        # Using qlx_ratingSet is better since it lets you work with different rating APIs but qlx_balanceApi is
        # maintained for compatibility with other plugins.
        self.set_cvar_once("qlx_ratingSet", "A")
        # Use Slipgate rankings instead of QLstats. Needs a qlx_slipgateApiKey param set somewhere. Change through
        # !rating slipgate to properly discard rating caches.
        self.set_cvar_once("qlx_useSlipgateRatings", "0")

        # mybalance vars
        self.set_cvar_once("qlx_elo_limit_min", "0")
        self.set_cvar_once("qlx_elo_limit_max", "1600")
        self.set_cvar_once("qlx_elo_games_needed", "10")
        self.set_cvar_once("qlx_elo_kick", "1")
        self.set_cvar_once("qlx_elo_block_connecters", "0")
        self.set_cvar_once("qlx_mybalance_warmup_seconds", "300")
        self.set_cvar_once("qlx_mybalance_warmup_interval", "60")
        self.set_cvar_once("qlx_mybalance_autoshuffle", "0")
        self.set_cvar_once("qlx_mybalance_perm_allowed", "2")
        self.set_cvar_once("qlx_mybalance_exclude", "0")
        self.set_cvar_once("qlx_mybalance_uneven_time", "10")
        self.set_cvar_once("qlx_mybalance_elo_bump_regs", "[]")
        self.set_cvar_once("qlx_elo_close_enough", "20")

        self.ELO_MIN = int(self.get_cvar("qlx_elo_limit_min"))
        self.ELO_MAX = int(self.get_cvar("qlx_elo_limit_max"))
        self.GAMES_NEEDED = int(self.get_cvar("qlx_elo_games_needed"))
        try:
            global BOUNDARIES
            BOUNDARIES = eval(self.get_cvar("qlx_mybalance_elo_bump_regs"))
            assert type(BOUNDARIES) is list
            for _e, _b in BOUNDARIES:
                assert type(_e) is int
                assert type(_b) is int
        except:
            BOUNDARIES = []

        self.prevent = False
        self.last_action = DEFAULT_LAST_ACTION
        self.jointimes = {}
        # used to calculate the last joiner
        self.join_match_times = {}
        # grace periods that allow players to reconnect; steam_id : [last_disconnect_time, last_join_time]
        # this is not gonna persist through server restarts but it's fine
        self.grace_periods = {}

        self.game_active = self.game.state == "in_progress"
        # Vars for CTF / TDM
        self.ctfplayer = False
        self.checking_balance = False

        # steam_id : [name, elo]
        self.kicked = {}

        # collection of [steam_id, name, thread]
        self.kickthreads = []
        # Keep broadcasting warmup reminders?
        self.warmup_reminders = True
        self.exceptions = []
        self.cmd_help_load_exceptions(None, None, None)

        self.add_command("prevent", self.cmd_prevent_last, 2)
        self.add_command("last", self.cmd_last_action, 2, usage="[SLAY|SPEC|IGNORE]")
        self.add_command(("load_exceptions", "reload_exceptions", "list_exceptions", "listexceptions", "exceptions"), self.cmd_help_load_exceptions, 3)
        self.add_command(("add_exception", "elo_exception"), self.cmd_add_exception, 3, usage="<name>|<steam_id> <name>")
        self.add_command(("del_exception", "rem_exception"), self.cmd_del_exception, 3, usage="<name>|<id>|<steam_id>")
        self.add_command("elokicked", self.cmd_elo_kicked)
        self.add_command("remkicked", self.cmd_rem_kicked, 2, usage="<id>")
        self.add_command(("nokick", "dontkick"), self.cmd_nokick, 2, usage="[<name>]")
        self.add_command(("limit", "limits", "elolimit"), self.cmd_elo_limit)
        self.add_command(("elomin", "minelo"), self.cmd_min_elo, 3, usage="[ELO]")
        self.add_command(("elomax", "maxelo"), self.cmd_max_elo, 3, usage="[ELO]")
        self.add_command(("rankings", "elotype"), self.cmd_elo_type, usage="[A|B]")
        self.add_command("reminders", self.cmd_warmup_reminders, 2, usage="[ON|OFF]")
        self.add_hook("game_start", self.handle_game_start)
        self.add_hook("game_end", self.handle_game_end)
        self.add_hook("map", self.handle_map)
        self.add_hook("game_countdown", self.handle_game_countdown)
        self.add_hook("vote_called", self.handle_vote_called)

        self.handle_new_game() # start counting reminders if we are in warmup

        if self.game_active and self.game.type_short in ['ctf', 'tdm']:
            self.balance_before_start(self.game.type_short, True)

    def cache_cvars(self):
        # Store some cvar values that are used in non-game threads
        self.use_local = self.get_cvar("qlx_balanceUseLocal", bool)
        self.use_slipgate_ratings = self.get_cvar("qlx_useSlipgateRatings", bool)
        self.qlstats_api_url = "http://{}/{}/".format(self.get_cvar("qlx_balanceUrl"), self.get_cvar("qlx_balanceApi"))
        self.slipgate_api_url = "http://{}/{}/".format(self.get_cvar("qlx_slipgateBalanceUrl"),
          self.get_cvar("qlx_slipgateBalanceApi"))

    def handle_round_countdown(self, round_number):
        # No swap is done here -- this server doesn't have round countdowns
        # enabled, so the swap happens in handle_round_end instead. If
        # countdowns are ever re-enabled, this is where the swap should go
        # (using @minqlx.next_frame to avoid clobbering the countdown sound
        # and text, per an old comment about that behavior).
        def red_min_blue():
            t = self.teams()
            return len(t['red']) - len(t['blue'])

        self.in_countdown = True

        # Grab the teams
        teams = self.teams()
        player_count = len(teams["red"] + teams["blue"])

        # If it is the last player, don't do this and let the game finish normally
        if player_count == 1:
            return

        # If there is a difference in teams of more than 1
        diff = red_min_blue()
        to, fr = ['blue', 'red'] if diff > 0 else ['red','blue']
        n = int(abs(diff) / 2)
        if abs(diff) >= 1:
            last = self.algo_get_last()
            if not last:
                self.msg("^7No last person could be predicted in round countdown from teams:\nRed:{}\nBlue:{}".format(teams['red'], teams['blue']))

            elif diff % 2 == 0:
                n = last.name if n == 1 else "{} players".format(n)
                self.msg("^6Uneven teams detected!^7 At round start i'll move {} to {}".format(n, to))
            else:
                m = 'lowest player' if n == 1 else '{} lowest players'.format(n)
                m = " and move the {} to {}".format(m, to) if n else ''
                self.msg("^6Uneven teams detected!^7 Server will auto spec {}{}.".format(last.name, m))

        self.balance_before_start(round_number)

    def handle_round_start(self, *args, **kwargs):
        self.in_countdown = False

    # do the swap here - seems like the round start event is delayed by ~4 seconds; doing this in round end
    # should have the expected behaviour of immediately swapping people after round starts
    def handle_round_end(self, round_number):
        players = self.teams()
        if all(self.suggested_agree) and len(players["red"]) == len(players["blue"]):
            # don't wait because we don't have the countdown
            self.execute_suggestion()
        self.balance_before_start(round_number, True)
        self.prevent = False

    def _near_endgame(self):
        """True if either team is within 2 rounds of winning the match.
        Used to suppress force-swap decisions on round-based gametypes --
        if the match is close to ending, moving a player between teams
        disrupts endgame play. The 2-round window covers both:
          - The current round being potentially last (roundlimit-1 already)
          - The round after this one becoming potentially last
        Only meaningful for gametypes that use roundlimit (AD, CA, FT);
        returns False when roundlimit isn't set or is 0 (e.g. timelimit-
        only games)."""
        if self.game is None:
            return False
        try:
            roundlimit = self.game.roundlimit
            if roundlimit <= 0:
                return False
            return max(self.game.red_score, self.game.blue_score) >= roundlimit - 2
        except (AttributeError, TypeError):
            return False

    def handle_vote_ended(self, votes, vote, args, passed):
        if passed == True and vote == "shuffle" and self.get_cvar("qlx_balanceAuto", bool):
            gt = self.game.type_short
            if gt not in SUPPORTED_GAMETYPES:
                return

            @minqlx.delay(4)
            def f():
                players = self.teams()
                if len(players["red"] + players["blue"]) % 2 != 0:
                    self.msg("Teams were ^6NOT^7 balanced due to the total number of players being an odd number.")
                    return

                players = dict([(p.steam_id, gt) for p in players["red"] + players["blue"]])
                self.add_request(players, self.callback_balance, minqlx.CHAT_CHANNEL)
            f()

    # load player elo on connect immediately
    def handle_player_connect(self, player):
        self.fetch_ratings_for_players({ player.steam_id: self.game.type_short })

        sid = player.steam_id
        gt = self.game.type_short

        # If they joined very very very recently (like a short block from other plugins)
        if sid in self.jointimes:
            if (time.time() - self.jointimes[sid]) < 5: # dunno why 5s but should be enough
                return

        # Record their join times regardless
        self.jointimes[sid] = time.time()

        # If you are not an exception (or have high enough perm lvl);
        # you must be checked for elo limit
        if not (sid in self.exceptions or self.db.has_permission(player, self.get_cvar("qlx_mybalance_perm_allowed", int))):
            elo = self.ratings[sid][gt]["elo"]
            games = self.ratings[sid][gt]["games"]

            eval_elo = self.evaluate_elo_games(player, elo, games)

            # If it's too high, but it is close enough to the limit, start kickthread
            if eval_elo and eval_elo[0] == "high" and (eval_elo[1] - self.ELO_MAX) <= self.get_cvar("qlx_elo_close_enough",int):
                self.msg("^7Connecting player ({}^7)'s glicko ^6{}^7 is too high, but maybe close enough for a ^2!nokick ^7?".format(player.name, eval_elo[1]))
                self.kicked[player.steam_id] = [player.name, eval_elo[1]]
                self.help_start_kickthread(player, eval_elo[1], eval_elo[0])

            # If it's too low, but close enough to the limit, start kickthread
            elif eval_elo and eval_elo[0] == "low" and (self.ELO_MIN - eval_elo[1]) <= self.get_cvar("qlx_elo_close_enough",int):
                self.kicked[player.steam_id] = [player.name, eval_elo[1]]
                self.msg("^7Connecting player ({}^7)'s glicko ^6{}^7 is too low, but maybe close enough for a ^2!nokick ^7?".format(player.name, eval_elo[1]))
                self.help_start_kickthread(player, eval_elo[1], eval_elo[0])

            # If it's still not allowed, block connection
            elif eval_elo:
                return "^1Sorry, but your skill rating {} is too {}!".format(eval_elo[1], eval_elo[0])

    def handle_player_disconnect(self, player, reason):
        self.clean_player_data(player)

        new_kickthreads = []
        for kt in self.kickthreads:
            if kt[0] != player.steam_id:
                new_kickthreads.append(kt)
            else:
                try:
                    thread = kt[2]
                    thread.stop()
                except:
                    pass
        self.kickthreads = new_kickthreads

        if self.game_active and player.team != "spectator" and self.game.type_short in ["ctf", "tdm"]:
            self.balance_before_start(self.game.type_short, True)

    def handle_new_game(self):
        # Built-in QL factories can't be edited and never set qlx_balanceApi.
        # If a previous custom factory set it (e.g. to "elo_b") and the engine
        # then switches to a built-in, the cvar would persist and balance.py
        # would fetch from the wrong API. Force-reset based on the current
        # built-in factory's expected rating type. Custom factories are left
        # untouched -- their .factories file sets qlx_balanceApi explicitly
        # and the existing cache_cvars() flow handles them correctly.
        # Must run BEFORE cache_cvars() so api_urls reflects the corrected value.
        if self.game.factory in DEFAULT_ELO_FACTORIES:
            self.set_cvar("qlx_balanceApi", "elo")
            self.set_cvar("qlx_ratingSet", "A")
        elif self.game.factory in DEFAULT_ELO_B_FACTORIES:
            self.set_cvar("qlx_balanceApi", "elo_b")
            self.set_cvar("qlx_ratingSet", "B")

        self.cache_cvars()

        # reset ratings cache on start and load elos for all players
        if self.game.state == "warmup":
            self.refetch_player_elo()

        # mybalance logic
        if self.game is None: return
        if self.game.state in ["in_progress", "countdown"]: return
        self.game_active = False
        self.checking_balance = False
        self.check_warmup(time.time(), self.game.map)

    # check balance for even teams after switches
    def handle_team_switch(self, player, old, new):
        @minqlx.next_frame
        def balance():
            # only check during the actual game
            if self.game.state != "in_progress":
                return
            teams = self.teams()
            if len(teams["red"]) != len(teams["blue"]):
                return
            gt = self.game.type_short
            if new in ['red', 'blue', 'spectator']:
                players = dict([(p.steam_id, gt) for p in teams["red"] + teams["blue"]])
                self.add_request(players, self.callback_teams, minqlx.CHAT_CHANNEL)

        balance()

        # mybalance logic
        p_id = player.steam_id
        if new in ['red', 'blue', 'free']:
            if p_id in self.kicked:
                player.put("spectator")
                if self.get_cvar("qlx_elo_kick") == "1":
                    kickmsg = "so you'll be kicked shortly..."
                else:
                    kickmsg = "but you are free to keep watching."
                player.tell("^6You do not meet the skill rating requirements to play on this server, {}".format(kickmsg))
                player.center_print("^6You do not meet the skill rating requirements to play on this server, {}".format(kickmsg))
                return

            # this keeps track of whoever joined last to queue if needed and applies the grace period
            last_disconnect_time = self.grace_periods[p_id][0] if p_id in self.grace_periods else 0
            # only set when someone joins from spec, if it's a swap (and there is an existing match time) do nothing
            if p_id not in self.join_match_times:
                if time.time() - last_disconnect_time > GRACE_PERIOD_TIME:
                    self.join_match_times[p_id] = time.time()
                    if last_disconnect_time != 0:
                        minqlx.console_print("Player {} has last disconnect time {}: outside of grace period".format(p_id, last_disconnect_time))
                    minqlx.console_print("Player {} has joined the match at {}".format(p_id, self.join_match_times[p_id]))
                else:
                    self.join_match_times[p_id] = self.grace_periods[p_id][1]
                    minqlx.console_print("Player {} has last disconnect time {}: inside of grace period, set join time {}".format(p_id, last_disconnect_time, self.grace_periods[p_id][1]))
        else:
            if p_id in self.join_match_times:
                del self.join_match_times[p_id]

        # If the game mode has no rounds, and a player joins, set a timer
        if self.game_active and self.game.type_short in ["ctf", "tdm"]:
            teams = self.teams()
            # If someone joins, check if teams are even
            if new in ['red', 'blue']:
                if len(teams['red']) != len(teams['blue']):
                    self.msg("^7If teams will remain uneven for ^6{}^7 seconds, {} will be put to spec.".format(self.get_cvar("qlx_mybalance_uneven_time", int), player.name))
                    self.ctfplayer = player
                    # position() read here - main thread.
                    self.evaluate_team_balance(player, list(player.position()))
                else:
                    # If teams are even now, it's all good.
                    self.ctfplayer = None
            else:
                # If someone goes to spec, check later if they are still uneven
                self.ctfplayer = None # stop watching anyone
                if len(teams['red']) != len(teams['blue']):
                    self.msg("^7Uneven teams detected! If teams are still uneven in {} seconds, I will spec someone.".format(self.get_cvar("qlx_mybalance_uneven_time")))
                    if not self.checking_balance:
                        self.checking_balance = True
                        self.evaluate_team_balance()

    @minqlx.thread
    def clean_player_data(self, player):
        for p in self.players().copy():
            if p.steam_id == player.steam_id and p.id != player.id:
                # there is a second client with same steam id
                return

        with self.ratings_lock:
            if player.steam_id in self.ratings:
                del self.ratings[player.steam_id]

        if player.steam_id in self.jointimes:
            del self.jointimes[player.steam_id]

        # persist the join match time if it exists - in case the player
        # unexpectedly disconnects
        if player.steam_id in self.join_match_times:
            self.grace_periods[player.steam_id] = [time.time(), self.join_match_times[player.steam_id]]
            del self.join_match_times[player.steam_id]


    @minqlx.thread
    def fetch_ratings(self, players, request_id):
        if not players:
            return

        # We don't want to modify the actual dict, so we use a copy.
        players_copy = players.copy()

        # Get local ratings if present in DB.
        if self.use_local:
            for steam_id in players_copy:
                gt = players_copy[steam_id]
                key = RATING_KEY.format(steam_id, gt)
                if key in self.db:
                    with self.ratings_lock:
                        if steam_id in self.ratings:
                            self.ratings[steam_id][gt] = {"games": -1, "elo": int(self.db[key]), "local": True, "time": -1}
                        else:
                            self.ratings[steam_id] = {gt: {"games": -1, "elo": int(self.db[key]), "local": True, "time": -1}}
                    del players_copy[steam_id]

            if not players_copy:
                self.handle_ratings_fetched(request_id, requests.codes.ok)
                return

        state = {"status": 0}

        self.fetch_ratings_for_players(players_copy, state)

        self.handle_ratings_fetched(request_id, state["status"])

    def fetch_ratings_for_players(self, players, state=None):
        if state is None:
            state = {"status": 0}
        attempts = 0

        while attempts < MAX_ATTEMPTS:
            attempts += 1

            if self.use_slipgate_ratings:
                self.fetch_slipgate_ratings(players, state)
            else:
                self.fetch_qlstats_ratings(players, state)

            if state["status"] != requests.codes.ok:
                continue

            break

    @minqlx.thread
    def fetch_qlstats_ratings(self, players, state):
        url = self.qlstats_api_url + "+".join([str(sid) for sid in players])
        res = requests.get(url, headers={"X-QuakeLive-Map": self.game.map}, timeout=5.0)
        if res.status_code != requests.codes.ok:
            state["status"] = res.status_code
            return

        js = res.json()
        if "players" not in js:
            state["status"] = -1
            return

        # Fill our ratings dict with the ratings we just got.
        for p in js["players"]:
            sid = int(p["steamid"])
            del p["steamid"]
            t = time.time()

            with self.ratings_lock:
                if sid not in self.ratings:
                    self.ratings[sid] = {}

                for gt in p:
                    p[gt]["time"] = t
                    p[gt]["local"] = False
                    self.ratings[sid][gt] = p[gt]
                    if self.ratings[sid][gt]["elo"] == 0 and self.ratings[sid][gt]["games"] == 0:
                        self.ratings[sid][gt]["elo"] = DEFAULT_QLSTATS_RATING

                    if sid in players and gt == players[sid]:
                        # The API gave us the game type we wanted, so we remove it.
                        del players[sid]

                # Fill the rest of the game types the API didn't return but supports.
                for gt in SUPPORTED_GAMETYPES:
                    if gt not in self.ratings[sid]:
                        self.ratings[sid][gt] = {"games": -1, "elo": DEFAULT_QLSTATS_RATING, "local": False, "time": time.time()}

        # If the API didn't return all the players, we set them to the default rating.
        for sid in players:
            with self.ratings_lock:
                if sid not in self.ratings:
                    self.ratings[sid] = {}
                self.ratings[sid][players[sid]] = {"games": -1, "elo": DEFAULT_QLSTATS_RATING, "local": False, "time": time.time()}

        # Setting ratings for untracked players.
        if "untracked" in js:
            untracked_sids = list(map( lambda sid: int(sid), js["untracked"]))

            for gt in SUPPORTED_GAMETYPES:
                for sid in untracked_sids:
                  with self.ratings_lock:
                      if sid not in self.ratings:
                          self.ratings[sid] = {}
                      self.ratings[sid][gt] = {"games": -1, "elo": UNTRACKED_RATING, "local": False, "time": time.time()}

        state["status"] = res.status_code

    @minqlx.thread
    def fetch_slipgate_ratings(self, players, state):
        # Slipgate only returns ratings for a specific gametype, so we ignore the ones passed in players and instead
        # only fetch ratings for the current game type
        gt = self.game.type_short
        rating_set = self.get_cvar("qlx_ratingSet")
        request = {
            "game_type": gt,
            "rating_set": rating_set,
            "steam_ids": list(map(lambda x: str(x), players.keys()))
        }
        res = requests.post(
            self.slipgate_api_url,
            json=request,
            headers={"Authorization": "Bearer " + self.get_cvar("qlx_slipgateApiKey")},
            timeout=5.0
        )
        if res.status_code != requests.codes.ok:
            state["status"] = res.status_code
            return

        js = res.json()
        if "players" not in js:
            state["status"] = -1
            return

        for p in js["players"]:
            sid = int(p["steam_id"])
            t = time.time()

            with self.ratings_lock:
                if sid not in self.ratings:
                    self.ratings[sid] = {}
                elo = p["display"]
                games = p["games"]
                if elo is None or elo == 0 or games is None or games == 0:
                    elo = DEFAULT_SLIPGATE_RATING

                self.ratings[sid][gt] = { "elo": elo, "games": games, "local": False, "time": t }

        state["status"] = requests.codes.ok

    @minqlx.next_frame
    def handle_ratings_fetched(self, request_id, status_code):
        players, callback, channel, args = self.requests[request_id]
        del self.requests[request_id]
        if status_code != requests.codes.ok:
            # TODO: Put a couple of known errors here for more detailed feedback.
            channel.reply("ERROR {}: Failed to fetch ratings.".format(status_code))
        else:
            callback(players, channel, *args)

    def add_request(self, players, callback, channel, *args):
        req = next(self.request_counter)
        self.requests[req] = players.copy(), callback, channel, args

        # Only start a new thread if we need to make an API request.
        if self.remove_cached(players):
            self.fetch_ratings(players, req)
        else:
            # All players were cached, so we tell it to go ahead and call the callbacks.
            self.handle_ratings_fetched(req, requests.codes.ok)

    def remove_cached(self, players):
        with self.ratings_lock:
            for sid in players.copy():
                gt = players[sid]
                if sid in self.ratings and gt in self.ratings[sid]:
                    t = self.ratings[sid][gt]["time"]
                    if t == -1 or time.time() < t + CACHE_EXPIRE:
                        del players[sid]

        return players

    def cmd_getrating(self, player, msg, channel):
        if len(msg) == 1:
            sid = player.steam_id
        else:
            try:
                sid = int(msg[1])
                target_player = None
                if 0 <= sid < 64:
                    target_player = self.player(sid)
                    sid = target_player.steam_id
            except ValueError:
                player.tell("Invalid ID. Use either a client ID or a SteamID64.")
                return minqlx.RET_STOP_ALL
            except minqlx.NonexistentPlayerError:
                player.tell("Invalid client ID. Use either a client ID or a SteamID64.")
                return minqlx.RET_STOP_ALL

        if len(msg) > 2:
            if msg[2].lower() in EXT_SUPPORTED_GAMETYPES:
                gt = msg[2].lower()
            else:
                player.tell("Invalid gametype. Supported gametypes: {}"
                    .format(", ".join(EXT_SUPPORTED_GAMETYPES)))
                return minqlx.RET_STOP_ALL
        else:
            gt = self.game.type_short
            if gt not in EXT_SUPPORTED_GAMETYPES:
                player.tell("This game mode is not supported by the balance plugin.")
                return minqlx.RET_STOP_ALL

        self.add_request({sid: gt}, self.callback_getrating, channel, gt)

    def cmd_rating(self, player, msg, channel):
        if len(msg) < 2 or msg[1] not in ['qlstats', 'slipgate']:
            return minqlx.RET_USAGE

        if msg[1] == 'qlstats':
            self.set_cvar("qlx_useSlipgateRatings", "0")
            self.use_slipgate_ratings = False
        else:
            self.set_cvar("qlx_useSlipgateRatings", "1")
            self.use_slipgate_ratings = True
        self.msg('The server is now using {} ratings.'.format(msg[1]))
        self.refetch_player_elo()


    def callback_getrating(self, players, channel, gametype):
        sid = next(iter(players))
        player = self.player(sid)
        if player:
            name = player.name
        else:
            name = sid

        elo = self.ratings[sid][gametype]["elo"]
        games = self.ratings[sid][gametype]["games"]
        m = "{} ".format(name)
        elos = []
        rating_provider = "slipgate.gg" if self.use_slipgate_ratings else "qlstats.net"
        key = RATING_KEY.format(sid, self.game.type_short)
        if key in self.db:
            dbelo = int(self.db[key])
            elos.append("^7local {} glicko: ^6{}".format(gametype.upper(), dbelo))
        #if elo and games:
        b = " ^3B^7" if self.get_cvar('qlx_balanceApi') == "elo_b" else ""
        elos.append("^7{} {}{} glicko: ^6{} ({} games)".format(rating_provider, gametype.upper(), b, elo, games))

        # msg_main_thread - reached from the fetch() worker.
        if elos: self.msg_main_thread("^6{}".format(m) + " ^7, ".join(elos) + "^7.")

    def cmd_setrating(self, player, msg, channel):
        if len(msg) < 3:
            return minqlx.RET_USAGE

        try:
            sid = int(msg[1])
            target_player = None
            if 0 <= sid < 64:
                target_player = self.player(sid)
                sid = target_player.steam_id
        except ValueError:
            player.tell("Invalid ID. Use either a client ID or a SteamID64.")
            return minqlx.RET_STOP_ALL
        except minqlx.NonexistentPlayerError:
            player.tell("Invalid client ID. Use either a client ID or a SteamID64.")
            return minqlx.RET_STOP_ALL

        try:
            rating = int(msg[2])
        except ValueError:
            player.tell("Invalid rating.")
            return minqlx.RET_STOP_ALL

        if target_player:
            name = target_player.name
        else:
            name = sid

        gt = self.game.type_short
        self.db[RATING_KEY.format(sid, gt)] = rating

        # If we have the player cached, set the rating.
        with self.ratings_lock:
            if sid in self.ratings and gt in self.ratings[sid]:
                self.ratings[sid][gt]["elo"] = rating
                self.ratings[sid][gt]["local"] = True
                self.ratings[sid][gt]["time"] = -1

        channel.reply("{}'s {} rating has been set to ^6{}^7.".format(name, gt.upper(), rating))

    def cmd_remrating(self, player, msg, channel):
        if len(msg) < 2:
            return minqlx.RET_USAGE

        try:
            sid = int(msg[1])
            target_player = None
            if 0 <= sid < 64:
                target_player = self.player(sid)
                sid = target_player.steam_id
        except ValueError:
            player.tell("Invalid ID. Use either a client ID or a SteamID64.")
            return minqlx.RET_STOP_ALL
        except minqlx.NonexistentPlayerError:
            player.tell("Invalid client ID. Use either a client ID or a SteamID64.")
            return minqlx.RET_STOP_ALL

        if target_player:
            name = target_player.name
        else:
            name = sid

        gt = self.game.type_short
        del self.db[RATING_KEY.format(sid, gt)]

        # If we have the player cached, remove the game type.
        with self.ratings_lock:
            if sid in self.ratings and gt in self.ratings[sid]:
                del self.ratings[sid][gt]

        channel.reply("{}'s locally set {} rating has been deleted.".format(name, gt.upper()))

    def cmd_balance(self, player, msg, channel):
        gt = self.game.type_short
        if gt not in SUPPORTED_GAMETYPES:
            player.tell("This game mode is not supported by the balance plugin.")
            return minqlx.RET_STOP_ALL

        teams = self.teams()
        if len(teams["red"] + teams["blue"]) % 2 != 0:
            player.tell("The total number of players should be an even number.")
            return minqlx.RET_STOP_ALL

        players = dict([(p.steam_id, gt) for p in teams["red"] + teams["blue"]])
        self.add_request(players, self.callback_balance, minqlx.CHAT_CHANNEL)

    def callback_balance(self, players, channel):
        # We check if people joined while we were requesting ratings and get them if someone did.
        teams = self.teams()
        current = teams["red"] + teams["blue"]
        gt = self.game.type_short

        for p in current:
            if p.steam_id not in players:
                d = dict([(p.steam_id, gt) for p in current])
                self.add_request(d, self.callback_balance, channel)
                return

        # Start out by evening out the number of players on each team.
        diff = len(teams["red"]) - len(teams["blue"])
        if abs(diff) > 1:
            if diff > 0:
                for i in range(diff - 1):
                    p = teams["red"].pop()
                    p.put("blue")
                    teams["blue"].append(p)
            elif diff < 0:
                for i in range(abs(diff) - 1):
                    p = teams["blue"].pop()
                    p.put("red")
                    teams["red"].append(p)

        # Start shuffling by looping through our suggestion function until
        # there are no more switches that can be done to improve teams.
        switch = self.suggest_switch(teams, gt)
        if switch:
            while switch:
                p1 = switch[0][0]
                p2 = switch[0][1]
                self.switch(p1, p2)
                teams["blue"].append(p1)
                teams["red"].append(p2)
                teams["blue"].remove(p2)
                teams["red"].remove(p1)
                switch = self.suggest_switch(teams, gt)
            avg_red = self.team_average(teams["red"], gt)
            avg_blue = self.team_average(teams["blue"], gt)
            diff_rounded = abs(round(avg_red) - round(avg_blue)) # Round individual averages.
            if round(avg_red) > round(avg_blue):
                self.msg("^1{} ^7vs ^4{}^7 - DIFFERENCE: ^1{}"
                    .format(round(avg_red), round(avg_blue), diff_rounded))
            elif round(avg_red) < round(avg_blue):
                self.msg("^1{} ^7vs ^4{}^7 - DIFFERENCE: ^4{}"
                    .format(round(avg_red), round(avg_blue), diff_rounded))
            else:
                self.msg("^1{} ^7vs ^4{}^7 - Holy shit!"
                    .format(round(avg_red), round(avg_blue)))
        else:
            channel.reply("Teams are good! Nothing to balance.")
        return True

    def cmd_teams(self, player, msg, channel):
        gt = self.game.type_short
        if gt not in SUPPORTED_GAMETYPES:
            player.tell("This game mode is not supported by the balance plugin.")
            return minqlx.RET_STOP_ALL

        teams = self.teams()
        if len(teams["red"]) != len(teams["blue"]):
            player.tell("Both teams should have the same number of players.")
            return minqlx.RET_STOP_ALL

        teams = dict([(p.steam_id, gt) for p in teams["red"] + teams["blue"]])
        self.add_request(teams, self.callback_teams, channel)

    # Runs on the main thread. callback_teams reads self.teams(), self.game
    # state, and issues channel.reply() / execute_suggestion() -- all of
    # which touch engine state and need to run on the game tick, not on the
    # fetch_ratings @minqlx.thread worker that would otherwise invoke this
    # callback directly.
    @minqlx.next_frame
    def callback_teams(self, players, channel):
        # prevent teams call from being called too fast; this also fixes the double-call when people join
        with self.teams_lock:
            t = time.time()
            if (t - self.last_teams_call_timestamp < TEAMS_CALL_COOLDOWN):
                return
            self.last_teams_call_timestamp = t

        # We check if people joined while we were requesting ratings and get them if someone did.
        teams = self.teams()
        current = teams["red"] + teams["blue"]
        gt = self.game.type_short

        for p in current:
            if p.steam_id not in players:
                d = dict([(p.steam_id, gt) for p in current])
                self.add_request(d, self.callback_teams, channel)
                return

        avg_red = self.team_average(teams["red"], gt)
        avg_blue = self.team_average(teams["blue"], gt)
        switch = self.suggest_switch(teams, gt)
        diff_rounded = abs(round(avg_red) - round(avg_blue)) # Round individual averages.
        if round(avg_red) > round(avg_blue):
            channel.reply("^1{} ^7vs ^4{}^7 - DIFFERENCE: ^1{}"
                .format(round(avg_red), round(avg_blue), diff_rounded))
        elif round(avg_red) < round(avg_blue):
            channel.reply("^1{} ^7vs ^4{}^7 - DIFFERENCE: ^4{}"
                .format(round(avg_red), round(avg_blue), diff_rounded))
        else:
            channel.reply("^1{} ^7vs ^4{}^7 - Holy shit!"
                .format(round(avg_red), round(avg_blue)))

        minimum_suggestion_diff = self.get_cvar("qlx_balanceMinimumSuggestionDiff", float)
        force_swap_diff = self.get_cvar("qlx_balanceForceSwapDiff", float)
        if switch and switch[1] >= minimum_suggestion_diff:
            gt = self.game.type_short
            # Auto-swap is disabled (never triggers) when force_swap_diff is
            # missing, empty, zero, or negative. All three of these give the
            # same behavior:
            #   - cvar absent from server.cfg entirely
            #   - cvar set to "" (empty string)
            #   - cvar set to "0"
            # Without explicit handling, an empty/missing cvar makes
            # get_cvar(..., float) return None and `None > 0` would raise a
            # TypeError in Python 3.
            force_swap = (force_swap_diff is not None
                          and force_swap_diff > 0
                          and diff_rounded >= force_swap_diff)
            # Don't force-swap when the match is close to ending. On
            # round-based gametypes, if either team is within 2 rounds of
            # winning, forcibly moving a player between teams disrupts the
            # endgame -- e.g. a high-scoring player gets shifted to the
            # other side just as the match is about to end. The regular
            # !agree suggestion path still runs below, so players can opt
            # in if both sides genuinely want the swap.
            if force_swap and gt in ROUND_BASED_GAMETYPES and self._near_endgame():
                force_swap = False
            # On continuous gametypes (TDM, CTF, DOM), there is no
            # meaningful "end of the round" mid-game -- the queued swap
            # would only execute when the entire game ends. So when a
            # force-swap is triggered on a continuous gametype, execute it
            # immediately and say so. On round-based gametypes (AD, CA, FT),
            # keep the existing "queue for end of round" behavior.
            if force_swap and gt not in ROUND_BASED_GAMETYPES:
                message = "Players ^6{}^7 and ^6{}^7 will be swapped now because teams are greatly unbalanced!"
            elif force_swap:
                message = "Players ^6{}^7 and ^6{}^7 will be swapped at the end of the round because teams are greatly unbalanced!"
            else:
                message = "SUGGESTION: switch ^6{}^7 with ^6{}^7. Mentioned players can type !a to agree."
            channel.reply(message.format(switch[0][0].clean_name, switch[0][1].clean_name))
            if not self.suggested_pair or self.suggested_pair[0] != switch[0][0] or self.suggested_pair[1] != switch[0][1]:
                self.suggested_pair = (switch[0][0], switch[0][1])
                self.suggested_agree = [True, True] if force_swap else [False, False]
                # If we just decided to force-swap on a continuous gametype,
                # don't wait for handle_round_end -- it won't fire until the
                # whole game ends. Execute now.
                if force_swap and gt not in ROUND_BASED_GAMETYPES:
                    self.execute_suggestion()
        else:
            i = random.randint(0, 99)
            if not i:
                channel.reply("Teens look ^6good!")
            else:
                channel.reply("Teams look good!")
            self.suggested_pair = None
            self.suggested_agree = [False, False]

        return True

    def cmd_do(self, player, msg, channel):
        """Forces a suggested switch to be done."""
        if self.suggested_pair:
            self.execute_suggestion()

    def cmd_agree(self, player, msg, channel):
        """After the bot suggests a switch, players in question can use this to agree to the switch."""
        if self.suggested_pair and not all(self.suggested_agree):
            p1, p2 = self.suggested_pair

            if p1 == player:
                self.suggested_agree[0] = True
            elif p2 == player:
                self.suggested_agree[1] = True

            if all(self.suggested_agree):
                # On round-based gametypes (AD, CA, FT), defer to the next
                # round start if the game's in progress -- the existing
                # handle_round_end hook will catch and execute it.
                # On continuous gametypes (TDM, CTF, DOM), there's no
                # meaningful "next round" boundary mid-game -- the only
                # round_end event fires when the whole game ends, which
                # would leave players waiting up to a full timelimit/
                # fraglimit before the switch happens. Execute immediately
                # in that case.
                gt = self.game.type_short
                if self.game.state == "in_progress" and not self.in_countdown \
                        and gt in ROUND_BASED_GAMETYPES:
                    self.msg("Both players agreed. The switch will be executed at the start of next round.")
                    return

                # Otherwise, switch right away.
                self.execute_suggestion()

    def cmd_ratings(self, player, msg, channel):
        gt = self.game.type_short
        if gt not in EXT_SUPPORTED_GAMETYPES:
            player.tell("This game mode is not supported by the balance plugin.")
            return minqlx.RET_STOP_ALL

        players = dict([(p.steam_id, gt) for p in self.players()])
        self.add_request(players, self.callback_ratings, channel)

    def callback_ratings(self, players, channel):
        # We check if people joined while we were requesting ratings and get them if someone did.
        teams = self.teams()
        current = self.players()
        gt = self.game.type_short

        for p in current:
            if p.steam_id not in players:
                d = dict([(p.steam_id, gt) for p in current])
                self.add_request(d, self.callback_ratings, channel)
                return

        if teams["free"]:
            free_sorted = sorted(teams["free"], key=lambda x: self.ratings[x.steam_id][gt]["elo"], reverse=True)
            free = ", ".join(["{}: ^6{}^7".format(p.clean_name, self.ratings[p.steam_id][gt]["elo"]) for p in free_sorted])
            channel.reply(free)
        if teams["red"]:
            red_sorted = sorted(teams["red"], key=lambda x: self.ratings[x.steam_id][gt]["elo"], reverse=True)
            red = ", ".join(["{}: ^1{}^7".format(p.clean_name, self.ratings[p.steam_id][gt]["elo"]) for p in red_sorted])
            channel.reply(red)
        if teams["blue"]:
            blue_sorted = sorted(teams["blue"], key=lambda x: self.ratings[x.steam_id][gt]["elo"], reverse=True)
            blue = ", ".join(["{}: ^4{}^7".format(p.clean_name, self.ratings[p.steam_id][gt]["elo"]) for p in blue_sorted])
            channel.reply(blue)
        if teams["spectator"]:
            spec_sorted = sorted(teams["spectator"], key=lambda x: self.ratings[x.steam_id][gt]["elo"], reverse=True)
            spec = ", ".join(["{}: {}".format(p.clean_name, self.ratings[p.steam_id][gt]["elo"]) for p in spec_sorted])
            channel.reply(spec)

    def suggest_switch(self, teams, gametype):
        """Suggest a switch based on average team ratings."""
        avg_red = self.team_average(teams["red"], gametype)
        avg_blue = self.team_average(teams["blue"], gametype)
        cur_diff = abs(avg_red - avg_blue)
        min_diff = 999999
        best_pair = None

        for red_p in teams["red"]:
            for blue_p in teams["blue"]:
                r = teams["red"].copy()
                b = teams["blue"].copy()
                b.append(red_p)
                r.remove(red_p)
                r.append(blue_p)
                b.remove(blue_p)
                avg_red = self.team_average(r, gametype)
                avg_blue = self.team_average(b, gametype)
                diff = abs(avg_red - avg_blue)
                if diff < min_diff:
                    min_diff = diff
                    best_pair = (red_p, blue_p)

        if min_diff < cur_diff:
            return (best_pair, cur_diff - min_diff)
        else:
            return None

    def team_average(self, team, gametype):
        """Calculates the average rating of a team."""
        avg = 0
        if team:
            for p in team:
                avg += self.ratings[p.steam_id][gametype]["elo"]
            avg /= len(team)

        return avg

    def execute_suggestion(self):
        p1, p2 = self.suggested_pair
        try:
            p1.update()
            p2.update()
        except minqlx.NonexistentPlayerError:
            return

        if p1.team != "spectator" and p2.team != "spectator":
            self.switch(self.suggested_pair[0], self.suggested_pair[1])

        self.suggested_pair = None
        self.suggested_agree = [False, False]

    def refetch_player_elo(self):
        gt = self.game.type_short
        with self.ratings_lock:
            self.ratings = {}
            players = dict([(p.steam_id, gt) for p in self.players()])
            self.add_request(players, self.callback_fetch_player_elo, minqlx.CHAT_CHANNEL)

    # helper functions for the queue plugin
    # empty callback on purpose - used to fetch the player elo through sending it to add_request without printing
    # anything in chat
    def callback_fetch_player_elo(self, players, channel):
        pass

    def get_player_elo(self, player, attempt=0):
        try:
            return self.ratings[player.steam_id][self.game.type_short]["elo"]
        except:
            # normally this shouldn't happen at all but if for whatever reason we couldn't fetch the elo we need to
            # re-fetch it and return it after some wait again
            if attempt > 3:
                raise Exception("couldn't fetch rating for player {}".format(player.steam_id))

            minqlx.console_print("Couldn't fetch rating for player {} when adding to teams".format(player.steam_id))
            self.add_request({ player.steam_id: self.game.type_short }, self.callback_fetch_player_elo, minqlx.CHAT_CHANNEL)
            time.sleep(0.5)
            return self.get_player_elo(player, attempt + 1)

    def get_team_averages(self, attempt=0):
        gt = self.game.type_short
        try:
            teams = self.teams()
            avg_red = self.team_average(teams["red"], gt)
            avg_blue = self.team_average(teams["blue"], gt)
            return { "red": avg_red, "blue": avg_blue }
        except:
            # normally this shouldn't happen at all but if for whatever reason we couldn't fetch the elo of some player
            # we need to re-fetch it and return it after some wait again
            if attempt > 3:
                raise Exception("couldn't calculate the average rating for teams")

            minqlx.console_print("Couldn't calculate the average rating for teams!")
            teams = self.teams()
            current = teams["red"] + teams["blue"]
            d = dict([(p.steam_id, gt) for p in current])
            self.add_request(d, self.callback_fetch_player_elo, minqlx.CHAT_CHANNEL)
            time.sleep(0.5)
            return self.get_team_averages(attempt + 1)

    # mybalance.py functions. Half of them can probably be nuked since they're never used on the server
    def handle_vote_called(self, caller, vote, args):
        # If it is not shuffle, whatever
        if vote.lower() != "shuffle": return

        # Shuffle won't be called in ffa or duel
        if self.game.type_short in ["ffa", "duel"]: return

        # If it is shuffle and we have autoshuffle enabled...
        if self.get_cvar("qlx_mybalance_autoshuffle", int):
            self.msg("^7Callvote shuffle ^1DENIED ^7since the server will ^3autoshuffle ^7on match start.")
            return minqlx.RET_STOP_ALL

    def cmd_elo_type(self, player, msg, channel):
        if len(msg) < 2:
            if self.get_cvar('qlx_balanceApi') == 'elo':
                channel.reply("^7The server is retrieving A (normal) rankings.")
            elif self.get_cvar('qlx_balanceApi') == 'elo_b':
                channel.reply("^7The server is retrieving with B (fun server) rankings.")
            return
        elif len(msg) < 3:
            # If the player doesnt have the permission to change it
            if not self.db.has_permission(player, 3):
                player.tell("^6You don't have the required permission (3) to perform this action. ")
                return minqlx.RET_STOP_ALL
            # If there was not a correct ranking type given
            rankings = {'a':'elo', 'b': 'elo_b'}
            if not (msg[1].lower() in rankings):
                return minqlx.RET_USAGE
            self.set_cvar('qlx_balanceApi', rankings[msg[1].lower()])
            self.set_cvar("qlx_ratingSet", msg[1].upper())
            channel.reply("^7Switched to ^6{}^7 rankings.".format(msg[1].upper()))
            return

    def cmd_min_elo(self, player, msg, channel):
        if len(msg) < 2:
            channel.reply("^7The minimum skill rating required for this server is: ^6{}^7.".format(self.ELO_MIN))
        elif len(msg) < 3:
            try:
                new_elo = int(msg[1])
                assert new_elo >= 0
            except:
                return minqlx.RET_USAGE
            self.ELO_MIN = new_elo
            channel.reply("^7The server minimum skill rating has been temporarily set to: ^6{}^7.".format(new_elo))
        else:
            return minqlx.RET_USAGE

    def cmd_max_elo(self, player, msg, channel):
        if len(msg) < 2:
            channel.reply("^7The maximum skill rating set for this server is: ^6{}^7.".format(self.ELO_MAX))
        elif len(msg) < 3:
            try:
                new_elo = int(msg[1])
                assert new_elo >= 0
            except:
                return minqlx.RET_USAGE
            self.ELO_MAX = new_elo
            channel.reply("^7The server maximum skill ratings has been temporarily set to: ^6{}^7.".format(new_elo))
        else:
            return minqlx.RET_USAGE

    def cmd_elo_limit(self, player, msg, channel):
        if int(self.get_cvar('qlx_elo_block_connecters')):
            close_enough = self.get_cvar("qlx_elo_close_enough", int)
            if close_enough:
                close_enough = " (and normal kick when ^6{}^7 from limit)".format(close_enough)
            else:
                close_enough = ""

            self.msg("^7Players will be blocked on connection outside limits: [^6{}^7-^6{}^7]{}.".format(self.ELO_MIN, self.ELO_MAX, close_enough))
        elif int(self.get_cvar('qlx_elo_kick')):
            self.msg("^7The server will kick players who fall outside [^6{}^7-^6{}^7].".format(self.ELO_MIN, self.ELO_MAX))
        else:
            self.msg("^7Players who don't have a skill rating between ^6{} ^7and ^6{} ^7are only allowed to spec.".format(self.ELO_MIN, self.ELO_MAX))

    def cmd_elo_kicked(self, player, msg, channel):
        self.show_elo_kicked(player, channel)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def show_elo_kicked(self, player, channel):
        @minqlx.next_frame
        def reply(m):
            if player: player.tell(m)
            else: channel.reply(m)

        n = 0
        if not self.kicked:
            reply("No players kicked since plugin (re)start.")
        # Snapshot: self.kicked is mutated from the fetch workers via callback().
        for sid, (name, elo) in list(self.kicked.items()):
            m = "^7{}: ^6{}^7 - ^6{}^7 - ^6{}".format(n, sid, elo, name)
            reply(m)
            n += 1
            time.sleep(0.2)

    def cmd_rem_kicked(self, player, msg, channel):
        if len(msg) < 2:
            return minqlx.RET_USAGE

        try:
            n = int(msg[1])
            assert 0 <= n < len(self.kicked)
        except:
            return minqlx.RET_USAGE

        counter = 0
        for sid in self.kicked.copy():
            if counter == n:
                name, elo = self.kicked[sid]
                del self.kicked[sid]
                break
            counter += 1

        channel.reply("^7Successfully removed ^6{}^7 (glicko {}) from the list.".format(name, elo))

    def cmd_nokick(self, player, msg, channel):
        def dontkick(kickthread):
            sid, nam, thr = kickthread
            thr.stop()
            if sid in self.kicked:
                del self.kicked[sid]

            new_kickthreats = []
            for kt in self.kickthreads:
                if kt[0] != sid:
                    new_kickthreats.append(kt)
                else:
                    kt[2].stop()
            self.kickthreads = new_kickthreats

            try:
                self.find_player(nam)[0].unmute()
            except:
                pass
            channel.reply("^7An admin has prevented {} from being kicked.".format(nam))

        if not self.kickthreads:
                player.tell("^6Psst^7: There are no people being kicked right now.")
                return minqlx.RET_STOP_ALL

        # if there is only one
        if len(self.kickthreads) == 1:
            dontkick(self.kickthreads[0])
            return

        # If no arguments given
        if len(msg) < 2:
            _names = map(lambda _el: _el[1], self.kickthreads)
            player.tell("^6Psst^7: did you mean ^6{}^7?".format("^7 or ^6".join(_names)))
            return minqlx.RET_STOP_ALL

        # If a search term, name, was given
        else:

            match_threads = [] # Collect matching names
            new_threads = [] # Collect non-matching threads

            for kt in self.kickthreads:
                if msg[1] in kt[1]:
                    match_threads.append(kt)
                else:
                    new_threads.append(kt)

            # If none of the threads had a name like that
            if not match_threads:
                player.tell("^6Psst^7: no players matched '^6{}^7'?".format(msg[1]))
                return minqlx.RET_STOP_ALL

            # If there was one result:
            if len(match_threads) == 1:
                self.kickthreads = new_threads
                dontkick(match_threads.pop())
                return

            # If multiple results were found:
            else:
                _names = map(lambda el: el[1], match_threads)
                player.tell("^6Psst^7: did you mean ^6{}^7?".format("^7 or ^6".join(_names)))
                return minqlx.RET_STOP_ALL

    def cmd_add_exception(self, player, msg, channel):
        try:
            # more than 2 arguments = NO NO
            if len(msg) > 3:
                return minqlx.RET_USAGE

            # less than 2 arguments is NOT OKAY if it was with a steam id
            if len(msg) < 3 and len(msg[1]) == 17:
                return minqlx.RET_USAGE

            # if steam_id given
            match_id = re.search('[0-9]{17}',  msg[1])
            if match_id and match_id.group() == msg[1]:
                add_sid = int(msg[1])
                add_nam = msg[2]

            # if name given
            else:
                target = self.find_by_name_or_id(player, msg[1])
                if not target:
                    return minqlx.RET_STOP_ALL
                add_sid = target.steam_id
                add_nam = msg[2] if len(msg) == 3 else target.name

            abs_file_path = os.path.join(self.get_cvar("fs_homepath"), EXCEPTIONS_FILE)
            with open (abs_file_path, "r") as file:
                for line in file:
                    if line.startswith("#"): continue
                    split = line.split()
                    sid = split.pop(0)
                    name = " ".join(split)
                    if int(sid) == add_sid:
                        player.tell("^6Psst: ^7This ID is already in the exception list under name ^6{}^7!".format(name))
                        return minqlx.RET_STOP_ALL

            with open (abs_file_path, "a") as file:
                file.write("{} {}\n".format(add_sid, add_nam))

            if not add_sid in self.exceptions:
                self.exceptions.append(add_sid)
            if add_sid in self.kicked:
                del self.kicked[add_sid]
            player.tell("^6Psst: ^2Succesfully ^7added ^6{} ^7to the exception list.".format(add_nam))
            return minqlx.RET_STOP_ALL

        except IOError as e:
            player.tell("^6Psst: IOError: ^7{}".format(e))

        except ValueError as e:
            return minqlx.RET_USAGE

        except Exception as e:
            player.tell("^6Psst: ^1Error: ^7{}".format(e))
        return minqlx.RET_STOP_ALL

    # Load a list of exceptions
    def cmd_help_load_exceptions(self, player, msg, channel):
        names = {}
        for p in self.players():
            names[p.steam_id] = p.name
        try:
            abs_file_path = os.path.join(self.get_cvar("fs_homepath"), EXCEPTIONS_FILE)
            with open (abs_file_path, "r") as file:
                excps = []
                n = 0
                if player: player.tell("^6Psst: ^7Glicko exceptions:\n")
                for line in file:
                    if line.startswith("#"): continue # comment lines
                    split = line.split()
                    sid = split.pop(0)
                    name = " ".join(split)
                    try:
                        excps.append(int(sid))
                        if player:
                            _name = names[int(sid)] if int(sid) in names else name.strip('\n\r\t')
                            player.tell("^6Psst: ^7{} ({})".format(sid, _name))

                        n += 1
                    except:
                        continue

                self.exceptions = excps
                if player:
                    player.tell("^6Open your console to see {} exceptions.".format(n))

        except IOError as e:
            try:
                abs_file_path = os.path.join(self.get_cvar("fs_homepath"), EXCEPTIONS_FILE)
                with open(abs_file_path,"a+") as f:
                    f.write("# This is a commented line because it starts with a '#'\n")
                    f.write("# Every exception on a newline, format: STEAMID NAME\n")
                    f.write("# The NAME is for a mental reference and may contain spaces\n")
                    f.write("{} (owner)\n".format(self.get_cvar('qlx_owner')))
                minqlx.CHAT_CHANNEL.reply("^6mybalance plugin^7: No exception list found, so I made one myself.")
            except:
                minqlx.CHAT_CHANNEL.reply("^1Error: ^7reading and creating exception list: {}".format(e))

        except Exception as e:
            minqlx.CHAT_CHANNEL.reply("^1Error: ^7reading exception list: {}".format(e))

    def cmd_del_exception(self, player, msg, channel):
        if len(msg) != 2:
            return minqlx.RET_USAGE
        try:
           # if steam_id given
            assert len(msg[1]) == 17
            add_sid = int(msg[1])
        except:
            # if name given
            target = self.find_by_name_or_id(player, msg[1])
            if not target:
                return minqlx.RET_STOP_ALL
            add_sid = target.steam_id

        try:
            f = open(os.path.join(self.get_cvar("fs_homepath"), EXCEPTIONS_FILE),"r+")
            d = f.readlines()
            f.seek(0)
            for i in d:
                if not i.startswith(str(add_sid)):
                    f.write(i)
                else:
                    player.tell("^6Player found and removed!")
                    if add_sid in self.exceptions:
                        self.exceptions.remove(add_sid)
                    msg = None
            f.truncate()
            f.close()
            if msg: player.tell("^6{} was not found in the exception list...".format(msg[1]))
        except:
            player.tell("^1Error^7: cannot open exception list.")
        return minqlx.RET_STOP_ALL

    def cmd_warmup_reminders(self, player, msg, channel):
        if len(msg) < 2 and self.warmup_reminders:
            s = self.get_cvar('qlx_mybalance_warmup_seconds')
            i = self.get_cvar('qlx_mybalance_warmup_interval')
            channel.reply("^7Warmup reminders will be displayed after {}s at {}s intervals.".format(s,i))
        elif len(msg) < 2:
            channel.reply("^7Warmup reminders have currently been turned ^6off^7.")
        elif len(msg) < 3 and msg[1].lower() in ['on', 'off']:
            if not self.warmup_reminders and (msg[1].lower() == 'on'):
                self.warmup_reminders = True
                self.check_warmup(time.time(), self.game.map)
            self.warmup_reminders = msg[1].lower() == 'on'
            channel.reply("^7Warmup reminders have been turned ^6{}^7.".format(msg[1].lower()))
        else:
            return minqlx.RET_USAGE

    @minqlx.thread
    def check_warmup(self, warmup, mapname):
        """Warmup ready-up reminders. Pacing only -- the work happens in check_warmup_step().

        Ticks at a fixed 1s. The step owns its own reminder schedule via state["next"], since
        anything it writes here would only be read back a tick late -- the step is deferred to
        the next frame, but the sleep below is evaluated microseconds after scheduling it.
        """
        state = {"done": False, "next": 0}
        while not state["done"]:
            self.check_warmup_step(warmup, mapname, state)
            time.sleep(1)

    @minqlx.next_frame
    def check_warmup_step(self, warmup, mapname, state):
        """One reminder tick. Runs on the main game thread; the predicates below read teams()."""
        if state["done"]:
            return

        if not (self.is_game_in_warmup() and self.game_with_map_loaded(mapname)
                and self.warmup_reminders and self.is_plugin_still_loaded()
                and self.is_warmup_seconds_enabled()
                and self.is_there_more_than_one_player_joined()):
            state["done"] = True
            return

        if time.time() - warmup < int(self.get_cvar('qlx_mybalance_warmup_seconds')):
            return
        if time.time() < state["next"]:
            return

        pgs = minqlx.Plugin._loaded_plugins
        if 'maps' in pgs and pgs['maps'].plugin_active:
            m = "^7Type ^2!s^7 to skip this map, or ^3ready up^7! "
            if self.get_cvar("qlx_mybalance_autoshuffle", int):
                m += "\nTeams will auto shuffle+balance!"
        else:
            m = "^7Time to ^3ready^7 up! "
            if self.get_cvar("qlx_mybalance_autoshuffle", int):
                m += "\nTeams will be auto shuffled and balanced!"
        self.msg(m.replace('\n', ''))
        self.center_print(m)
        state["next"] = time.time() + int(self.get_cvar('qlx_mybalance_warmup_interval'))

    # game/teams predicates -- main thread only, so check_warmup_step() is their only caller.
    def is_game_in_warmup(self) -> bool:
        if not self.game:
            return False

        return self.game.state == "warmup"

    def game_with_map_loaded(self, mapname) -> bool:
        if not self.game:
            return False

        return self.game.map == mapname

    def is_plugin_still_loaded(self) -> bool:
        return self.__class__.__name__ in minqlx.Plugin._loaded_plugins

    def is_warmup_seconds_enabled(self) -> bool:
        return self.get_cvar('qlx_mybalance_warmup_seconds', int) > -1

    def is_there_more_than_one_player_joined(self) -> bool:
        teams = self.teams()
        return len(teams["red"] + teams["blue"]) > 1

    @minqlx.delay(5)
    def handle_game_countdown(self):

        # cleanup outdated grace periods
        # do it here instead of game end to avoid unwanted race conditions
        current_time = time.time()
        for player_id in list(self.grace_periods.keys()):
            if current_time - self.grace_periods[player_id][0] > GRACE_PERIOD_TIME:
                del self.grace_periods[player_id]

        if self.game.type_short in ["ffa", "race"]: return

        # Make sure teams have even amount of players
        self.balance_before_start(0, True)

        # If autoshuffle is off, return
        if not int(self.get_cvar("qlx_mybalance_autoshuffle")): return

        # Do the autoshuffle
        #self.center_print("*autoshuffle*")
        self.msg("^4Autoshuffle...")
        self.shuffle()

        self.msg("^4Balancing on skill ratings...")
        teams = self.teams()
        players = dict([(p.steam_id, self.game.type_short) for p in teams["red"] + teams["blue"]])
        self.add_request(players, self.callback_balance, minqlx.CHAT_CHANNEL)

    def cmd_last_action(self, player, msg, channel):
        if len(msg) < 2:
            if self.last_action == 'slay' and 'anti_rape' in minqlx.Plugin._loaded_plugins:
                return channel.reply("^7The current action is ^6slay^7, but will ^6spec^7 since ^6anti_rape^7 is active.")
            return channel.reply("^7The current action when teams are uneven is: ^6{}^7.".format(self.last_action))

        if msg[1] not in ["slay", "spec", "ignore"]:
            return minqlx.RET_USAGE

        self.last_action = msg[1]

        if self.last_action == 'slay' and 'anti_rape' in minqlx.Plugin._loaded_plugins:
            return channel.reply("^7Action has been set to ^6slay^7, but will ^6spec^7 because ^6anti_rape^7 is loaded.")
        channel.reply("^7Action has been succesfully changed to: ^6{}^7.".format(msg[1]))

    # `pos` must be read by the caller on the main thread.
    @minqlx.thread
    def evaluate_team_balance(self, player=None, pos=None):
        @minqlx.next_frame
        def setpos(_p, _x, _y, _z):
            _p.position(x=_x, y=_y, z=_z)
            _p.velocity(reset=True)
        @minqlx.next_frame
        def cprint(_p, _m):
            if _p: _p.center_print(_m)

        if not self.game_active: return
        if player and not pos: return

        cvar = float(self.get_cvar("qlx_mybalance_uneven_time", int))
        while (cvar > 0):
            if not self.game_active: return
            # If there was a player to watch given, see if he is still extra
            if player:
                if self.ctfplayer:
                    if(self.ctfplayer.steam_id != player.steam_id):
                        return # different guy? return without doing anything
                else:
                    return # If there is a player but he is not tagged; return

                setpos(player, pos[0], pos[1], pos[2])
                if cvar.is_integer():
                    cprint(player, "^7Teams are uneven. ^6{}^7s until spec!".format(int(cvar)))

            time.sleep(0.1)
            cvar -= 0.1

        # Time's up; time to check the teams
        self.checking_balance = False
        self.balance_before_start(self.game.type_short, True)

    @minqlx.thread
    def balance_before_start(self, context, direct=False):
        """Even out the teams. Pacing only - engine work happens in balance_step()."""
        # Wait until round almost starts
        if not direct:
            countdown = int(self.get_cvar('g_roundWarmupDelay'))
            if self.get_cvar('g_freezeRoundDelay') and self.game and self.game.type_short == "ft":
                countdown = int(self.get_cvar('g_freezeRoundDelay'))
            time.sleep(max(countdown / 1000 - 0.8, 0))

        # `excluded` is re-applied to a fresh snapshot each step; the cap guarantees exit.
        state = {"excluded": [], "done": False}
        for _ in range(20):
            self.balance_step(context, state)
            time.sleep(0.2)
            if state["done"]:
                return
        self.logger.warning("balance_before_start({}) gave up after 20 steps".format(context))

    @minqlx.next_frame
    def balance_step(self, context, state):
        """One pass of the uneven-teams fix. Runs on the main game thread."""
        if state["done"]:
            return

        # Bail if the match has ended. game_end sets self.game_active to
        # False; without this, a round_end at end-of-match schedules a
        # balance_step on the next frame and can spec the remaining
        # player after a mid-scoreboard disconnect. Mirrors the pattern
        # already used by handle_team_switch, evaluate_team_balance, and
        # the freeze-then-spec path.
        # Explicitly check for countdown here: the countdown handler has a delay and does not set game_active itself
        # (only gets set after the match starts) but we need to be able to balance teams right before the match starts.
        if not self.game_active and self.game.state != "countdown":
            state["done"] = True
            return

        # If it is the last player, don't do this and let the game finish normally
        # OR if there is no match going on (countdown also counts as match)
        if self.game is None or self.game.state == "warmup":
            state["done"] = True
            return

        teams = self.teams()
        red, blue = teams["red"], teams["blue"]
        if len(red + blue) <= 1:
            state["done"] = True
            return

        gt = self.game.type_short

        # Double check to not do anything you don't have to
        if gt in ("ca", "ft") and self.game.roundlimit in [self.game.blue_score, self.game.red_score]:
            state["done"] = True
            return
        if gt == "tdm" and self.game.fraglimit in [self.game.blue_score, self.game.red_score]:
            state["done"] = True
            return
        if gt == "ctf" and self.game.capturelimit in [self.game.blue_score, self.game.red_score]:
            state["done"] = True
            return

        # Exclude the prevented/ignored player from this frame's snapshot, not a stale one.
        red = [p for p in red if p.steam_id not in state["excluded"]]
        blue = [p for p in blue if p.steam_id not in state["excluded"]]

        diff = len(red) - len(blue)
        if abs(diff) < 1:
            state["done"] = True
            return

        last = self.algo_get_last({"red": red, "blue": blue})
        if not last:
            self.logger.warning("Trying to balance before round {} start. Red({}) - Blue({}) players"
                                .format(context, len(red), len(blue)))
            state["done"] = True
            return

        if diff % 2 == 0:  # one team has an even amount of people more than the other
            to, fr = ['blue', 'red'] if diff > 0 else ['red', 'blue']
            last.put(to)
            self.msg("^6Uneven teams action^7: Moved {} from {} to {}".format(last.name, fr, to))
            return

        # There is an odd number of players, so one will have to spec.
        if self.prevent or self.last_action == "ignore":
            if last.steam_id in state["excluded"]:
                # Already excluded and the teams are still uneven; nothing more to do.
                state["done"] = True
                return
            state["excluded"].append(last.steam_id)
            self.msg("^6Uneven teams^7: {} will not be moved to spec".format(last.name))
            return

        if self.last_action == "slay" and 'anti_rape' not in minqlx.Plugin._loaded_plugins:
            last.health = 0
            self.msg("{} ^7has been ^1slain ^7to even the teams!".format(last.name))
            return

        last.put("spectator")
        if self.try_add_to_queue(last):
            self.msg("^6Uneven teams action^7: {} was moved to queue to even teams!".format(last.name))
        else:
            self.msg("^6Uneven teams action^7: {} was moved to spec to even teams!".format(last.name))
        if self.last_action == "slay":
            self.logger.info("Not slayed because anti_rape plugin is loaded.")

    # Main thread only - queue.addToQueue reads the engine's client array.
    def try_add_to_queue(self, p):
        """Tries to add the spec'd player to the front of the queue."""
        if 'queue' in minqlx.Plugin._loaded_plugins:
            minqlx.Plugin._loaded_plugins['queue'].addToQueue(p, 0)
            return True
        self.logger.info("Unable to add player {} to queue - no queue plugin installed".format(p.clean_name))
        return False

    def handle_game_start(self, data):
        # game_start can arrive with the game already torn down for a map change.
        if self.game is None:
            return

        self.game_active = True

        # There are no rounds?? Check it yourself then, pronto!
        if self.game.type_short in ["ctf", "tdm"]:
            self.balance_before_start("game start ({})".format(self.game.type_short), True)

    def handle_game_end(self, data):
        self.game_active = False

    def handle_map(self, mapname, factory):
        self.game_active = False

    def cmd_prevent_last(self, player, msg, channel):
        """A command to prevent the last player on a team being kicked if
        teams are magically balanced """
        self.prevent = True
        channel.reply("^7You will prevent the last player to be acted on at the start of next round.")

    def find_time(self, player):
        if not (player.steam_id in self.join_match_times):
            self.join_match_times[player.steam_id] = time.time()
        return self.join_match_times[player.steam_id]

    # Workers must pass `teams` in - the fallback self.teams() needs the main thread.
    def algo_get_last(self, teams=None):
        # Find the player to be acted upon. Original implementation takes the
        # player with lowest score most of the time, we take the last to join

        # If teams are even, just return
        if not teams:
            teams = self.teams()

        # See which team is bigger than the other
        if len(teams["blue"]) > len(teams["red"]):
            bigger_team = teams["blue"].copy()
        elif len(teams["red"]) > len(teams["blue"]):
            bigger_team = teams["red"].copy()
        else:
            minqlx.console_print("Cannot pick last player since there are none.")
            return

        minqlx.console_print("Picking someone to {} based on join times.".format(self.last_action))
        minqlx.console_print("Current join times: {}".format(self.join_match_times))
        bigger_team.sort(key = lambda el: self.find_time(el), reverse=True)
        lowest_player = bigger_team[0]

        minqlx.console_print("Picked {} from the {} team.".format(lowest_player.name, lowest_player.team))
        return lowest_player

    def help_start_kickthread(self, player, elo, highlow):
        class kickThread(threading.Thread):
            def __init__(self, plugin, player, elo):
                threading.Thread.__init__(self)
                self.plugin = plugin
                self.player = player
                self.elo = elo
                self.highlow = highlow
                self.go = True
            def try_mess(self):
                # msg() needs the main thread -- run() is a raw thread, like try_mute/try_kick.
                @minqlx.next_frame
                def execute(message):
                    self.plugin.msg(message)
                time.sleep(1)
                if self.plugin.get_cvar("qlx_elo_kick") == "1":
                    kickmsg = "so you'll be ^6kicked ^7shortly..."
                else:
                    kickmsg = "but you are free to keep watching."
                execute("^7Sorry, {} your glicko ({}) is too {}, {}".format(self.player.name, self.elo, self.highlow, kickmsg))
            def try_mute(self):
                @minqlx.next_frame
                def execute():
                    try:
                        self.player.mute()
                    except:
                        pass
                time.sleep(4)
                #self.player = self.plugin.player(self.player.id)
                if not self.player: self.stop()
                if self.go and self.plugin.get_cvar("qlx_elo_kick") == "1": execute()
            def try_kick(self):
                @minqlx.next_frame
                def execute():
                    try:
                        self.player.kick("^1GOT KICKED!^7 Glicko ({}) was too {} for this server.".format(self.elo, self.highlow))
                    except:
                        pass
                if self.plugin.get_cvar("qlx_elo_kick") == "0": return
                time.sleep(16)
                #self.player = self.plugin.player(self.player.id)
                if not self.player: self.stop()
                if self.go: execute()
            def run(self):
                self.try_mute()
                self.try_mess()
                self.try_kick()
                self.stop()
            def stop(self):
                self.go = False
                new_kickthreads = []
                for kt in self.plugin.kickthreads:
                    if kt[0] != self.player.steam_id:
                        new_kickthreads.append(kt)
                self.plugin.kickthreads = new_kickthreads
                del self

        t = kickThread(self, player, elo)
        t.start()
        self.kickthreads.append([player.steam_id, player.clean_name.lower(), t])

    def evaluate_elo_games(self, player, elo, games):

        key = RATING_KEY.format(player.steam_id, self.game.type_short)
        if (key in self.db):
            elo = int(self.db[key])

        try:
            completed = int(self.db[COMPLETED_KEY.format(player.steam_id)])
        except:
            completed = 0
        try:
            left = int(self.db[LEFT_KEY.format(player.steam_id)])
        except:
            left = 0

        max_elo = self.ELO_MAX
        for threshold, boundary in reversed(BOUNDARIES):
            if left + completed >= threshold:
                max_elo += boundary
                break

        if self.get_cvar("qlx_mybalance_exclude", int) and games < self.GAMES_NEEDED:
            #self.msg("{}'s ratings are too uninformative for this server. ({}/{} games played)".format(player.name, games, self.GAMES_NEEDED))
            return ['uninformative', elo]

        if not elo and not games: return # allow person to join

        # msg_main_thread - also reached from the fetch() worker via callback().
        if elo < self.ELO_MIN:
            if games < self.GAMES_NEEDED:
                self.msg_main_thread("{}'s ({}) is below the server limit ({}), but insufficient tracked games ({}/{}).".format(player.name, elo, self.ELO_MIN, games, self.GAMES_NEEDED))
                return
            return ['low', elo]
        if max_elo < elo:
            if games < self.GAMES_NEEDED:
                self.msg_main_thread("{}'s ({}) is above the server limit ({}), but insufficient tracked games ({}/{}).".format(player.name, elo, self.ELO_MAX, games, self.GAMES_NEEDED))
                return
            return ['high', elo]

    # Chat output for @minqlx.thread workers - msg needs the main thread.
    @minqlx.next_frame
    def msg_main_thread(self, message):
        self.msg(message)

    def find_by_name_or_id(self, player, target):
        # Find players returns a list of name-matching players
        def find_players(query):
            players = []
            for p in self.find_player(query):
                if p not in players:
                    players.append(p)
            return players

        # Tell a player which players matched
        def list_alternatives(players, indent=2):
            player.tell("A total of ^6{}^7 players matched for {}:".format(len(players),target))
            out = ""
            for p in players:
                out += " " * indent
                out += "{}^6:^7 {}\n".format(p.id, p.name)
            player.tell(out[:-1])

        # Get the list of matching players on name
        target_players = find_players(target)

        # If id:X is given and it amounts to a player, give it precedence.
        # This is to avoid deadlocks
        match = re.search("(id[=:][0-9]{1,2})", target)
        if match and match.group() == target:
            try:
                match_id = re.search("([0-9]{1,2})", target)
                player = self.player(int(match_id.group()))
                if player.steam_id:
                    return player
            except:
                pass

        # even if we get only 1 person, we need to check if the input was meant as an ID
        # if we also get an ID we should return with ambiguity

        try:
            i = int(target)
            target_player = self.player(i)
            if not (0 <= i < 64) or not target_player:
                raise ValueError
            # Add the found ID if the player was not already found
            if not target_player in target_players:
                target_players.append(target_player)
        except ValueError:
            pass

        # If there were absolutely no matches
        if not target_players:
            player.tell("Sorry, but no players matched your tokens: {}.".format(target))
            return None

        # If there were more than 1 matches
        if len(target_players) > 1:
            list_alternatives(target_players)
            return None

        # By now there can only be one person left
        return target_players.pop()