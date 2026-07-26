"""状态机：Idle / Walk / Drag / Fall / Land / Turn / Look / TurnBack
         / LieDown / Rest / RestPeek / Sleep / GetUp。

由 PetWindow 以固定 tick 驱动，负责状态切换与水平/垂直运动量计算；
窗口坐标的实际移动由 PetWindow 执行。
伪 3D 互动：idle 随机或双击触发 转身(turn) -> 注视观察者(look) -> 转回(turn_back)。
体力系统：走动消耗体力，累了趴下(rest)；趴卧时鼠标经过会抬头(rest_peek)，
单击起身行走；久趴不被打扰则入睡(sleep)，睡眠仅响应双击唤醒。
"""

from __future__ import annotations

import logging
import random
import time

log = logging.getLogger("lumia.state")

IDLE = "idle"
WALK = "walk"
DRAG = "drag"
FALL = "fall"
LAND = "land"
TURN = "turn"
LOOK = "look"
TURN_BACK = "turn_back"
LIE_DOWN = "lie_down"
REST = "rest"
REST_PEEK = "rest_peek"
SLEEP = "sleep"
GET_UP = "get_up"

GRAVITY = 2400.0        # px/s^2
MAX_FALL_SPEED = 1600.0 # px/s
WALK_SPEED = 60.0       # px/s
LAND_DURATION = 0.35    # 落地动画时长 s
TURN_DURATION = 0.375   # 转身动画时长 s（3 帧 @ 8FPS）
LOOK_MIN, LOOK_MAX = 2.5, 5.0    # 注视时长范围 s
LOOK_CHANCE = 0.28               # 待机计时到时选择转身看你的概率
WALK_CHANCE = 0.30               # 待机计时到时选择走动的概率（其余继续呆着）
WALK_MIN, WALK_MAX = 3.0, 8.0    # 单次走动时长范围 s
IDLE_MIN, IDLE_MAX = 5.0, 15.0   # 待机后尝试行为的间隔范围 s

# 体力系统：走动消耗，趴卧/睡眠恢复；节奏放缓，犯困晚、睡得久
WALK_DRAIN_T = 60.0     # 累计走动多少秒耗尽体力（走走停停，实际要好几分钟才犯困）
REST_RECOVER_T = 90.0   # 只趴着回满体力所需秒数（慢，促使入睡）
SLEEP_RECOVER_T = 90.0  # 睡着回满体力所需秒数
TIRED_ENERGY = 0.3      # 低于此值视为累了，待机到时趴下
WAKE_ENERGY = 0.95      # 体力恢复到此值自然起身
LIE_DURATION = 0.4      # 趴下过渡动画时长 s（3 帧 @ 8FPS）
GETUP_DURATION = 0.4    # 起身过渡动画时长 s
PEEK_HOLD = 1.6         # 抬头张望保持时长 s（鼠标再动则刷新）
SLEEP_AFTER_MIN, SLEEP_AFTER_MAX = 6.0, 12.0  # 趴卧不被打扰多久后入睡 s
SLEEP_MIN = 45.0        # 单次睡眠时长下限 s（未到时不自然醒，双击仍可唤醒）
SLEEP_MAX = 180.0       # 单次睡眠时长上限 s（到时自然醒）


class PetStateMachine:
    def __init__(self, walking_enabled: bool = True):
        self.state = IDLE
        self.facing_left = True
        self.walking_enabled = walking_enabled
        self.vy = 0.0  # 下落速度 px/s
        self.energy = 1.0  # 体力 0..1
        self._timer = 0.0
        self._next_walk_at = self._rand_idle()
        self._walk_until = 0.0
        self._look_until = 0.0
        self._rest_calm = 0.0    # 趴卧未被打扰的累计时长
        self._sleep_after = 0.0  # 本次趴卧多久后入睡
        # 导演强制：None | sleep | meal | sit_away
        self.director_mode: str | None = None
        self._anger_until: float = 0.0
        self._director_walk_boost = False

    @staticmethod
    def _rand_idle() -> float:
        return random.uniform(IDLE_MIN, IDLE_MAX)

    def _switch(self, state: str) -> None:
        if state != self.state:
            log.debug("状态切换: %s -> %s", self.state, state)
            self.state = state
            self._timer = 0.0

    # --- 外部事件 ---

    def set_director_mode(self, mode: str | None) -> None:
        """由大脑导演：sleep / meal / sit_away / None。"""
        mode = mode if mode in ("sleep", "meal", "sit_away") else None
        if mode == self.director_mode:
            return
        prev = self.director_mode
        self.director_mode = mode
        log.info("导演模式: %s -> %s", prev, mode)
        if mode == "sleep":
            self._switch(SLEEP)
        elif mode == "meal":
            # 用餐氛围：停下看人
            self._look_until = random.uniform(LOOK_MIN, LOOK_MAX)
            self._switch(LOOK)
        elif mode == "sit_away":
            self.walking_enabled = True
            self._director_walk_boost = True
            self.facing_left = random.random() < 0.5
            self._walk_until = 9999.0
            self._switch(WALK)
        elif mode is None and prev is not None:
            self._director_walk_boost = False
            if self.state in (SLEEP, WALK):
                self._switch(IDLE)
                self._next_walk_at = self._rand_idle()

    def anger_burst(self) -> None:
        """睡觉时段被点：短暂炸毛再睡回去（用 fall/闪电帧，避免和吃饭 look 冲突）。"""
        self._anger_until = time.monotonic() + 1.4
        self._timer = 0.0
        self._switch(FALL)

    def start_drag(self) -> None:
        if self.state == SLEEP or self.director_mode == "sleep":
            return  # 睡眠中不响应拖拽
        self.vy = 0.0
        self._switch(DRAG)

    def play_gesture(self) -> None:
        """双击互动：睡眠/趴卧时唤醒起身，否则转身面向观察者。"""
        if self.director_mode == "sleep":
            self.anger_burst()
            return
        if self.state in (SLEEP, REST, REST_PEEK, LIE_DOWN):
            self._switch(GET_UP)
        elif self.state in (IDLE, WALK):
            self._look_until = random.uniform(LOOK_MIN, LOOK_MAX)
            self._switch(TURN)

    def on_click(self) -> None:
        """单击：趴卧时起身行走；睡眠时忽略（仅双击可唤醒）。"""
        if self.director_mode == "sleep":
            self.anger_burst()
            return
        if self.state in (REST, REST_PEEK, LIE_DOWN):
            self._switch(GET_UP)

    def notify_mouse_move(self) -> None:
        """光标经过：趴卧时微微抬头张望（不打扰睡眠）。"""
        if self.state == REST:
            self._rest_calm = 0.0
            self._switch(REST_PEEK)
        elif self.state == REST_PEEK:
            self._rest_calm = 0.0
            self._timer = 0.0  # 延长抬头保持时间

    def end_drag(self, on_ground: bool) -> None:
        if on_ground:
            self._land()
        else:
            self.vy = 0.0
            self._switch(FALL)

    def _land(self) -> None:
        self.vy = 0.0
        self._switch(LAND)

    def _enter_rest(self) -> None:
        self._rest_calm = 0.0
        self._sleep_after = random.uniform(SLEEP_AFTER_MIN, SLEEP_AFTER_MAX)
        self._switch(REST)

    # --- 每帧推进，返回 (dx, dy) 期望位移（px）---

    def tick(self, dt: float, on_ground: bool, at_left_edge: bool, at_right_edge: bool) -> tuple[float, float]:
        self._timer += dt

        # 生气爆发：播完 fall 帧后回到睡觉导演
        if self._anger_until and time.monotonic() >= self._anger_until:
            self._anger_until = 0.0
            self.vy = 0.0
            if self.director_mode == "sleep":
                self._switch(SLEEP)
            else:
                self._switch(IDLE)
            return 0.0, 0.0

        # 体力结算：走动消耗，趴卧/睡眠恢复
        if self.state == WALK:
            self.energy = max(0.0, self.energy - dt / WALK_DRAIN_T)
        elif self.state in (LIE_DOWN, REST, REST_PEEK):
            self.energy = min(1.0, self.energy + dt / REST_RECOVER_T)
        elif self.state == SLEEP:
            self.energy = min(1.0, self.energy + dt / SLEEP_RECOVER_T)

        if self.state == DRAG:
            return 0.0, 0.0  # 拖拽中位置由鼠标控制

        # 生气动画：原地播 fall，不真下落
        if self._anger_until and self.state == FALL:
            return 0.0, 0.0

        # 非拖拽状态下悬空（如分辨率变化/任务栏位置变化）自动转下落
        if not on_ground and self.state not in (FALL,):
            self.vy = 0.0
            self._switch(FALL)

        if self.state == FALL:
            if on_ground:
                self._land()
                return 0.0, 0.0
            self.vy = min(self.vy + GRAVITY * dt, MAX_FALL_SPEED)
            return 0.0, self.vy * dt

        if self.state == LAND:
            if self._timer >= LAND_DURATION:
                if self.director_mode == "sleep":
                    self._switch(SLEEP)
                else:
                    self._switch(IDLE)
                    self._next_walk_at = self._rand_idle()
            return 0.0, 0.0

        # 转身看你链路：turn -> look -> turn_back -> idle（全程原地）
        if self.state == TURN:
            if self._timer >= TURN_DURATION:
                self._switch(LOOK)
            return 0.0, 0.0

        if self.state == LOOK:
            if self._timer >= self._look_until:
                if self.director_mode == "sleep":
                    self._switch(SLEEP)
                elif self.director_mode == "meal":
                    self._switch(IDLE)
                    self._next_walk_at = self._timer + self._rand_idle()
                else:
                    self._switch(TURN_BACK)
            return 0.0, 0.0

        if self.state == TURN_BACK:
            if self._timer >= TURN_DURATION:
                self._switch(IDLE)
                self._next_walk_at = self._rand_idle()
            return 0.0, 0.0

        # 疲劳休息链路：lie_down -> rest <-> rest_peek -> sleep -> get_up（全程原地）
        if self.state == LIE_DOWN:
            if self._timer >= LIE_DURATION:
                self._enter_rest()
            return 0.0, 0.0

        if self.state == REST:
            self._rest_calm += dt
            if self.energy >= WAKE_ENERGY:
                self._switch(GET_UP)
            elif self._rest_calm >= self._sleep_after:
                log.debug("趴卧 %.1fs 未被打扰，入睡", self._rest_calm)
                self._switch(SLEEP)
            return 0.0, 0.0

        if self.state == REST_PEEK:
            if self._timer >= PEEK_HOLD:
                self._enter_rest()  # 鼠标消停，低头继续趴卧
            return 0.0, 0.0

        if self.state == SLEEP:
            # 导演强制睡觉：不许自然醒
            if self.director_mode == "sleep":
                return 0.0, 0.0
            # 至少睡够 SLEEP_MIN 才允许自然醒，避免刚趴下就快速醒来
            if self._timer >= SLEEP_MAX or (self._timer >= SLEEP_MIN and self.energy >= 1.0):
                log.debug("睡饱了（体力 %.2f，睡了 %.0fs），自然醒来", self.energy, self._timer)
                self._switch(GET_UP)
            return 0.0, 0.0

        # 用餐氛围：周期性看人
        if self.director_mode == "meal" and self.state == IDLE:
            if self._timer >= self._next_walk_at:
                self._look_until = random.uniform(LOOK_MIN, LOOK_MAX)
                self._switch(LOOK)
            return 0.0, 0.0

        if self.state == GET_UP:
            if self._timer >= GETUP_DURATION:
                if self.walking_enabled:
                    self.facing_left = random.random() < 0.5
                    self._walk_until = random.uniform(WALK_MIN, WALK_MAX)
                    self._switch(WALK)
                else:
                    self._switch(IDLE)
                    self._next_walk_at = self._rand_idle()
            return 0.0, 0.0

        if self.state == IDLE:
            if self._timer >= self._next_walk_at:
                if self.energy < TIRED_ENERGY:
                    log.debug("体力不足（%.2f），趴下休息", self.energy)
                    self._switch(LIE_DOWN)
                    return 0.0, 0.0
                r = random.random()
                if r < LOOK_CHANCE:
                    self._look_until = random.uniform(LOOK_MIN, LOOK_MAX)
                    self._switch(TURN)
                elif r < LOOK_CHANCE + WALK_CHANCE and self.walking_enabled:
                    self.facing_left = random.random() < 0.5
                    self._walk_until = random.uniform(WALK_MIN, WALK_MAX)
                    self._switch(WALK)
                else:
                    self._next_walk_at = self._timer + self._rand_idle()
            return 0.0, 0.0

        if self.state == WALK:
            # 久坐走远：不因计时停走，边缘折返继续遛
            if self.director_mode != "sit_away":
                if not self.walking_enabled or self._timer >= self._walk_until:
                    self._switch(IDLE)
                    self._next_walk_at = self._rand_idle()
                    return 0.0, 0.0
            # 碰到屏幕边缘折返
            if at_left_edge and self.facing_left:
                self.facing_left = False
                log.debug("到达左边缘，折返")
            elif at_right_edge and not self.facing_left:
                self.facing_left = True
                log.debug("到达右边缘，折返")
            speed = WALK_SPEED * (1.7 if self._director_walk_boost else 1.0)
            dx = -speed * dt if self.facing_left else speed * dt
            return dx, 0.0

        return 0.0, 0.0
