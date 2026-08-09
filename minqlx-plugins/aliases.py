# Created by Thomas Jones on 14/12/2015 - thomas@tomtecsolutions.com
# aliases.py, a plugin for minqlx to show aliases from the redis database.
# This plugin is released to everyone, for any purpose. It comes with no warranty, no guarantee it works, it's released AS IS.
# You can modify everything, except for lines 1-4 and the !tomtec_versions code. They're there to indicate I whacked this together originally. Please make it better :D

import minqlx
import minqlx.database

class aliases(minqlx.Plugin):
    database = minqlx.database.Redis
    
    def __init__(self):
        self.add_command("alias", self.cmd_alias, usage="<id>")
        self.add_command("clearalias", self.cmd_clearalias, 5, usage="<id>")
        self.add_command("clearaliases", self.cmd_clearaliases, 5)
        self.add_command("tomtec_versions", self.cmd_showversion)

        self.plugin_version = "1.7"

    def cmd_alias(self, player, msg, channel):
        if len(msg) < 2:
            return minqlx.RET_USAGE

        try:
            ident = int(msg[1])
            target_player = None
            if 0 <= ident < 64:
                steam_id = self.player(int(msg[1])).steam_id
                player_name = self.player(int(msg[1])).name
                ip_address = self.player(int(msg[1])).ip
            else:
                steam_id = ident
                key = "minqlx:players:{}:ips".format(steam_id)
                iplist = list(self.db.smembers(key))
                if not iplist:
                    channel.reply("^1No IP addresses recorded for that Steam ID.")
                    return
                ip_address = iplist[-1]
                # Look up the last-known name from Redis so we don't display
                # the raw numeric SteamID64 as "player name" in the output.
                name_key = "minqlx:players:{}".format(steam_id)
                names = self.db.lrange(name_key, 0, -1)
                player_name = names[-1] if names else str(steam_id)
        except ValueError:
            channel.reply("Invalid ID. Use either a client ID or a SteamID64.")
            return
        except minqlx.NonexistentPlayerError:
            channel.reply("Invalid client ID. Use either a client ID or a SteamID64.")
            return

        key = "minqlx:ips:{}".format(ip_address)
        steamid_iplist = list(self.db.smembers(key))

        namelist = list()
        
        for steam_id in steamid_iplist:
            key = "minqlx:players:{}".format(steam_id)
            namelist = namelist + (list(self.db.lrange(key, 0, -1)))
            
        channel.reply("{}^7 has played on this server under the following names:".format(player_name))
        channel.reply(" ^4*^7  {}".format("   \n ^4*^7  ".join(namelist)))

        channel.reply("{}^7 has played on this server under the following Steam64 IDs:".format(player_name))
        channel.reply(" ^4*^7  {}".format("   \n ^4*^7  ".join(steamid_iplist)))

        
    def cmd_clearaliases(self, player, msg, channel):
        # this function came from namesfix.py by Mino
        players = self.db.smembers("minqlx:players")
        for p in players:
            del self.db["minqlx:players:{}".format(p)]
        channel.reply("All aliases for all players ({} players in total) were cleared.".format(len(players)))

    def cmd_clearalias(self, player, msg, channel):
        if len(msg) < 2:
            return minqlx.RET_USAGE

        # Resolve the input to a steam_id (matches cmd_alias's input handling)
        try:
            ident = int(msg[1])
            if 0 <= ident < 64:
                # Client ID -- look up the connected player at that slot
                target = self.player(ident)
                steam_id = target.steam_id
                display_name = target.name
            else:
                # SteamID64 -- use it directly. Look up last-known name from
                # Redis for the display, fall back to the raw ID.
                steam_id = ident
                names = self.db.lrange("minqlx:players:{}".format(steam_id), 0, -1)
                display_name = names[-1] if names else str(steam_id)
        except ValueError:
            channel.reply("^1Invalid ID. Use either a client ID or a SteamID64.")
            return
        except minqlx.NonexistentPlayerError:
            channel.reply("^1Invalid client ID. Use either a client ID or a SteamID64.")
            return

        key = "minqlx:players:{}".format(steam_id)
        if key not in self.db:
            channel.reply("^3No aliases found for {}^7.".format(display_name))
            return

        count = self.db.llen(key)
        del self.db[key]
        channel.reply("Aliases for {}^7 cleared ({} names removed).".format(display_name, count))

    def cmd_showversion(self, player, msg, channel):
        channel.reply("^4aliases.py^7 - version {}, created by Thomas Jones on 14/12/2015.".format(self.plugin_version))
