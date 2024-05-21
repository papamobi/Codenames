# Copyright (c) 2024 Codenames, MadHypnofrog
#
# https://github.com/papamobi/Codenames/
#
# Displays info about the server.

import minqlx
import minqlx.database


# you can change those messages and add new commands in a similar fashion
ADMINS = """
Admins list (contact through Discord for any questions. Use discord username in brackets below):
^4coeurl^7 (.coeurl)
^4mobi^7 (f.mobile)
^4frog:^7 (madhypnofrog)
^4cyku^7 (cyku)
"""

INFO = """
Frequently used commands:
^6!info^7 - display this message
^6!admins^7 - Show admin team list and their discord username for contact
^6!teams^7 - display team average elos and suggest a switch if necessary
^6!a^7 - agree to the switch
^6!elos^7 - display elos for all players
^6!elo <id>^7 - display the elo of a player
^6!mappool^7 - display all maps in the mappool
^6!motd^7 - display the message of the day
^6!afk^7 and ^6!here^7 - set your status while in spec
^6!q^7 - display the queue
^6!sounds^7 - enable or disable custom sounds
^6!listsounds <#soundpack>^7 - display a list of custom sounds
^6!pummel^7 and ^6!airpummel^7 - display pummel/air pummel stats for you and everyone on the server
^6!clan <tag>^7 - set your clan tag
^6!name <name>^7 - set your nickname (you can also override your steam name!)
^6!seen <STEAMID64>^7 - Check when the user was last seen on the server 
"""

class info(minqlx.Plugin):

    def __init__(self):
        super().__init__()
        self.add_command("admins", self.cmd_admins)
        self.add_command("info", self.cmd_info)

    def cmd_admins(self, player, msg, channel):
        player.tell(ADMINS)

    def cmd_info(self, player, msg, channel):
        player.tell(INFO)
