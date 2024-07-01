import minqlx
import subprocess
import shlex
import os

VERSION = "1.2"

class getws(minqlx.Plugin):
    def __init__(self):
        self.set_cvar_once("qlx_getwsAdmin", "5")
        self.set_cvar_once("qlx_getwsSteamCmd", "/home/steam/.steam/steamcmd/steamcmd.sh")

        self.add_command(("getws", "get"), self.cmd_getws, self.get_cvar("qlx_getwsAdmin", int))
        self.add_command(("delws", "remws"), self.cmd_delws, self.get_cvar("qlx_getwsAdmin", int))

    def cmd_getws(self, player, msg, channel):
        if len(msg) < 2 or not msg[1].isnumeric():
            player.tell("^1You must include a workshop steam ID number")
            return

        self.download_workshop(msg[1], player)

    def cmd_delws(self, player, msg, channel):
        if len(msg) < 2 or not msg[1].isnumeric():
            player.tell("^1You must include a workshop steam ID number")
            return

        self.remove_workshop(msg[1], player)

    @minqlx.thread
    def download_workshop(self, workshop_id, player=None):
        steam_cmd = self.get_cvar("qlx_getwsSteamCmd")
        base_path = self.get_cvar("fs_basepath")

        minqlx.console_print("Starting download_workshop for workshop ID: {}".format(workshop_id))
        if player:
            player.tell("^1Starting download for workshop ID: {}".format(workshop_id))

        def run(cmd):
            try:
                env = os.environ.copy()
                if "LD_PRELOAD" in env:
                    del env["LD_PRELOAD"]
                minqlx.console_print("Running command: {}".format(" ".join(cmd)))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                stdout, stderr = proc.communicate()
                minqlx.console_print("Command executed with return code: {}".format(proc.returncode))
                minqlx.console_print("stdout: {}".format(stdout.decode('utf-8')))
                minqlx.console_print("stderr: {}".format(stderr.decode('utf-8')))
                return proc.returncode, stdout, stderr
            except Exception as e:
                minqlx.console_print("Exception occurred: {}".format(str(e)))
                return -1, b"", str(e).encode()

        args = shlex.split("{} +force_install_dir {}/ +login anonymous +workshop_download_item 282440 {} +quit".format(steam_cmd, base_path, workshop_id))
        code, out, err = run(args)

        if player:
            player.tell("^1SteamCMD output:\n{}".format(out.decode('utf-8')))
            player.tell("^1SteamCMD errors:\n{}".format(err.decode('utf-8')))

        minqlx.console_print("^1SteamCMD output:\n{}".format(out.decode('utf-8')))
        minqlx.console_print("^1SteamCMD errors:\n{}".format(err.decode('utf-8')))

        if code == 0 and b'Success.' in out:
            player.tell("^2workshop {} Download Success".format(workshop_id))
            workshop_file = self.get_cvar("sv_workshopfile")
            with open("{}/baseq3/{}".format(base_path, workshop_file), "r") as f:
                items = f.readlines()
            
            found = any(item.strip() == str(workshop_id) for item in items if not item.startswith("#"))

            if not found:
                with open("{}/baseq3/{}".format(base_path, workshop_file), "a") as f:
                    if items and items[-1] not in ['\n', '\r\n']:
                        f.write("\n")
                    f.write("{}\n".format(workshop_id))

            if len(self.players()) == 0:
                minqlx.console_command("quit")
            else:
                player.tell("The server is not empty. Restart aborted.")
        else:
            if player:
                player.tell("^1Workshop {} download failed.".format(workshop_id))
                if code != 0:
                    player.tell("^1SteamCMD returned error code: {}".format(code))
                if err:
                    player.tell("^1SteamCMD error: {}".format(err.decode('utf-8')))

    @minqlx.thread
    def remove_workshop(self, workshop_id, player=None):
        base_path = self.get_cvar("fs_basepath")
        workshop_file = self.get_cvar("sv_workshopfile")
        with open("{}/baseq3/{}".format(base_path, workshop_file), "r") as f:
            items = f.readlines()
        
        found = False
        for index, item in enumerate(items):
            if item.startswith(str(workshop_id)):
                items[index] = "#" + item
                found = True
        
        if found:
            with open("{}/baseq3/{}".format(base_path, workshop_file), "w") as f:
                f.writelines(items)
            player.tell("^2Workshop {} commented out in {}".format(workshop_id, workshop_file))

            if len(self.players()) == 0:
                minqlx.console_command("quit")
            else:
                player.tell("The server is not empty. Restart aborted.")
        else:
            player.tell("^1Workshop {} not found in {}".format(workshop_id, workshop_file))
