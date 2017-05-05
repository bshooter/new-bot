# ammo.py by bshooter

from pyspades.server import weapon_reload
from pyspades.constants import *
from twisted.internet.reactor import callLater

def apply_script(protocol, connection, config):
    class PickupConnection(connection):       

        def add_ammo(self, clip, reserve):
            self.weapon_object.current_ammo += clip
            self.weapon_object.current_stock += reserve
            weapon_reload.player_id = self.player_id
            weapon_reload.clip_ammo = self.weapon_object.current_ammo  
            weapon_reload.reserve_ammo = self.weapon_object.current_stock
            self.protocol.send_contained(weapon_reload)

        def addammo(self):
            if self.weapon == SMG_WEAPON:
                self.add_ammo(0, 120)
            elif self.weapon == RIFLE_WEAPON:
                self.add_ammo(0, 50)
            elif self.weapon == SHOTGUN_WEAPON:
                self.add_ammo(0, 48)

        def on_refill(self):
            callLater(0.01, self.addammo)
            return connection.on_refill(self)
        
        def on_spawn(self, pos):
            self.addammo()
            return connection.on_spawn(self, pos)
    return protocol, PickupConnection