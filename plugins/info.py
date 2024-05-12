# Copyright (c) 2024 Codenames, MadHypnofrog
#
# https://github.com/papamobi/Codenames/
#
# Displays info about the server.

import minqlx
import minqlx.database


# you can change those messages and add new commands in a similar fashion
ADMINS = """
Admins list (contact through Discord for any questions):
coeurl (.coeurl) - server owner
mobi (f.mobile)
:frog: (madhypnofrog)
cyku (cyku)
"""

INFO = """
Frequently used commands:
!info - display this message
!teams - display team average elos and suggest a switch if necessary
!a - agree to the switch
!elos - display elos for all players
!elo <id> - display the elo of a player
!mappool - display all maps in the mappool
!motd - display the message of the day
!afk and !here - set your status while in spec
!q - display the queue
!sounds - enable or disable custom sounds
!getsounds - display a list of custom sounds
!pummel - display pummel stats for you and everyone on the server
!clan <tag> - set your clan tag
!name <name> - set your nickname (you can also override your steam name!)
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
