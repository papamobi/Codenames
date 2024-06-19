import minqlx
import minqlx.database


FLAGS = """
^4Usage^7: /country ^5<flag code>^7
^5boxing^7 - ^5bruh^7 - ^5doge^7 - ^5dogman^7 - ^5fish^7 - ^5fisty^7 - ^5gauntlet^7 - ^5giga^7 - 
^5goodgame^7 - ^5guppy^7 - ^5ikea^7 - ^5lobster^7 - ^5mittens^7 - ^5notimpressive^7 - ^5peperl^7 - 
^5pepesword^7 - ^5pog^7 - ^5pornhub^7 - ^5ql_logo^7 - ^5quake3^7 - ^5smile^7 - ^5suprise^7 - 
^5tetromino^7 - ^5think^7 - ^5yeet^7 - ^5yu^7
"""


class customflags(minqlx.Plugin):

    def __init__(self):
        super().__init__()
        self.add_command("flags", self.cmd_flags)

    def cmd_flags(self, player, msg, channel):
        player.tell(FLAGS)
