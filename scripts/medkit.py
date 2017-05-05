"""
Gives a specified amount of medkits on spawn

Author: Booboorocks998
Maintainer: mat^2
"""

from twisted.internet.task import LoopingCall
import commands
from pyspades.constants import *

@commands.alias('m')
def medkit(connection):
    if connection.medkits and connection.hp < 100:
        connection.set_hp(connection.hp + connection.protocol.heal_amount,
            type = FALL_KILL)
        connection.medkits -= 1
        connection.send_chat('You have been healed')
    else:
        connection.send_chat("You don't have any medkits or have full health!")
commands.add(medkit)

def apply_script(protocol, connection, config):
    default_medkits = config.get('medkits', 1)
    medkit_heal_amount = config.get('medkit_heal_amount', 40)
    def __init__(self, *arg, **kw):
        connection.__init__(self, *arg, **kw)
        self.recovery_loop = LoopingCall(self.recovery)      
    
    class MedkitConnection(connection):
        medkits = 0
        def on_spawn(self, pos):
            self.medkits = default_medkits
            self.send_chat('You have %s medkit!' % self.medkits)
            return connection.on_spawn(self, pos)

        def on_refill(self):
            self.medkits = default_medkits
            self.send_chat('You have %s medkit!' % self.medkits)
            return connection.on_refill(self)

        def on_disconnect(self): 
            self.recovery_loop.stop()
            return connection.on_disconnect(self)
            
        def recovery(self):
            self.set_hp(self.hp + 1, type = FALL_KILL)
            
    class MedkitProtocol(protocol):
        heal_amount = medkit_heal_amount

    return MedkitProtocol, MedkitConnection
