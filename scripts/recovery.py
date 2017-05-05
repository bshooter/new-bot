from twisted.internet.task import LoopingCall
from pyspades.constants import *

def apply_script(protocol, connection, config):
    class RecoveryConnection(connection):
        def __init__(self, *arg, **kw):
            connection.__init__(self, *arg, **kw)
            self.recovery_loop = LoopingCall(self.recovery)       
            
        def on_animation_update(self, jump, crouch, sneak, sprint):
            if crouch:
                if not self.recovery_loop.running:
                    self.recovery_loop.start(0.5, now = False)
            else:
                self.recovery_loop.stop()            
            return connection.on_animation_update(self, jump, crouch, sneak, sprint)
            
        def on_disconnect(self): 
            self.recovery_loop.stop()
            return connection.on_disconnect(self)
            
        def recovery(self):
            if self.hp > 20:
                self.set_hp(self.hp + 1, type = FALL_KILL)
             
    return protocol, RecoveryConnection 