# Copyright (c) 2024 Codenames, MadHypnofrog
#
# https://github.com/papamobi/Codenames/
#
# Displays info about the server.

import minqlx
import minqlx.database


# you can change those messages and add new commands in a similar fashion - just don't forget to add \n at the end
# of each line!
ADMINS = """
Admins list (contact through Discord for any questions):\n
coeurl (.coeurl) - server owner\n
mobi (f.mobile)\n
:frog: (madhypnofrog)\n
cyku (cyku)
"""

INFO = """
Frequently used commands:\n
!info - display this message\n
!teams - display team average elos and suggest a switch if necessary\n
!a - agree to the switch\n
!elos - display elos for all players\n
!elo <id> - display the elo of a player\n
!mappool - display all maps in the mappool\n
!motd - display the message of the day\n
!afk and !here - set your status while in spec\n
!q - display the queue\n
!sounds - enable or disable custom sounds\n
!getsounds - display a list of custom sounds\n
!pummel - display pummel stats for you and everyone on the server\n
!clan <tag> - set your clan tag\n
!name <name> - set your nickname (you can also override your steam name!)
"""

class info(minqlx.Plugin):

    def __init__(self):
        super().__init__()
        self.add_command("admins", self.cmd_admins)
        self.add_command("info", self.cmd_info)

    def cmd_admins(self, player):
        player.tell(ADMINS)

    def cmd_info(self, player):
        player.tell(INFO)
