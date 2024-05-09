# This is an extension plugin  for minqlx.
# Copyright (C) 2016 mattiZed (github) aka mattiZed (ql)

# You can redistribute it and/or modify it under the terms of the 
# GNU General Public License as published by the Free Software Foundation, 
# either version 3 of the License, or (at your option) any later version.

# You should have received a copy of the GNU General Public License
# along with minqlx. If not, see <http://www.gnu.org/licenses/>.

# Parts of this plugin were inspired by iou(onegirl)'s autospec plugin.
# Deciding who to put to spectators it goes a whole different way though:

# We keep a dictionary containing all players on the server as keys and their
# PLAYTIME as values. In this context, playtime means for how long the player
# was in red or blue team in an ACTIVE GAME - no matter for how long
# he was alive though!

# For now, the plugin warns that teams are uneven on event round_countdown and 
# slays or move to spectators the player who was in-game the shortest 
# on event round_start.

# I wrote a little timer module that can be used to start and stop timers.
# If a timer was stopped yet not cleared 
# (therefore you'd have to reinstantiate the object) 
# the modules' .start() method just resumes the timer.

# .elapsed() tells how many seconds have passed since the timer was
# _started_for_the_first_time_, no matter if it is paused or running.

# Please consider this a very early version and highly experimental. 
# I haven't tested it that much yet because testing these things is hard...

# Now with non-round-based gametypes support. For handling the new joined players use the
# new queue plugin: https://github.com/Melodeiro/minqlx-plugins_mattiZed/blob/master/queue.py

import minqlx
import datetime
import time
import threading

NON_ROUND_BASED_GAMETYPES = ("ctf", "dom", "tdm", "1f", "har")
SLAY_NOT_SUPPORTED_GAMETYPES = ("ft", "dom", "tdm", "1f", "har")

class timer():
    def __init__(self, running=False):
        self._started = None
        self._running = False
        self._elapsed = 0
        if running:
            self.start()
    
    def start(self):
        if self._running:
            return
            
        self._started = datetime.datetime.now()
        self._running = True
        
    def stop(self):
        if not self._started or not self._running:
            return
        stopped = datetime.datetime.now()
        diff = stopped - self._started
        self._elapsed += diff.seconds
        self._running = False
    
    def elapsed(self):
        if self._running:
            now = datetime.datetime.now()
            diff = now - self._started
            self._elapsed += diff.seconds
            self._started = now
            return self._elapsed
        else:
            return self._elapsed

class uneventeams(minqlx.Plugin):
    def __init__(self):
        self.add_hook("new_game", self.handle_new_game)
        self.add_hook("game_end", self.handle_game_end)
        self.add_hook("player_connect", self.handle_player_connect)
        self.add_hook("team_switch", self.handle_team_switch)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("round_start", self.handle_round_start)
        self.add_hook("round_countdown", self.handle_round_countdown)
        self.add_hook("game_start", self.handle_game_start)
        self.add_hook("game_countdown", self.handle_game_countdown)
        self.add_command("playertimes", self.cmd_playertimes, 2)
        self.add_command("utversion", self.cmd_utversion)
        
        # { steam_id : time_played }
        self._players = {}
        self.is_endscreen = False
        
        self.version = 1.8
        self.plugin_updater_url = "https://raw.githubusercontent.com/Melodeiro/minqlx-plugins_mattiZed/master/uneventeams.py"
        
        self.initialize()
        
        
        # 0 to slay or 1 to move to spectators when teams are uneven
        self.set_cvar_once("qlx_unevenTeamsAction", "0")
        # Minimum amount of players in red + blue for uneventeams to work
        self.set_cvar_once("qlx_unevenTeamsMinPlayers", "2")
        # Delay (seconds) before excess player will be handled in non round-based gamemodes
        self.set_cvar_once("qlx_unevenTeamsActionDelay", "15")
        # If qlx_unevenTeamsInstantWarning set to 1, don't wait for next round for checking 
        # and moving to spectators. Not available for certain gametypes
        self.set_cvar_once("qlx_unevenTeamsInstantWarning", "0")
        
    def initialize(self):
        '''
            Equip all players with timers on plugin load.
        '''
        players = self.teams()
        
        for p in players["red"]:
            self._players[p.steam_id] = timer()
            self._players[p.steam_id].start()
        for p in players["blue"]:
            self._players[p.steam_id] = timer()
            self._players[p.steam_id].start()
        for p in players["spectator"]:
            self._players[p.steam_id] = timer()
    
    @minqlx.next_frame
    def check_teams(self, punish = False, old_guy = None):
        '''
            Check if teams are uneven and if so, warn the player with the 
            least amount of time played, or punish if variable set to True.
        '''
        @minqlx.thread
        def deferred_punish(guy):
            '''
                Perform an action on excess player with delay.
            '''
            delay = self.get_cvar("qlx_unevenTeamsActionDelay", int)
            time.sleep(delay)
            self.check_teams(True, guy)
        
        min_players = self.get_cvar("qlx_unevenTeamsMinPlayers", int)
        instant_warning = self.get_cvar("qlx_unevenTeamsInstantWarning", int)
        
        teams = self.teams()
        
        if self.is_endscreen:
            return
        if len(teams["red"]) == len(teams["blue"]):
            return
        if len(teams["red"] + teams["blue"]) < min_players:
            return
        if len(teams["red"]) > len(teams["blue"]):
            guy = self.player(self.find_lastjoined("red"))
        else:
            guy = self.player(self.find_lastjoined("blue"))
            
        # cancel the punishment if last joined guy has changed (only for instant warning)
        if instant_warning == 1 or self.game.type_short in NON_ROUND_BASED_GAMETYPES:
            if old_guy and old_guy != guy:
                punish = False
            
        if punish:
            action = self.get_cvar("qlx_unevenTeamsAction", int)
            
            if action == 1 or self.game.type_short in SLAY_NOT_SUPPORTED_GAMETYPES:
                guy.put("spectator")
                try:
                    # adding the guy to the first place of queue (needs new queue.py plugin installed)
                    queue = minqlx.Plugin._loaded_plugins['queue']
                    queue.addToQueue(guy, 0)
                    self.msg("^1Uneven Teams^7 >> {}^7 was moved to queue.".format(guy.name))
                except:
                    self.msg("^1Uneven Teams^7 >> {}^7 was moved to spectators.".format(guy.name))
            elif action == 0:
                guy.health = 0
                self.msg("^1Uneven Teams^7 >> {}^7 was slain.".format(guy.name))
        else:
            self.msg("^1Uneven Teams^7 >> {}^7 joined last and should spectate".format(guy.name))
            # punish the guy after certain delay if instant warning is enabled
            if instant_warning == 1 or self.game.type_short in NON_ROUND_BASED_GAMETYPES:
                deferred_punish(guy)
                
    def handle_game_countdown(self):
        if self.game.type_short in NON_ROUND_BASED_GAMETYPES:
            self.check_teams()
        
    def handle_game_start(self, data):
        if self.game.type_short in NON_ROUND_BASED_GAMETYPES:
            self.check_teams(True)
    
    def handle_round_countdown(self, round_number):
        '''
            Warn excess player at the end of round.
        '''
        instant_warning = self.get_cvar("qlx_unevenTeamsInstantWarning", int)
        if instant_warning == 0:
            self.check_teams()
    
    def handle_round_start(self, round_number):
        '''
            Kick/move to spectators excess player at the start of round.
        '''
        for p in self._players.keys():
            try: 
                self.player(p)
            except:
                self.deferred_removing(p, self._players[p.steam_id].elapsed())
        
        self.check_teams(True)
    
    def handle_team_switch(self, player, old_team, new_team):
        '''
            If a player joined spectators he cant gain playtime.
        '''
        if new_team == "spectator":
            self._players[player.steam_id].stop()
            self.deferred_removing(player, self._players[player.steam_id].elapsed())
            
            instant_warning = self.get_cvar("qlx_unevenTeamsInstantWarning", int)
            if self.game.state == 'in_progress':
                if self.game.type_short in NON_ROUND_BASED_GAMETYPES or instant_warning == 1:
                    self.check_teams()
        
        if new_team == "red" or new_team == "blue":
            self._players[player.steam_id].start()
            
    def handle_player_disconnect(self, player, reason):
        if player.steam_id in self._players.keys():
            self._players[player.steam_id].stop()
            self.deferred_removing(player, self._players[player.steam_id].elapsed())
        
        instant_warning = self.get_cvar("qlx_unevenTeamsInstantWarning", int)
        if self.game.state == 'in_progress':
            if self.game.type_short in NON_ROUND_BASED_GAMETYPES or instant_warning == 1:
                self.check_teams()
    
    def handle_player_connect(self, player):
        '''
            Equip every new player with a timer instance.
        '''
        if player.steam_id not in self._players.keys():
            self._players[player.steam_id] = timer()
    
    def cmd_playertimes(self, player, msg, channel):
        # This one is mostly for debugging.
        teams = self.teams()
        red_msg = ""
        for p in teams["red"]:
            red_msg += "^7{}:^1 {}^7s ".format(p, self._players[p.steam_id].elapsed())
        
        blue_msg = ""
        for p in teams["blue"]:
            blue_msg += "^7{}:^4 {}^7s ".format(p, self._players[p.steam_id].elapsed())
            
        spec_msg = ""
        for p in teams["spectator"]:
            spec_msg += "^7{}:^7 {}^7s ".format(p, self._players[p.steam_id].elapsed())
        
        channel.reply(red_msg)
        channel.reply(blue_msg)
        channel.reply(spec_msg)
    
    def find_lastjoined(self, team):
        '''
            Find the player with the least amount of time played.
        '''
        teams = self.teams()
        if team == "red":
            players = teams["red"]
        else:
            players = teams["blue"]
            
        bigger_team = {}
        for p in players:
            bigger_team[p.steam_id] = self._players[p.steam_id].elapsed()
            
        namesbytime = sorted(bigger_team, key = lambda item: bigger_team[item])
        return namesbytime[0]
        
    @minqlx.thread
    def deferred_removing(self, player, old_elapsed):
        '''
            Deffered removing player timer after 180 seconds
            or resetting it if player didn't joined teams, and currently spectating
        '''
        @minqlx.next_frame
        def removing():
            players = self.teams()["red"] + self.teams()["blue"] + self.teams()["spectator"]
            
            if player in players:
                if self._players[player.steam_id].elapsed() == old_elapsed:
                    self._players[player.steam_id] = timer()
            else:
                del self._players[player.steam_id]
            
        
        time.sleep(180)
        
        if player.steam_id in self._players.keys():
            removing()
            
    def cmd_utversion(self, player, msg, channel):
        channel.reply('^7This server has installed ^2uneventeams.py {} ^7ver. by matti^1Z^7ed'.format(self.version))
    
    def handle_new_game(self):
        self.is_endscreen = False
        
    def handle_game_end(self, data):
        self.is_endscreen = True
