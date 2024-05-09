# Copyright (c) 2024 Codenames, MadHypnofrog
#
# https://github.com/papamobi/Codenames/
#
# Kicks players that try to connect with the same SteamID from another client (e.g. stuck with 999 ping).

import minqlx

VERSION = "v0.1"


class kickduplicateid(minqlx.Plugin):

    def __init__(self):
        super().__init__()
        self.add_hook("player_connect", self.handle_player_connect)

    def handle_player_connect(self, player):
        for p in self.players():
            if p.steam_id == player.steam_id and p.id != player.id:
                self.logger.info("Found player {} with the same SteamID as connecting player {}, going to kick".format(p, player))
                p.kick("was kicked - player is connecting from a different client")