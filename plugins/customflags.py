# Plugin version 1.1 26/07/2024
# (c) Codenames, mobi
import minqlx
import minqlx.database


FLAGS = """
^4Usage^7: /country ^5<flag code>^7
^5amd^7 - ^5assist^7 - ^5bigben^7 - ^5boxing^7 - ^5cat^7 - ^5cat2^7 - ^5cat3^7 - ^5cutecaco^7 
^5doge^7 - ^5dogman^7 - ^5ezpepe^7 - ^5facepalm^7 - ^5fish^7 - ^5fisty^7 - ^5gauntlet^7 
^5gauntletlover^7 - ^5giga^7 - ^5goodgame^7 - ^5greensmile^7 - ^5grumpycat^7 - ^5guppy^7
^5heart^7 - ^5heart1^7 - ^5ikea^7 - ^5impressive^7 - ^5kek^7 - ^5kiss^7 - ^5lobster^7
^5mittens^7 - ^5net^7 - ^5nightmare^7 - ^5noentry^7 - ^5gauntletlover^7 - ^5giga^7
^5goodgame^7 - ^5greensmile^7 - ^5grumpycat^7 - ^5guppy^7 - ^5heart^7 - ^5notimpressive^7
^5nvidia^7 - ^5orange^7 - ^5orbb^7 - ^5owl^7 - ^5peka^7 - ^5pepelove^7 - ^5peperl^7 - ^5pepesword^7
^5perfect^7 - ^5playboy^7 - ^5pog^7 - ^5pornhub^7 - ^5ql_logo^7 - ^5quakelove^7 - ^5rail^7
^5revenge^7 - ^5sarge^7 - ^5smile^7 - ^5srl^7 - ^5suprise^7 - ^5think^7
^5underage^7 - ^5yeet^7 - ^5yu^7
"""


class customflags(minqlx.Plugin):

    def __init__(self):
        super().__init__()
        self.add_command("flags", self.cmd_flags)

    def cmd_flags(self, player, msg, channel):
        player.tell(FLAGS)
