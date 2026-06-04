# players_db.py - a minqlx plugin for managing player database records.
# Saves and loads permissions, bans, silences, and player info to/from file.
# Lists players with permissions, active bans, silences, leavers, and warnings.
# Supports name history lookup, bad name removal, and zero-permission cleanup.
# Note: minimum permission level is 2 (!bans, !silenced, !leavers, !warned); !perms and !sid require 3; destructive commands require 5.
# created by BarelyMiSSeD on 11-10-15
# refactored for correctness, performance, and clarity
#
"""
!getperms        - Save server permissions to file (fs_homepath/server_perms.txt)
!addperms        - Load permissions from file into the database
!perms           - List players with permissions on the server
!bans            - List banned players on the server
!silenced        - List silenced players on the server
!leavers         - List players flagged for leaving games
!warned          - List players warned for leaving games
!sid             - Show name/IP history for a Steam ID or connected player ID
!clearzeroperms  - Delete all permission=0 entries from the database
!removename      - Remove a specific name from a player's name history
"""

import minqlx
import os
import time
import datetime
import re

PLAYER_KEY = "minqlx:players:{}"
PLAYER_DB_KEY = "minqlx:players:{}:{}"
PERMS_FILE = "server_perms.txt"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DB_FILE = "server_db.txt"

VERSION = "2.1"

def _parse_time(s):
    """Parse a datetime string using TIME_FORMAT."""
    return datetime.datetime.strptime(s, TIME_FORMAT)

class players_db(minqlx.Plugin):
    def __init__(self):
        self.add_command("getperms", self.get_perms, 5)
        self.add_command("getdb", self.get_db, 5)
        self.add_command("addperms", self.add_perms, 5)
        self.add_command("savedb", self.add_db, 5)
        self.add_command(("perms", "listperms"), self.list_perms, 3)
        self.add_command(("bans", "banned", "listbans"), self.list_bans, 2)
        self.add_command(("silenced", "silences", "listsilenced"), self.list_silenced, 2)
        self.add_command("leavers", self.list_leavers, 2)
        self.add_command("warned", self.list_warned, 2)
        self.add_command("sid", self.sid_info, 3)
        self.add_command(("clearzeroperms", "cleanzero"), self.clear_zero_perms, 5)
        self.add_command("removename", self.remove_name, 5)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_valid_steam_id(self, sid):
        """Return True if sid is a 17-digit Steam ID (not a bot/server ID)."""
        s = str(sid)
        return len(s) == 17 and s[0] != "9"

    def player_name(self, steam_id):
        """Return the display name for a steam_id, falling back to DB history."""
        try:
            for player in self.players():
                if str(player.steam_id) == str(steam_id):
                    return player.name
        except Exception:
            pass
        try:
            return self.db.lindex(PLAYER_KEY.format(steam_id), 0)
        except Exception as e:
            minqlx.console_print(f"^1players_db player_name Exception {steam_id}: {e}")
            return str(steam_id)

    def get_db_field(self, field):
        """
        Read a Redis key and return (type_str, value).
        Returns (None, None) on error or unsupported type.
        zset entries are now exported as a list of (member, score) pairs.
        """
        try:
            entry_type = self.db.type(field)
            if entry_type == "set":
                return entry_type, self.db.smembers(field)
            elif entry_type == "hash":
                return entry_type, self.db.hgetall(field)
            elif entry_type == "list":
                return entry_type, self.db.lrange(field, 0, -1)
            elif entry_type == "zset":
                # Export as list of alternating member/score pairs so they can
                # be round-tripped back via enter_db.
                pairs = self.db.zrange(field, 0, -1, withscores=True)
                return entry_type, pairs
            else:
                return entry_type, self.db.get(field)
        except Exception as e:
            minqlx.console_print(f"^1players_db get_db_field Exception ({field}): {e}")
            return None, None

    def _get_leaver_data(self, asker):
        """
        Shared helper for show_leavers / show_warned.
        Returns (playerlist, min_games, ban_threshold, warn_threshold)
        or None if leaver bans are disabled.
        """
        if not self.get_cvar("qlx_leaverBan", bool):
            asker.tell("^5Leaver bans are not enabled on this server.")
            return None
        playerlist = self.db.keys(PLAYER_KEY.format("*"))
        min_games = self.get_cvar("qlx_leaverBanMinimumGames", int)
        ban_threshold = self.get_cvar("qlx_leaverBanThreshold", float)
        warn_threshold = self.get_cvar("qlx_leaverBanWarnThreshold", float)
        return playerlist, min_games, ban_threshold, warn_threshold

    # ------------------------------------------------------------------
    # DB export (!getdb)
    # ------------------------------------------------------------------

    def get_db(self, player, msg, channel):
        self.save_db(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def save_db(self, player):
        player.tell(f"^1Starting DB retrieval and writing to {DB_FILE} (this may take a while).")
        file_path = os.path.join(self.get_cvar("fs_homepath"), DB_FILE)
        try:
            with open(file_path, "w") as h:
                self._write_db_keys(h, self.db.keys("minqlx:players:*"))
                self._write_db_keys(h, self.db.keys("minqlx:ips:*"))
                self._write_set_key(h, "minqlx:players")
                self._write_set_key(h, "minqlx:ips")
        except Exception as e:
            minqlx.console_print(f"^1players_db save_db ERROR: {e}")
            player.tell(f"^1ERROR saving DB: {e}")
            return
        player.tell(f"^1Finished saving player DB to {DB_FILE}")

    def _write_db_keys(self, h, keys):
        for entry in keys:
            try:
                entry_type, value = self.get_db_field(entry)
                if not entry_type:
                    continue
                if entry_type in ("set", "list"):
                    h.write(f"{entry_type}//{entry}//" + "//".join(list(value)) + "\n")
                elif entry_type == "hash":
                    flat = []
                    for k, v in value.items():
                        flat.append(k)
                        flat.append(v)
                    h.write(f"{entry_type}//{entry}//" + "//".join(flat) + "\n")
                elif entry_type == "zset":
                    # Store as alternating member//score pairs
                    flat = []
                    for member, score in value:
                        flat.append(str(member))
                        flat.append(str(score))
                    h.write(f"{entry_type}//{entry}//" + "//".join(flat) + "\n")
                else:
                    h.write(f"{entry_type}//{entry}//{value}\n")
            except Exception as e:
                minqlx.console_print(f"^1players_db _write_db_keys error on {entry}: {e}")

    def _write_set_key(self, h, key):
        try:
            value = self.db.smembers(key)
            h.write("set//{}//{}\n".format(key, "//".join(list(value))))
        except Exception as e:
            minqlx.console_print(f"^1players_db _write_set_key error on {key}: {e}")

    # ------------------------------------------------------------------
    # DB import (!savedb)
    # ------------------------------------------------------------------

    def add_db(self, player, msg, channel):
        self.enter_db(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def enter_db(self, player):
        player.tell(f"^1Starting DB entries from {DB_FILE} (this may take a while).")
        file_path = os.path.join(self.get_cvar("fs_homepath"), DB_FILE)
        try:
            with open(file_path, "r") as h:
                for raw_line in h:
                    line = raw_line.rstrip("\n")
                    if not line:
                        continue
                    try:
                        self._import_db_line(line)
                    except Exception as e:
                        minqlx.console_print(f"^1players_db enter_db error on line: {e} | {line[:80]}")
        except Exception as e:
            player.tell(f"^1ERROR Opening DB file: {e}")
            return
        player.tell("^1Finished entering information to the database.")

    def _import_db_line(self, line):
        info = line.split("//")
        if len(info) < 3:
            return
        entry_type, key = info[0], info[1]
        values = info[2:]

        if entry_type == "string":
            self.db.set(key, values[0])

        elif entry_type == "set":
            if values:
                self.db.sadd(key, *values)

        elif entry_type == "list":
            existing = set(self.db.lrange(key, 0, -1))
            for item in values:
                if item not in existing:
                    self.db.lpush(key, item)
                    existing.add(item)

        elif entry_type == "zset":
            # Stored as alternating member/score pairs
            pairs = list(zip(values[0::2], values[1::2]))
            pipe = self.db.pipeline()
            for member, score in pairs:
                pipe.zadd(key, {member: float(score)})
            pipe.execute()

        elif entry_type == "hash":
            base_key = ":".join(key.split(":")[0:4])
            slot = self.db.zcard(base_key)
            # values are alternating field/value
            data = dict(zip(values[0::2], values[1::2]))
            required = {"expires", "reason", "issued", "issued_by"}
            if not required.issubset(data):
                minqlx.console_print(f"^1players_db _import_db_line: missing hash fields in {key}")
                return
            expires_ts = _parse_time(data["expires"]).timestamp()
            pipe = self.db.pipeline()
            pipe.zadd(base_key, {str(slot): expires_ts})
            pipe.hmset(f"{base_key}:{slot}", data)
            pipe.execute()

    # ------------------------------------------------------------------
    # Perms export/import (!getperms / !addperms)
    # ------------------------------------------------------------------

    def get_perms(self, player, msg, channel):
        self.save_perms(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def save_perms(self, player):
        playerlist = self.db.keys(PLAYER_DB_KEY.format("*", "permission"))
        file_path = os.path.join(self.get_cvar("fs_homepath"), PERMS_FILE)
        try:
            with open(file_path, "w") as h:
                for entry in playerlist:
                    steam_id = entry.split(":")[2]
                    if not self.is_valid_steam_id(steam_id):
                        continue
                    try:
                        perm = int(self.db.get(entry))
                    except (TypeError, ValueError):
                        continue
                    if perm > 0:
                        h.write(f"{steam_id}:{perm}\n")
        except Exception as e:
            player.tell(f"^1ERROR Opening perms file: {e}")
            return
        player.tell(f"^1Finished saving player permissions to {PERMS_FILE}")

    def add_perms(self, player, msg, channel):
        self.enter_perms(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def enter_perms(self, calling_player):
        file_path = os.path.join(self.get_cvar("fs_homepath"), PERMS_FILE)
        try:
            with open(file_path, "r") as h:
                for raw_line in h:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        parts = line.split(":")
                        if len(parts) != 2:
                            continue
                        sid, perm = parts[0].strip(), parts[1].strip()
                        self.db.set(PLAYER_DB_KEY.format(sid, "permission"), int(perm))
                    except Exception as e:
                        minqlx.console_print(f"^1players_db enter_perms error on line '{line}': {e}")
        except Exception as e:
            minqlx.console_print(f"^1ERROR Opening perms file: {e}")
            calling_player.tell(f"^1ERROR Opening perms file: {e}")
            return
        calling_player.tell("^1Finished entering player permissions to the database.")

    def clear_zero_perms(self, player, msg, channel):
        self.do_clear_zero_perms(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def do_clear_zero_perms(self, player):
        """Delete all permission keys whose value is 0 from the database."""
        playerlist = self.db.keys(PLAYER_DB_KEY.format("*", "permission"))
        removed = []
        for entry in playerlist:
            try:
                if int(self.db.get(entry)) == 0:
                    self.db.delete(entry)
                    steam_id = entry.split(":")[2]
                    removed.append(steam_id)
            except (TypeError, ValueError) as e:
                minqlx.console_print(f"^1players_db do_clear_zero_perms error on {entry}: {e}")
        if removed:
            player.tell(f"^2Removed ^7{len(removed)} ^2zero-permission entr{'y' if len(removed) == 1 else 'ies'}.")
        else:
            player.tell("^5No zero-permission entries found.")

    # ------------------------------------------------------------------
    # List permissions (!perms)
    # ------------------------------------------------------------------

    def list_perms(self, player, msg, channel):
        self.show_perms(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def show_perms(self, asker):
        playerlist = self.db.keys(PLAYER_DB_KEY.format("*", "permission"))
        # Group by permission level 1-5
        perms_lists = {i: [] for i in range(1, 6)}
        for entry in playerlist:
            steam_id = entry.split(":")[2]
            if not self.is_valid_steam_id(steam_id):
                continue
            try:
                perms = int(self.db.get(entry))
            except (TypeError, ValueError):
                continue
            if perms in perms_lists:
                perms_lists[perms].append(
                    f"{self.player_name(steam_id)} ^7({steam_id}): ^{perms}{perms}"
                )
        owner = minqlx.owner()
        asker.tell(f"^1Server Owner^7: {self.player_name(owner)} ^7({owner})")
        for level in range(5, 0, -1):
            if perms_lists[level]:
                asker.tell(f"^{level}Level {level} Permissions^7:")
                for p in perms_lists[level]:
                    asker.tell(p)

    # ------------------------------------------------------------------
    # List bans (!bans)
    # ------------------------------------------------------------------

    def list_bans(self, player, msg, channel):
        self.show_bans(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def show_bans(self, asker):
        playerlist = self.db.keys(PLAYER_DB_KEY.format("*", "bans"))
        bans_list = []
        now = time.time()
        for entry in playerlist:
            steam_id = entry.split(":")[2]
            banned = self.db.zrangebyscore(
                PLAYER_DB_KEY.format(steam_id, "bans"), now, "+inf", withscores=True
            )
            if not banned:
                continue
            try:
                longest_ban = self.db.hgetall(
                    PLAYER_DB_KEY.format(steam_id, "bans") + f":{int(banned[-1][0])}"
                )
                expires = _parse_time(longest_ban["expires"])
                if (expires - datetime.datetime.now()).total_seconds() > 0:
                    reason = longest_ban.get("reason") or "No Saved Reason"
                    bans_list.append(
                        f"{self.player_name(steam_id)} ^7({steam_id}): "
                        f"^6Expires: ^7{expires} "
                        f"^5Reason: ^7{reason} "
                        f"^2Issued By: ^7{self.player_name(longest_ban.get('issued_by', '?'))}"
                    )
            except Exception as e:
                minqlx.console_print(f"^1players_db show_bans error for {steam_id}: {e}")

        if bans_list:
            asker.tell("^5Bans^7:")
            for ban in bans_list:
                asker.tell(ban)
        else:
            asker.tell("^5No Active bans found.")

    # ------------------------------------------------------------------
    # List silenced (!silenced)
    # ------------------------------------------------------------------

    def list_silenced(self, player, msg, channel):
        self.show_silenced(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def show_silenced(self, asker):
        playerlist = self.db.keys(PLAYER_DB_KEY.format("*", "silences"))
        message = []
        now = time.time()
        for entry in playerlist:
            steam_id = entry.split(":")[2]
            silenced = self.db.zrangebyscore(
                PLAYER_DB_KEY.format(steam_id, "silences"), now, "+inf", withscores=True
            )
            if not silenced:
                continue
            try:
                silence_time = self.db.hgetall(
                    PLAYER_DB_KEY.format(steam_id, "silences") + f":{int(silenced[-1][0])}"
                )
                expires = _parse_time(silence_time["expires"])
                if (expires - datetime.datetime.now()).total_seconds() > 0:
                    reason = silence_time.get("reason") or "No Saved Reason"
                    message.append(
                        f"{self.player_name(steam_id)} ^7({steam_id}): "
                        f"^6Expires: ^7{expires} "
                        f"^5Reason: ^7{reason} "
                        f"^2Issued By: ^7{self.player_name(silence_time.get('issued_by', '?'))}"
                    )
            except Exception as e:
                minqlx.console_print(f"^1players_db show_silenced error for {steam_id}: {e}")

        if message:
            asker.tell("^5Silenced^7:")
            for silence in message:
                asker.tell(silence)
        else:
            asker.tell("^5No Active silences found.")

    # ------------------------------------------------------------------
    # List leavers (!leavers) and warned (!warned)
    # ------------------------------------------------------------------

    def list_leavers(self, player, msg, channel):
        self.show_leavers(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def show_leavers(self, asker):
        result = self._get_leaver_data(asker)
        if result is None:
            return
        playerlist, min_games, ban_threshold, _ = result
        message = []
        for entry in playerlist:
            steam_id = entry.split(":")[2]
            completed, left, total, ratio = self._leaver_stats(steam_id, min_games)
            if completed is None:
                continue
            if ratio <= ban_threshold:
                message.append(
                    f"{self.player_name(steam_id)} ^7({steam_id}): "
                    f"^6Games Played: ^7{total} ^5Left: ^7{left} ^4Percent: ^7{ratio:.2%}"
                )
        if message:
            asker.tell("^5Leaver Banned^7:")
            for line in message:
                asker.tell(line)
        else:
            asker.tell("^5No Leaver Bans found.")

    def list_warned(self, player, msg, channel):
        self.show_warned(player)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def show_warned(self, asker):
        result = self._get_leaver_data(asker)
        if result is None:
            return
        playerlist, min_games, ban_threshold, warn_threshold = result
        message = []
        for entry in playerlist:
            steam_id = entry.split(":")[2]
            completed, left, total, ratio = self._leaver_stats(steam_id, min_games)
            if completed is None:
                continue
            if ratio <= warn_threshold and (ratio > ban_threshold or total < min_games):
                message.append(
                    f"{self.player_name(steam_id)} ^7({steam_id}): "
                    f"^6Games Played: ^7{total} ^5Left: ^7{left} ^4Percent: ^7{ratio:.2%}"
                )
        if message:
            asker.tell("^5Leaver Warned^7:")
            for line in message:
                asker.tell(line)
        else:
            asker.tell("^5No Leaver Warned found.")

    def _leaver_stats(self, steam_id, min_games):
        """
        Return (completed, left, total, ratio) for a steam_id, or
        (None, None, None, None) if the player should be skipped.
        """
        try:
            completed = int(self.db[PLAYER_KEY.format(steam_id) + ":games_completed"])
            left = int(self.db[PLAYER_KEY.format(steam_id) + ":games_left"])
        except KeyError:
            return None, None, None, None
        total = completed + left
        if not total or total < min_games:
            return None, None, None, None
        ratio = completed / total
        return completed, left, total, ratio

    # ------------------------------------------------------------------
    # Steam ID info (!sid)
    # ------------------------------------------------------------------

    def sid_info(self, player, msg, channel):
        self.show_sid_info(player, msg)
        return minqlx.RET_STOP_ALL

    @minqlx.thread
    def show_sid_info(self, asker, msg):
        try:
            pid = int(msg[1])
            if 0 <= pid <= 63:
                sid = str(self.player(pid).steam_id)
            else:
                sid = str(pid)
            if not self.is_valid_steam_id(sid):
                asker.tell("^1Please enter a valid player ID or Steam ID.")
                return
        except TypeError:
            asker.tell("^1Include a Steam ID or a connected Player ID.")
            return
        except minqlx.NonexistentPlayerError:
            asker.tell("^1That Player ID is not a connected player.")
            return
        except Exception as e:
            minqlx.console_print(f"^1players_db show_sid_info Exception: {e}")
            return

        names = list(self.db.lrange(PLAYER_KEY.format(sid), 0, -1))
        if not names:
            asker.tell(f"^6No information for ^7{sid} ^6was found.")
            return

        asker.tell(f"^6Names found for Steam ID ^7{sid}^6:")
        for count, name in enumerate(names, start=1):
            asker.tell(f"^1Name {count}^7: {name}")

        ip_list = self.db.smembers(PLAYER_KEY.format(sid) + ":ips")
        shared_by = {}
        ip_line = []
        for count, ip in enumerate(ip_list):
            shared = self.db.smembers(f"minqlx:ips:{ip}")
            if len(shared) > 1:
                shared_by[ip] = shared
            ip_line.append(ip)
            if len(ip_line) == 5:
                asker.tell("^1IPs: ^2{}".format("^1, ^2".join(ip_line)))
                ip_line = []
        if ip_line:
            asker.tell("^1IPs: ^2{}".format("^1, ^2".join(ip_line)))

        for ip, ids in shared_by.items():
            id_list = list(ids)
            asker.tell(f"^1IP ^7{ip} ^1used by Steam IDs^7: ^2" + "^1, ^2".join(id_list[:3]))
            id_list = id_list[3:]
            while id_list:
                asker.tell("^2" + "^1, ^2".join(id_list[:5]))
                id_list = id_list[5:]

    # ------------------------------------------------------------------
    # Remove a name from a player's name history (!removename)
    # ------------------------------------------------------------------

    def remove_name(self, player, msg, channel):
        """Usage: !removename <steam_id> <name>"""
        if len(msg) < 3:
            player.tell("^1Usage: ^7!removename <steam_id> <name>")
            return minqlx.RET_STOP_ALL
        self.do_remove_name(player, msg[1], " ".join(msg[2:]))
        return minqlx.RET_STOP_ALL

    @staticmethod
    def _strip_colors(s):
        return re.sub(r'\^\d', '', s)

    @minqlx.thread
    def do_remove_name(self, player, sid, name):
        if not self.is_valid_steam_id(sid):
            player.tell("^1Invalid Steam ID.")
            return
        key = PLAYER_KEY.format(sid)
        try:
            all_names = self.db.lrange(key, 0, -1)
            matches = [n for n in all_names if players_db._strip_colors(n).lower() == name.lower()]
            if not matches:
                player.tell(f"^1Name '^7{name}^1' not found for Steam ID ^7{sid}^1.")
                return
            pipe = self.db.pipeline()
            for match in matches:
                pipe.lrem(key, 0, match)
            pipe.execute()
        except Exception as e:
            minqlx.console_print(f"^1players_db do_remove_name error: {e}")
            player.tell(f"^1Error removing name: {e}")
            return
        player.tell(f"^2Removed ^7{len(matches)} ^2instance(s) of '^7{name}^2' from Steam ID ^7{sid}^2.")
