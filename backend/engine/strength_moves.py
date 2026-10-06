"""
肌力課的動作自己挑 (SP-191) — docs/research/strength-session-design.md §3 (SP-121: the
interchangeable moves of each movement type, the easier / harder versions, the equipment, who each
one suits) and §3.8 (the design: one 課表偏好 per type, the default when nothing is picked); the
balance moves: baiyue-technical-terrain.md §3.2 (the stages of the 平衡小課, read here as easier /
harder versions of each of the block's four moves).

The athlete picks WHICH move a type uses. The phase still decides which types a session trains and
the sets × reps (engine/strength_plan.py, SP-119), and the balance block keeps its dose
(engine/balance_plan.py, SP-120). The first move of a type is its default: the move those two
modules named before, so with nothing picked every text stays as it was.

沒有的器材 (`lack`: bar 單槓 / band 彈力帶): a move that can't be done without it gives way to the
type's first move that can — pull-ups without a bar -> band rows, without a band either -> inverted
rows under a table. Every type has a move that needs neither (tested), so there is always one. The
other equipment of the doc (a box or a stair, a dumbbell) is only said in the option's text: the
default moves don't need a dumbbell, and the doc has no step-less eccentric move to fall back on.

The app doesn't diagnose (owner 2026-10-05: 不做個人化): the options are the same for everyone, the
texts say who each one suits (the doc's 「適合誰、怎麼換」) and the athlete chooses.

Not a choice: 高踏階 (a fixed station of SP-119's tables — the uphill single-leg push — though §3.1
lists it with the knee moves), 髖主導 (§3.3: no phase trains it), 下樓梯走 (owner: not a session),
俄羅斯扭轉 (§3.6: not recommended). 彈力帶划船 is the owner's own example of the bar-less pull
(推估: it isn't in §3.7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backend.i18n import N_, _

COACH, EST = N_("教練級"), N_("推估")          # the doc's marks: a coach / a textbook, or my extension
EQUIPMENT = {"bar": N_("單槓"), "band": N_("彈力帶")}


@dataclass(frozen=True)
class Move:
    key: str
    name: str                     # short: the session title and the picker say it
    gear: str                     # the doc's 器材 column
    who: str                      # 適合誰、怎麼換, in plain words
    grade: str = COACH
    level: int = 0                # against the type's default: −1 easier, +1 harder, 0 another way
    needs: Optional[str] = None   # the EQUIPMENT it can't be done without
    # its own wording where the stage's plain sets × reps don't fit (a hold, a carry, a load, a cue):
    # {aa | max | maint: msgid} (strength_plan), {block: msgid} (balance_plan)
    text: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Type:
    key: str
    group: str                    # strength | balance (the block at the end of the session)
    label: str
    tip: str                      # what it is for, which sessions use it
    moves: tuple                  # the default first; the order is also the fallback order


@dataclass(frozen=True)
class Pick:
    move: Move                    # what the session uses
    why: str = "default"          # default | picked (the athlete's) | swap (an equipment is missing)
    wanted: Optional[Move] = None  # swap: the move it stands in for


_BLOCK_TIP = N_("肌力課最後的平衡／腳踝段（下一場 A 賽是越野賽或百岳時才有）。平衡要越練越難才有效，穩了就換難一點的。")

TYPES = (
    Type("knee", "strength", N_("膝主導單腳"),
         N_("上坡往上推、下坡煞車用的單腳力量。基礎循環、最大肌力期和一般肌力課會練。"), (
        Move("split_squat", N_("分腿蹲"), N_("啞鈴、水壺或背包"),
             N_("大多數人從這個開始。重量抱在胸前比兩手垂著更練核心；拿在前腳的另一側，多練臀中肌。"),
             text={"max": N_("分腿蹲或後腳抬高蹲 3–4 組 × 3–6 下（留 2 下以上，組間休 2–3 分）")}),
        Move("bulgarian", N_("後腳抬高蹲"), N_("椅子或箱子，加重量"),
             N_("後腳放在椅子上的分腿蹲，前腳更吃力。分腿蹲加不了重量時再換；後腳腳背不舒服就回分腿蹲。"), level=1),
        Move("single_leg_squat", N_("單腳蹲"), N_("箱子或椅子、軟墊"),
             N_("單腳蹲下去坐到箱子再站起來。墊軟墊調深度；深度、次數一次只改一樣。雙腳蹲穩了再練。"), level=1),
        Move("iso_split_squat", N_("分腿蹲底部停住"), N_("不用器材（可加重量）"),
             N_("蹲到底停 30 秒。膝蓋前側不舒服，或跑山量很大、不想再加疲勞時改做。站不穩可以前腳踩矮箱、後腳踮腳尖。"),
             text={"aa": N_("分腿蹲底部停住 30 秒／腳"),
                   "max": N_("分腿蹲底部停住 3–4 組 × 30 秒／腳（組間休 2–3 分）")}),
        Move("goblet_squat", N_("高腳杯深蹲"), N_("啞鈴或水壺"),
             N_("雙腳蹲，重量抱在胸前。單腳還做不穩時先練這個；腳跟墊高，下背比較舒服。"), level=-1),
    )),
    Type("ecc", "strength", N_("離心（下坡）"),
         N_("下坡時大腿煞車的力量。基礎循環、最大肌力期和維持課會練；它是平日補強，不取代真的下坡。"), (
        Move("step_down", N_("離心下階"), N_("樓梯一階或板凳"),
             N_("站在箱上，一腳用 3 秒慢慢往下點地，再兩腳回去。箱子從 15 cm 加到 30 cm，之後再背包。"),
             text={"aa": N_("離心下階（箱 15–30 cm，3 秒慢慢往下點地）"),
                   "max": N_("離心下階 3 組 × 8–12 下／腳，背包 5 → 10 % 體重")}),
        Move("rear_raised_lunge", N_("後腳抬高前弓步"), N_("地墊或矮箱"),
             N_("後腳踩在墊高的地方，前腳往前落下、馬上停住：練快的煞車，比較像真的下坡。墊子從低的開始。"), level=1,
             text={"aa": N_("後腳抬高前弓步（前腳落下馬上停住）")}),
        Move("box_lunge", N_("箱上原地前弓步"), N_("箱子"),
             N_("站在箱上往前跨下去、落地停住，再回箱上。箱子越高衝擊越大，從低的開始；上一個做穩了再換這個。"), level=1),
        Move("kettlebell_swing", N_("壺鈴擺盪"), N_("壺鈴，或裝水的水桶"),
             N_("快速放下再甩上來。有壺鈴、會用髖部往後坐的人再選。"),
             text={"max": N_("壺鈴擺盪 3 組 × 8–12 下"), "maint": N_("壺鈴擺盪 8–10 下")}),
    )),
    Type("pull", "strength", N_("拉"),
         N_("拉繩、岩稜要拉得住自己。基礎循環、最大肌力期和維持課會練。"), (
        Move("pull_up", N_("引體向上"), N_("門框單槓"),
             N_("做不到就用彈力帶輔助，或跳上去用 3–5 秒慢慢放下來。不要一天做幾十下，手肘會發炎。"), needs="bar",
             text={"aa": N_("引體向上（做不到用彈力帶輔助，或跳上去 3–5 秒慢放）")}),
        Move("band_row", N_("彈力帶划船"), N_("彈力帶（綁在門把或柱子上）"),
             N_("沒有單槓時用：把彈力帶拉向肚子，兩邊肩胛往後夾。"), EST, level=-1, needs="band"),
        Move("inverted_row", N_("反式划船"), N_("穩的桌子或低槓"),
             N_("躺在桌子或低槓下面，把胸口拉上去。沒有單槓，或引體向上一下都做不到時用。"), EST, level=-1),
        Move("dumbbell_row", N_("單手啞鈴划船"), N_("啞鈴、椅子"),
             N_("一手扶椅子，另一手把啞鈴拉向腰。"), level=-1),
    )),
    Type("grip", "strength", N_("握、提"),
         N_("握力和軀幹。基礎循環，和有 ME 負重爬坡那一週的維持課會練。"), (
        Move("farmer_carry", N_("農夫走路"), N_("壺鈴、水桶或背包"),
             N_("兩手提重物走 30–40 公尺。只提一手，多練側邊的核心。"), EST,
             text={"aa": N_("農夫走路 30–40 m"), "maint": N_("農夫走路 2 × 30–40 m")}),
        Move("dead_hang", N_("懸吊"), N_("單槓"),
             N_("吊在單槓上不動，練握力。"), EST, needs="bar",
             text={"aa": N_("懸吊 20–40 秒"), "maint": N_("懸吊 2 × 20–40 秒")}),
    )),
    Type("core", "strength", N_("核心"),
         N_("軀幹穩住，手腳才出得了力。每一期的肌力課都有。"), (
        Move("plank", N_("棒式"), N_("不用器材"),
             N_("正面撐或側撐。撐不到 30 秒，代表軀幹還不夠。"),
             text={"aa": N_("棒式 30 秒或懸吊抬腿"), "max": N_("棒式 2 × 30 秒"), "maint": N_("棒式 30 秒")}),
        Move("pallof", N_("斜向推拉"), N_("彈力帶或壺鈴"),
             N_("身體不跟著轉。先跪姿、再弓步；每邊 8 下起，每週加 2 下，到 12 下換下一階。")),
        Move("plank_row", N_("平棒式划船"), N_("啞鈴"),
             N_("撐在棒式上，兩手輪流划船。先求身體不晃，不求重量。"), level=1),
        Move("dead_bug", N_("死蟲式"), N_("不用器材（可加彈力帶）"),
             N_("仰躺，對側的手腳慢慢伸出去再收回。下背不舒服時選這個。"), level=-1),
        Move("hanging_leg_raise", N_("懸吊抬腿"), N_("單槓"),
             N_("吊在單槓上抬腿，同時練握力。百岳、岩稜適合。"), level=1, needs="bar"),
    )),
    Type("glute", "strength", N_("臀中肌"),
         N_("單腳站的時候骨盆不掉、膝蓋不往內夾。一般肌力課（下一場 A 賽不是越野賽或百岳時）會練。"), (
        Move("clam", N_("蚌殼式"), N_("迷你彈力帶"),
             N_("側躺，膝蓋像蚌殼一樣打開。入門動作；不用硬打開，臀部側邊有出力就好。"), needs="band"),
        Move("side_leg_raise", N_("側躺抬腿"), N_("不用器材（可在腳踝套彈力帶）"),
             N_("抬到大約 45 度就好，慢慢上下；上面那隻腳的腳尖朝下。")),
        Move("standing_abduction", N_("站姿髖外展"), N_("彈力帶"),
             N_("站著把腿往旁邊抬；可以靠牆，骨盆不要歪。"), level=1, needs="band"),
        Move("monster_walk", N_("怪獸走路"), N_("彈力帶"),
             N_("彈力帶套在腿上，半蹲橫著走：用踩地那條腿把身體推出去，腳尖朝前。"), level=1, needs="band"),
    )),
    Type("stance", "balance", N_("單腳站"), _BLOCK_TIP, (
        Move("eyes", N_("單腳站（張眼 → 閉眼）"), N_("不用器材"),
             N_("先張眼，站穩了再閉眼。"), EST),
        Move("head_turn", N_("單腳站轉頭"), N_("不用器材"),
             N_("張著眼，頭慢慢左右轉。剛開始練、閉眼還站不穩時用。"), EST, level=-1),
        Move("soft", N_("軟墊上單腳站"), N_("折起來的瑜珈墊或枕頭"),
             N_("站在軟的東西上。平地閉眼能站 20 秒以上再換這個。"), EST, level=1),
        Move("soft_eyes_closed", N_("軟墊上閉眼單腳站"), N_("折起來的瑜珈墊或枕頭"),
             N_("最難的一種：軟墊加閉眼。旁邊要有東西可以扶。"), EST, level=1),
    )),
    Type("reach", "balance", N_("站穩伸出去"), _BLOCK_TIP, (
        Move("foot_reach", N_("伸腳點地"), N_("不用器材"),
             N_("單腳站，另一腳往前、往旁邊、往後伸出去點地。"), EST,
             text={"block": N_("單腳站、另一腳往前、側、後伸出去點地")}),
        Move("leg_swing", N_("另一腳前後擺"), N_("不用器材"),
             N_("單腳站，另一腳像鐘擺一樣前後擺。剛開始練時用。"), EST, level=-1,
             text={"block": N_("單腳站、另一腳前後擺")}),
        Move("hand_reach", N_("伸手碰三個方向"), N_("不用器材"),
             N_("單腳站，彎下去用手碰前面、左邊、右邊的地面。"), EST,
             text={"block": N_("單腳站、伸手往前、左、右三個方向碰地")}),
        Move("single_leg_deadlift", N_("單腳硬舉觸地"), N_("不用器材"),
             N_("單腳站，身體往前傾、手碰到地再站直；膝蓋不要往內夾。"), EST, level=1),
    )),
    Type("landing", "balance", N_("落地停住"), _BLOCK_TIP, (
        Move("hop", N_("單腳小跳落地"), N_("不用器材"),
             N_("單腳往前、往旁邊小跳，落地停住 2 秒。"), EST,
             text={"block": N_("單腳往前、側小跳，落地停住 2 秒")}),
        Move("step_stop", N_("前跨步落地"), N_("不用器材"),
             N_("不跳：往前跨一步，落地停住 2 秒。剛開始練，或腳踝還在恢復時用。"), EST, level=-1,
             text={"block": N_("往前跨一步，落地停住 2 秒")}),
        Move("drop_landing", N_("下階單腳落地"), N_("20 cm 左右的階梯或矮箱"),
             N_("從矮階往下跳，單腳落地停住。小跳落地不晃、膝蓋不往內夾了再做。"), EST, level=1,
             text={"block": N_("從 20 cm 的階往下跳，單腳落地停住 2 秒")}),
    )),
    Type("calf", "balance", N_("小腿、腳踝"), _BLOCK_TIP, (
        Move("calf_raise", N_("單腳提踵"), N_("樓梯一階（平地也可以）"),
             N_("踮起來，慢慢放下。練小腿、阿基里斯腱和足弓。"),
             text={"block": N_("單腳提踵慢放")}),
        Move("bent_knee_raise", N_("屈膝提踵"), N_("樓梯一階（平地也可以）"),
             N_("膝蓋微彎做提踵，練小腿深層的比目魚肌：長下坡、長時間跑的小腿耐力。"), EST,
             text={"block": N_("屈膝單腳提踵慢放")}),
        Move("toe_raise", N_("提腳尖"), N_("靠牆"),
             N_("背靠牆、腳跟著地，把腳尖抬起來再慢慢放下，練小腿前側。下坡時腳掌會拍地，或小腿前側容易痠的人用。"), EST,
             text={"block": N_("靠牆提腳尖慢放")}),
    )),
)
BY = {t.key: t for t in TYPES}


def clean_moves(v, strict: bool = False) -> tuple:
    """((type, move), …) sorted, of {type: move}: a type's default is left out (not picked = the
    default). An unknown type / move is dropped — a stored value from before a move was removed —
    or, `strict` (a new write), refused."""
    if not isinstance(v, dict):
        if strict and v is not None:
            raise ValueError(_("肌力動作的格式不對"))
        return ()
    out = []
    for k, m in v.items():
        t = BY.get(k)
        if t is None or not any(x.key == m for x in t.moves):
            if strict:
                raise ValueError(_("肌力動作沒有「{type}：{move}」這個選項", type=k, move=m))
            continue
        if m != t.moves[0].key:
            out.append((k, m))
    return tuple(sorted(out))


def clean_gear(v, strict: bool = False) -> tuple:
    """The 沒有的器材 keys, sorted; an unknown one is dropped or, `strict`, refused."""
    if not isinstance(v, (list, tuple)):
        if strict and v is not None:
            raise ValueError(_("沒有的器材要是 {choices} 其中幾個", choices=tuple(EQUIPMENT)))
        return ()
    if strict and any(x not in EQUIPMENT for x in v):
        raise ValueError(_("沒有的器材要是 {choices} 其中幾個", choices=tuple(EQUIPMENT)))
    return tuple(sorted({x for x in v if x in EQUIPMENT}))


def choice(prefs) -> dict:
    """{"moves": {type: move}, "lack": [equipment]} of a 課表偏好 (plan_prefs.Prefs, or None) — a plain
    dict for the week contexts (strength_plan / balance_plan); {} when nothing is picked."""
    moves = dict(getattr(prefs, "strength_moves", None) or ())
    lack = list(getattr(prefs, "strength_no_gear", None) or ())
    return {"moves": moves, "lack": lack} if moves or lack else {}


def resolve(ch: Optional[dict] = None) -> dict:
    """{type: Pick} of every type: the athlete's move, else the default; a move whose equipment is
    in 沒有的器材 gives way to the type's first move that needs none of them (why "swap")."""
    moves, lack = (ch or {}).get("moves") or {}, set((ch or {}).get("lack") or ())
    out = {}
    for t in TYPES:
        want = next((m for m in t.moves if m.key == moves.get(t.key)), t.moves[0])
        if want.needs in lack:
            sub = next((m for m in t.moves if m.needs not in lack), None)
            if sub is not None:
                out[t.key] = Pick(sub, "swap", want)
                continue
        out[t.key] = Pick(want, "default" if want is t.moves[0] else "picked")
    return out


def notes(picks: dict, types) -> list[str]:
    """What the session's text says about the moves of `types` that aren't the defaults: the
    athlete's own, and each one changed for a missing equipment (so the reason is on the page)."""
    out = []
    mine = [_(picks[t].move.name) for t in types if picks[t].why == "picked"]
    if mine:
        out.append(_("這堂用你挑的動作：{moves}", moves=_("、").join(mine)))
    for p in (picks[t] for t in types):
        if p.why == "swap":
            out.append(_("沒有{gear}：{old}改成{new}", gear=_(EQUIPMENT[p.wanted.needs]), old=_(p.wanted.name),
                         new=_(p.move.name)))
    return out


def sources(picks: dict, types) -> Optional[str]:
    """The source line of the moves of `types` that aren't the defaults (the doc's mark of each)."""
    got = [_("{name}（{grade}）", name=_(picks[t].move.name), grade=_(picks[t].move.grade))
           for t in types if picks[t].why != "default"]
    return _("換上的動作：{moves}", moves=_("、").join(got)) if got else None


def options() -> dict:
    """The picker of the 課表偏好 page: every type with its moves (the default first), and the
    equipment that can be marked as missing."""
    return {"types": [{"key": t.key, "group": t.group, "label": _(t.label), "tip": _(t.tip),
                       "default": t.moves[0].key,
                       "moves": [{"key": m.key, "name": _(m.name), "level": m.level, "needs": m.needs,
                                  "gear": _(m.gear), "who": _(m.who), "grade": _(m.grade)} for m in t.moves]}
                      for t in TYPES],
            "equipment": [{"key": k, "label": _(v)} for k, v in EQUIPMENT.items()]}
