# github.com
# there should be no water on the map
# requires adding the 'local' attribute to server.py's ServerConnection
# 
# *** 201,206 ****
# --- 201,207 ----
#       last_block = None
#       map_data = None
#       last_position_update = None
# +     local = False
#       
#       def __init__(self, *arg, **kw):
#           BaseConnection.__init__(self, *arg, **kw)
# *** 211,216 ****
# --- 212,219 ----
#           self.rapids = SlidingWindow(RAPID_WINDOW_ENTRIES)
#       
#       def on_connect(self):
# +         if self.local:
# +             return
#           if self.peer.eventData != self.protocol.version:
#               self.disconnect(ERROR_WRONG_VERSION)
#               return

LOGIC_FPS = 15 # 4
MINIMUM_PATH_REQUEST_INTERVAL = 1.0 # seconds
TICKS_STUMPED_BEFORE_RETRYING = 40
TICKS_STUMPED_BEFORE_WIGGLING = 70
TICKS_STUMPED_BEFORE_KILL = 600
TICKS_BEFORE_JUMPING = 8
PROXIMITY_RAY_LENGTH = 4.0

from math import cos, sin, fabs, sqrt
from random import randrange
from enet import Address
from twisted.internet.reactor import seconds, callLater
from twisted.internet.threads import deferToThread
from twisted.internet.task import LoopingCall
from pyspades.protocol import BaseConnection
from pyspades.server import Territory, block_action
from pyspades.server import position_data, orientation_data, input_data
from pyspades.server import weapon_input, set_tool, grenade_packet
from pyspades.world import Grenade
from pyspades.common import Vertex3, sgn
from pyspades.collision import vector_collision, distance_3d, collision_3d
from pyspades.constants import *
from pyspades.navigation import Navigation, is_location_local
from commands import admin, add, name, get_team

@admin
@name('addbot')
def add_bot(connection, amount = None, team = None):
    protocol = connection.protocol
    if team:
        bot_team = get_team(connection, team)
    blue, green = protocol.blue_team, protocol.green_team
    for i in xrange(int(amount or 1)):
        if not team:
            bot_team = blue if blue.count() < green.count() else green
        bot = protocol.add_bot(bot_team)
        if not bot:
            return "Couldn't add bot!"

@admin
@name('toggleai')
def toggle_ai(connection):
    protocol = connection.protocol
    protocol.ai_enabled = not protocol.ai_enabled
    if not protocol.ai_enabled:
        for bot in protocol.bots:
            bot.flush_input()
    state = 'enabled' if protocol.ai_enabled else 'disabled'
    protocol.send_chat('AI %s!' % state)
    protocol.irc_say('* %s %s AI' % (connection.name, state))

add(add_bot)
add(toggle_ai)

class LocalPeer:
    #address = Address(None, 0)
    address = Address('255.255.255.255', 0)
    roundTripTime = 0.0
    
    def send(self, *arg, **kw):
        pass
    
    def reset(self):
        pass

def is_location_near(a, b):
    x, y, z = a
    i, j, k = b
    size = 16
    return x >= i - size and x < i + size and y >= j - size and y < j + size

def apply_script(protocol, connection, config):
    class BotProtocol(protocol):
        game_mode = TC_MODE 
        bots = None
        ai_enabled = True
        
        def add_bot(self, team):
            if len(self.connections) + len(self.bots) >= 32:
                return None
            bot = self.connection_class(self, None)
            bot.join_game(team)
            self.bots.append(bot)
            return bot
        
        def on_world_update(self):
            if self.bots:
                if self.ai_enabled:
                    for bot in self.bots:
                        bot.think()
                        bot.update()
                for bot in self.bots:
                    bot.shotgun_amount = 0
            protocol.on_world_update(self)
        
        def on_map_change(self, map):
            self.bots = []
            current_time = seconds()
            self.navigation = Navigation(map)
            dt = seconds() - current_time
            print "Navgraph contains %s nodes. Generation took %s" % (
                self.navigation.get_node_count(), dt)
            protocol.on_map_change(self, map)
        
        def on_map_leave(self):
            for bot in self.bots[:]:
                bot.disconnect()
            self.bots = None
            protocol.on_map_leave(self)
    
    class BotConnection(connection):
        plan = []
        path = []
        steps = []
        lookahead = None
        path_defer = None
        plan_defer = None
        future_plan = None
        last_path_time = None
        next_step = None
        to_next_step = None
        aim = None
        aim_at = None
        follow = None
        follow_was_local = True
        acquire_targets = True
        target_orientation = None
        input = None
        last_pos = None
        ticks_obstructed = 0
        ticks_stumped = 0
        ticks_wiggling_remaining = 0
        shotgun_amount = 0
        goal = None
        grenade_call = None
        
        _turn_speed = None
        _turn_vector = None
        def _get_turn_speed(self):
            return self._turn_speed
        def _set_turn_speed(self, value):
            self._turn_speed = value
            self._turn_vector = Vertex3(cos(value), sin(value), 0.0)
        turn_speed = property(_get_turn_speed, _set_turn_speed)
        
        def __init__(self, protocol, peer, *arg, **kw):
            if peer is not None:
                return connection.__init__(self, protocol, peer)
            self.local = True
            connection.__init__(self, protocol, LocalPeer(), *arg, **kw)
            self.on_connect()
            #~ self.saved_loaders = None
            self._send_connection_data()
            self.send_map()
            
            self.aim = Vertex3()
            self.target_orientation = Vertex3()
            self.to_next_step = Vertex3()
            self.turn_speed = 0.15 # rads per tick
            self.last_pos = Vertex3()
            self.input = set()
            self.spade_loop = LoopingCall(self.wield_spade)
        
        def join_game(self, team):
            self.name = 'Deuce%s' % str(self.player_id)
            self.team = team
            self.set_weapon(RIFLE_WEAPON, True)
            self.protocol.players[(self.name, self.player_id)] = self
            self.on_login(self.name)
            self.spawn()
        
        def disconnect(self, data = 0):
            if not self.local:
                return connection.disconnect(self)
            if self.disconnected:
                return
            if self.plan_defer:
                self.plan_defer.cancel()
            self.plan_defer = None
            if self.plan_defer:
                self.plan_defer.cancel()
            self.path_defer = None
            self.protocol.bots.remove(self)
            self.disconnected = True
            self.on_disconnect()
        
        def go_to(self, location):
            if self.plan_defer:
                self.plan_defer.cancel()
            self.plan = []
            self.path = []
            self.steps = []
            self.next_step = None
            x, y, z = self.world_object.position.get()
            defer = deferToThread(self.protocol.navigation.find_path,
                x, y, z, *location)
            defer.addCallback(self.finished_plan_job)
            defer.addErrback(self.cancelled_job)
            self.plan_defer = defer
        
        def cancelled_job(self, failure):
            pass
        
        def finished_plan_job(self, plan):
            # called by self.plan_defer on completion
            self.future_plan = plan
            self.plan_defer = None
        
        def finished_path_job(self, path):
            # called by self.path_defer on completion
            # self.path is None at this point so we can swap right ahead
            self.path = path
            self.path_defer = None
        
        def think(self):
            do_logic = self.protocol.loop_count % LOGIC_FPS == self.player_id % LOGIC_FPS
            if not do_logic:
                return
            if 0 < self.hp < 100:
                new_hp = self.hp * 1.01 + 0.5
                if new_hp >= 100:
                    self.hp = 100
                else:
                    self.hp = new_hp
            elif not self.hp:
                return
            self.on_position_update()

            obj = self.world_object
            pos = obj.position
            ori = obj.orientation
            nav = self.protocol.navigation
            x, y, z = pos.get()
            
            if self.future_plan:
                # swap out plan for the just finished one
                self.plan, self.future_plan = self.future_plan, None
            
            heading_to = None
            if self.follow:
                follow_xyz = None
                if type(self.follow) is not Territory:
                    if self.follow.hp:
                        follow_xyz = self.follow.world_object.position.get()
                else:
                    follow_xyz = self.follow.get()
                if follow_xyz is not None:
                    if is_location_local(pos.get(), follow_xyz):
                        heading_to = follow_xyz
                        self.follow_was_local = True
                    elif self.follow_was_local:
                        self.follow_was_local = False
                        if not self.plan_defer:
                            defer = deferToThread(nav.find_path, x, y, z, *follow_xyz)
                            defer.addCallback(self.finished_plan_job)
                            defer.addErrback(self.cancelled_job)
                            self.plan_defer = defer
            
            can_request_path = not self.path_defer and (self.last_path_time is None or
                seconds() - self.last_path_time > MINIMUM_PATH_REQUEST_INTERVAL)
            if not self.path and can_request_path:
                while self.plan and is_location_near(pos.get(), self.plan[-1]):
                    self.lookahead = self.plan.pop()
                if self.lookahead:
                    # keep track of the lookahead in case we run out of path but
                    # the plan stays the same
                    self.last_path_time = seconds()
                    defer = deferToThread(nav.find_path, x, y, z, *self.lookahead)
                    defer.addCallback(self.finished_path_job)
                    defer.addErrback(self.cancelled_job)
                    self.path_defer = defer
            
            if not heading_to:
                pos_z2 = (pos.x, pos.y, int(pos.z + 2))
                while self.path and is_location_local(pos_z2, self.path[-1]):
                    heading_to = self.path.pop()
            
            if heading_to:
                steps = nav.find_local_path(pos.x, pos.y, pos.z + 2, *heading_to)
                if steps:
                    self.steps = steps
                    self.next_step = None
                elif not self.steps:
                    self.next_step = heading_to[:2]
            elif not self.steps:
                # reconsider path
                self.path = None
            
            # find nearby foes
            if self.protocol.game_mode == CTF_MODE:
                other_entities = self.team.other.flag
            elif self.protocol.game_mode == TC_MODE:
                other_entities = []
                for entity in self.protocol.entities:
                    if entity.team == self.team:
                        continue
                    other_entities.append(entity)

            if self.acquire_targets:
                old_distance = float("inf")
                #print(other_entities[0].)
                for player in list(self.team.other.get_players()) + other_entities:
                    if type(player) is not Territory:
                        new_distance = distance_3d(
                            pos.get(), player.get_location())
                    else:
                        new_distance = distance_3d(pos.get(), player.get())
                    if new_distance < old_distance:
                        old_distance = new_distance
                        self.follow = player
                        self.aim_at = player
                        self.follow_was_local = True
            elif not self.plan:
                # find something to do
                pass

        def update(self):
            if not self.hp:
                return
            obj = self.world_object
            pos = obj.position
            ori = obj.orientation
            nav = self.protocol.navigation
            vel = obj.velocity
            obj.set_position(*self.get_location())

            if self.aim_at:
                aim_at_pos = None
                if type(self.aim_at) is not Territory:
                    if self.aim_at.hp:
                        aim_at_pos = self.aim_at.world_object.position
                else:
                    aim_at_pos = Vertex3()
                    aim_at_pos.set(*self.aim_at.get())
                if aim_at_pos is not None:
                    self.aim.set_vector(aim_at_pos)
                    self.aim -= pos
                    distance_to_aim = self.aim.normalize() # don't move this line
                    # look at the target if it's within sight
                    if obj.can_see(*aim_at_pos.get()):
                        self.target_orientation.set_vector(self.aim)
                    if distance_to_aim <= MELEE_DISTANCE:
                        self.input.discard('sprint')
                        current_time = seconds()
                        if current_time - self.last_spade >= 0.25:
                            self.input.add('primary_fire')
                            if not self.spade_loop.running:
                                self.last_spade = current_time
                                self.spade_loop.start(0.25)
                        else:
                            self.spade_loop.stop()
                    else:
                        vel.x, vel.y = vel.x * 1.02, vel.y * 1.02
                        self.spade_loop.stop()
                    # creeper behavior
                    # if distance_to_aim < 16.0 and self.grenade_call is None:
                        # self.grenade_call = callLater(3.0, self.throw_grenade, 0.0)

            if self.steps and not self.next_step:
                self.next_step = self.steps.pop()
            if self.next_step:
                self.input.add('up')
                self.input.add('sprint')
                x, y = self.next_step
                to_next_step = self.to_next_step
                to_next_step.set(x - pos.x, y - pos.y, 0.0)
                
                # strafe if the target is in front of us
                dot = ori.dot(to_next_step)
                if dot > 0.7071 and dot < 0.98:
                    p_dot = ori.perp_dot(to_next_step)
                    self.input.add('left' if p_dot < 0.0 else 'right')
                else:
                    self.target_orientation.set_vector(to_next_step)
                    to_next_step = self.target_orientation
                dist_to_next_step = to_next_step.normalize()
                if dist_to_next_step < 0.065:
                    self.next_step = None
            
            # check whether we're failing to go anywhere
            self.last_pos -= pos
            self.last_pos.z = 0.0
            distance_moved = self.last_pos.length_sqr()
            self.last_pos.set_vector(pos)
            if distance_moved < 0.0066:
                self.input.discard('sprint')
                self.ticks_stumped += 1
                if self.ticks_stumped == TICKS_STUMPED_BEFORE_RETRYING:
                    self.path = None
                if self.ticks_stumped == TICKS_STUMPED_BEFORE_WIGGLING:
                    self.ticks_wiggling_remaining = 60
                if self.ticks_stumped == TICKS_STUMPED_BEFORE_KILL:
                    self.kill()
            else:
                self.ticks_stumped = 0
            
            if self.ticks_wiggling_remaining > 0:
                # alternate strafing left and right, it might get us out
                self.ticks_wiggling_remaining -= 1
                left = sin(self.ticks_wiggling_remaining * 0.1) < 0.0
                self.input.discard('right' if left else 'left')
                self.input.add('left' if left else 'right')
            
            # project position and check for obstacles
            #~ ori_z, ori.z = ori.z, 0.0
            #~ ret = player.world_object.cast_ray(PROXIMITY_RAY_LENGTH)
            #~ ori.z = ori_z
            #~ if ret:
                #~ x, y, z = ret
            x, y, z = int(pos.x + ori.x), int(pos.y + ori.y), pos.z + 2
            x_i, y_i = int(pos.x), int(pos.y)
            diagonal = sgn(x - x_i) and sgn(y - y_i)
            walled = (nav.is_wall(x, y, z) if not diagonal else
                nav.is_wall(x, y_i, z) and nav.is_wall(x_i, y, z))
            self.ticks_obstructed = self.ticks_obstructed + 1 if walled else 0
            
            if (self.ticks_obstructed >= TICKS_BEFORE_JUMPING and
                (nav.is_jumpable(x, y, z) if not diagonal else
                nav.is_jumpable(x, y_i, z) or nav.is_jumpable(x_i, y, z))):
                self.input.discard('sprint')
                self.input.add('jump')
                self.jumpable = x, y, z - 4
            # jump correction
            if self.jumpable is not None and fabs(self.jumpable[2] - pos.z) < 0.41 and sqrt(
                (self.jumpable[0] - pos.x)**2 + (self.jumpable[1] - pos.y)**2) <= 0.7071:
                self.world_object.set_position(pos.x, pos.y, self.jumpable[2])
                self.jumpable = None

            # orientate towards target
            diff = ori - self.target_orientation
            diff.z = 0.0
            diff = diff.length_sqr()
            if diff > 0.001:
                p_dot = ori.perp_dot(self.target_orientation)
                if p_dot > 0.0:
                    ori.rotate(self._turn_vector)
                else:
                    ori.unrotate(self._turn_vector)
                new_p_dot = ori.perp_dot(self.target_orientation)
                if new_p_dot * p_dot < 0.0:
                    ori.set_vector(self.target_orientation)
            else:
                ori.set_vector(self.target_orientation)
            
            if self.grenade_call:
                self.input.add('primary_fire')
            
            obj.set_orientation(*ori.get())
            self.flush_input()
        
        def flush_input(self):
            input = self.input
            world_object = self.world_object
            z_vel = world_object.velocity.z
            if 'jump' in input and z_vel < 0:
                input.discard('jump')
            input_changed = not (
                ('up' in input) == world_object.up and
                ('down' in input) == world_object.down and
                ('left' in input) == world_object.left and
                ('right' in input) == world_object.right and
                ('jump' in input) == world_object.jump and
                ('crouch' in input) == world_object.crouch and
                ('sneak' in input) == world_object.sneak and
                ('sprint' in input) == world_object.sprint)
            if input_changed:
                if not self.freeze_animation:
                    world_object.set_walk('up' in input, 'down' in input,
                        'left' in input, 'right' in input)
                    world_object.set_animation('jump' in input, 'crouch' in input,
                        'sneak' in input, 'sprint' in input)
                if (not self.filter_visibility_data and
                    not self.filter_animation_data):
                    input_data.player_id = self.player_id
                    input_data.up = world_object.up
                    input_data.down = world_object.down
                    input_data.left = world_object.left
                    input_data.right = world_object.right
                    input_data.jump = world_object.jump
                    input_data.crouch = world_object.crouch
                    input_data.sneak = world_object.sneak
                    input_data.sprint = world_object.sprint
                    self.protocol.send_contained(input_data)
            primary = 'primary_fire' in input
            secondary = 'secondary_fire' in input
            shoot_changed = not (
                primary == world_object.primary_fire and
                secondary == world_object.secondary_fire)
            if shoot_changed:
                if primary != world_object.primary_fire:
                    if self.tool == WEAPON_TOOL:
                        self.weapon_object.set_shoot(primary)
                    if self.tool == WEAPON_TOOL or self.tool == SPADE_TOOL:
                        self.on_shoot_set(primary)
                world_object.primary_fire = primary
                world_object.secondary_fire = secondary
                if not self.filter_visibility_data:
                    weapon_input.player_id = self.player_id
                    weapon_input.primary = primary
                    weapon_input.secondary = secondary
                    self.protocol.send_contained(weapon_input)
            input.clear()
        
        def set_tool(self, tool):
            if self.on_tool_set_attempt(tool) == False:
                return
            self.tool = tool
            if self.tool == WEAPON_TOOL:
                self.on_shoot_set(self.world_object.fire)
                self.weapon_object.set_shoot(self.world_object.fire)
            self.on_tool_changed(self.tool)
            if self.filter_visibility_data:
                return
            set_tool.player_id = self.player_id
            set_tool.value = self.tool
            self.protocol.send_contained(set_tool)
        
        def wield_spade(self):
            for player in self.protocol.players.values():
                if player.hp <= 0:
                    continue
                position1 = self.world_object.position
                position2 = player.world_object.position
                if not vector_collision(position1, position2, MELEE_DISTANCE):
                    continue
                valid_hit = self.world_object.validate_hit(player.world_object,
                    MELEE, HIT_TOLERANCE)
                if not valid_hit:
                    continue
                if player.team is not self.team:
                    speed = self.world_object.velocity.length()
                    hit_amount = 20 * speed + 15
                    type = MELEE_KILL
                    returned = self.on_hit(hit_amount, player, type, None)
                    if returned == False:
                        continue
                    elif returned is not None:
                        hit_amount = returned
                    player.hit(hit_amount, self, type)
                return

            loc = self.world_object.cast_ray(6) # 6 = MAX_DIG_DISTANCE
            if loc:
                map = self.protocol.map
                x, y, z = loc
                if z >= 62:
                    return
                if not map.get_solid(x, y, z):
                    return
                pos = position1
                if not collision_3d(pos.x, pos.y, pos.z, x, y, z, 6):
                    return
                value = DESTROY_BLOCK
                if self.on_block_destroy(x, y, z, value) == False:
                    return
                if map.destroy_point(x, y, z):
                    self.on_block_removed(x, y, z)
                block_action.x = x
                block_action.y = y
                block_action.z = z
                block_action.value = value
                block_action.player_id = self.player_id
                self.protocol.send_contained(block_action, save = True)
                self.protocol.update_entities()

        def throw_grenade(self, time_left):
            self.grenade_call = None
            if not self.hp or not self.grenades:
                return
            self.grenades -= 1
            if self.on_grenade(time_left) == False:
                return
            obj = self.world_object
            grenade = self.protocol.world.create_object(Grenade, time_left,
                obj.position, None, obj.orientation, self.grenade_exploded)
            grenade.team = self.team
            self.on_grenade_thrown(grenade)
            if self.filter_visibility_data:
                return
            grenade_packet.player_id = self.player_id
            grenade_packet.value = time_left
            grenade_packet.position = grenade.position.get()
            grenade_packet.velocity = grenade.velocity.get()
            self.protocol.send_contained(grenade_packet)

        def get_respawn_time(self):
            if not self.local:
                return connection.get_respawn_time(self)
            return 1

        def on_spawn_location(self, pos):
            if not self.local:
                return connection.on_spawn_location(self, pos)
            return pos[0], pos[1], pos[2] + 2

        def on_spawn(self, pos):
            if not self.local:
                return connection.on_spawn(self, pos)
            self.world_object.set_orientation(1.0, 0.0, 0.0)
            self.aim.set_vector(self.world_object.orientation)
            self.target_orientation.set_vector(self.aim)
            self.to_next_step.zero()
            self.last_pos.set(*pos)
            self.steps = []
            self.path = []
            self.plan = []
            self.next_step = None
            self.set_tool(SPADE_TOOL)
            self.input.add('jump')
            self.jumpable = None
            self.follow = None
            self.aim_at = None
            self.follow_was_local = True
            self.acquire_targets = True
            self.ticks_obstructed = 0
            self.ticks_stumped = 0
            self.ticks_wiggling_remaining = 0
            self.last_spade = 0
            connection.on_spawn(self, pos)

        def on_position_update(self):
            if not self.local:
                return connection.on_position_update(self)
            pos = self.world_object.position

            if self.protocol.game_mode == CTF_MODE:
                other_flag = self.team.other.flag
                if vector_collision(pos, self.team.base):
                    if other_flag.player is self:
                        self.capture_flag()
                if not other_flag.player and vector_collision(pos, other_flag):
                    self.take_flag()
                    # go home and ignore enemies
                    self.go_to(self.team.base.get())
                    self.acquire_targets = False
                    if self.grenade_call and self.grenade_call.active():
                        self.grenade_call.cancel()
                    self.grenade_call = None
            elif self.protocol.game_mode == TC_MODE:
                for entity in self.protocol.entities:
                    collides = vector_collision(entity, 
                        self.world_object.position, TC_CAPTURE_DISTANCE)
                    if self in entity.players:
                        if not collides:
                            entity.remove_player(self)
                    else:
                        if collides:
                            entity.add_player(self)
            return connection.on_position_update(self) 

        def on_flag_drop(self):
            for bot in self.protocol.bots:
                if (bot.team is self.team and not bot.follow and 
                    bot.acquire_targets):
                    bot.go_to(self.team.other.flag.get())
            connection.on_flag_drop(self)
        
        def on_hit(self, hit_amount, hit_player, type, grenade):
            vel = hit_player.world_object.velocity
            obj1 = self.world_object
            obj2 = hit_player.world_object

            if not self.local:
                direction = obj2.position - obj1.position
                direction.normalize()
                value = 0.0005
                if type == MELEE_KILL:
                    value = 0.005
                value *= hit_amount
                vel.x = direction.x * value
                vel.y = direction.y * value
                vel.z = direction.z * value
            elif self.local and (vel.z == 0 or 0.3 > vel.z > 0.15):
                if not hit_player.local:
                    hit_player.input = set()
                    hit_player.input.add('jump')
                    hit_player.flush_input()
                else:
                    hit_player.input.add('jump')
            if not self.local:
                if type == GRENADE_KILL:
                    hit_amount *= 0.3334 # 0.333'4', it's need
                else:
                    hit_amount *= 0.6667
                if type == MELEE_KILL:
                    return 20
                elif type == WEAPON_KILL or type == HEADSHOT_KILL:
                    if self.weapon == SHOTGUN_WEAPON:
                        hit_player.shotgun_amount += hit_amount
                        if hit_player.shotgun_amount >= 100:
                            return False
                    elif type == HEADSHOT_KILL and self.weapon == RIFLE_WEAPON:
                        if obj1.validate_hit(obj2, HEAD, 0.25):
                            hit_amount = 100
            return hit_amount

        def on_kill(self, killer, type, grenade):
            for bot in self.protocol.bots:
                if bot.follow is self:
                    bot.follow = None
                if bot.aim_at is self:
                    bot.aim_at = None
            if self.local:
                if self.grenade_call is not None:
                    self.grenade_call.cancel()
                    self.grenade_call = None
                self.spade_loop.stop()
                if self.plan_defer:
                    self.plan_defer.cancel()
                self.plan_defer = None
                if self.path_defer:
                    self.path_defer.cancel()
                self.path_defer = None
            return connection.on_kill(self, killer, type, grenade)
            
        def on_connect(self):
            if self.local:
                return connection.on_connect(self)
            max_players = min(32, self.protocol.max_players)
            protocol = self.protocol
            if len(protocol.connections) + len(protocol.bots) > max_players:
                protocol.bots[-1].disconnect()
            connection.on_connect(self)
        
        def on_disconnect(self):
            for bot in self.protocol.bots:
                if bot.follow is self:
                    bot.follow = None
                if bot.aim_at is self:
                    bot.aim_at = None
            connection.on_disconnect(self)

        def _send_connection_data(self):
            if self.local:
                if self.player_id is None:
                    self.player_id = self.protocol.player_ids.pop()
                return
            connection._send_connection_data(self)
        
        def send_map(self, data = None):
            if self.local:
                self.on_join()
                return
            connection.send_map(self, data)
        
        def timer_received(self, value):
            if self.local:
                return
            connection.timer_received(self, value)
        
        def send_loader(self, loader, ack = False, byte = 0):
            if self.local:
                return
            return connection.send_loader(self, loader, ack, byte)
    
    return BotProtocol, BotConnection