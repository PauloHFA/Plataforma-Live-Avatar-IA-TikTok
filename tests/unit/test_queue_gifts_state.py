"""Testes unitários de fila de prioridades, matriz de presentes, estado e minijogos."""

from __future__ import annotations

import pytest

from services.models import (
    AvatarState,
    EventPayload,
    EventType,
    EventUser,
    GiftMatrixConfig,
    GiftPayload,
    GiftTierConfig,
    GiftTierId,
    IdleConfig,
    IdleTopic,
    LiveGoalConfig,
    NormalizedEvent,
    PriorityLevel,
)
from services.orchestrator.gifts import GiftReactionEngine
from services.orchestrator.minigames import ChatMinigameEngine
from services.orchestrator.queue import EventPriorityQueue
from services.orchestrator.state import AvatarStateMachine, IdleTopicRotator, SessionMemory


@pytest.mark.unit
def test_priority_queue_ordering_ttl_batch_and_ratelimit():
    q = EventPriorityQueue(max_size=50, user_cooldown_seconds=35.0, p2_ttl_seconds=20.0)
    u1 = EventUser(id="u1", nick="Ana")
    u2 = EventUser(id="u2", nick="Beto")
    u3 = EventUser(id="u3", nick="Carla")

    chat = NormalizedEvent(type=EventType.CHAT, user=u1, payload=EventPayload(text="Legal a live"))
    mention = NormalizedEvent(type=EventType.CHAT, user=u2, payload=EventPayload(text="Luna, oi?"))
    gift = NormalizedEvent(
        type=EventType.GIFT,
        user=u3,
        payload=EventPayload(gift=GiftPayload(name="Rose", coins=1)),
    )
    follow = NormalizedEvent(type=EventType.FOLLOW, user=u1)

    assert q.push(chat, now=100.0) is True
    # Mesmo usuário em cooldown é descartado
    assert q.push(chat, now=105.0) is False
    assert q.push(mention, now=101.0) is True
    assert q.push(gift, now=102.0) is True
    assert q.push(follow, now=103.0) is True

    # P1 (gift) sai antes de P2 (mention), que sai antes de P4 (chat)
    first = q.pop(now=104.0)
    second = q.pop(now=104.0)
    third = q.pop(now=104.0)
    assert first is not None and first.priority == PriorityLevel.P1_GIFT
    assert second is not None and second.priority == PriorityLevel.P2_MENTION_QUESTION
    assert third is not None and third.priority == PriorityLevel.P4_CHAT

    # P2 expirado (>20s) é descartado
    expired_q = NormalizedEvent(
        type=EventType.CHAT, user=u2, payload=EventPayload(text="Qual seu nome?")
    )
    q.push(expired_q, now=200.0)
    assert q.pop(now=225.0) is None

    # Lote P3
    batch = q.pop_follow_share_batch(force=True, now=225.0)
    assert len(batch) == 1


@pytest.mark.unit
def test_queue_preserves_gifts_under_overflow():
    q = EventPriorityQueue(max_size=5, user_cooldown_seconds=0.0)
    gift = NormalizedEvent(
        type=EventType.GIFT,
        user=EventUser(id="doador", nick="Doador"),
        payload=EventPayload(gift=GiftPayload(name="Lion", coins=1000)),
    )
    q.push(gift, is_donor=True, now=1.0)
    for i in range(10):
        q.push(
            NormalizedEvent(
                type=EventType.CHAT,
                user=EventUser(id=f"u{i}", nick=f"User{i}"),
                payload=EventPayload(text=f"Oi numero {i}"),
            ),
            now=2.0 + i,
        )
    assert q.size == 5
    top = q.pop(now=20.0)
    assert top is not None and top.event.type == EventType.GIFT


@pytest.mark.unit
def test_gift_matrix_tiers_combos_and_goal():
    matrix = GiftMatrixConfig(
        tiers=[
            GiftTierConfig(
                id=GiftTierId.T1,
                name="Pequeno",
                min_coins=1,
                max_coins=9,
                fx="p_light",
                sound="chime",
                gesture="wave",
                use_llm=False,
                cache_template="Valeu {gift}, {user}!",
            ),
            GiftTierConfig(
                id=GiftTierId.T4,
                name="Epico",
                min_coins=1000,
                max_coins=9999,
                fx="epic",
                sound="celeb",
                gesture="celebrate",
                interrupt=True,
                hall_of_live=True,
            ),
        ],
        combo_steps=[5, 10, 50],
        live_goal=LiveGoalConfig(title="Meta", target_coins=1000),
        custom_gifts={"Rose": {"fx": "rose_petals", "gesture": "heart"}},
    )
    engine = GiftReactionEngine(matrix)

    r1 = engine.evaluate(
        user_nick="Ana",
        gift=GiftPayload(name="Rose", coins=5, repeat=5),
        live_total_coins=5,
    )
    assert r1.tier.id == GiftTierId.T1
    assert r1.fx == "rose_petals"
    assert r1.combo_milestone == 5
    assert r1.cached_speech == "Valeu Rose, Ana!"
    assert r1.goal_reached is False

    r4 = engine.evaluate(
        user_nick="Joao",
        gift=GiftPayload(name="Galaxy", coins=1000, repeat=1),
        live_total_coins=1005,
    )
    assert r4.tier.id == GiftTierId.T4
    assert r4.tier.interrupt is True
    assert r4.goal_reached is True


@pytest.mark.unit
def test_session_memory_idle_rotator_and_budget():
    mem = SessionMemory()
    mem.add_exchange("Maria", "Oi", "Oi Maria, tudo bem por aí?")
    assert mem.is_repetitive("Oi Maria, tudo bem por aí?") is True
    assert mem.is_repetitive("Qual jogo você recomenda hoje?") is False

    mem.record_gift(user_id="1", nick="A", gift_name="Rose", coins=50, repeat=1, tier=2)
    mem.record_gift(
        user_id="2", nick="B", gift_name="Galaxy", coins=1000, repeat=1, tier=4, hall_of_live=True
    )
    top = mem.top_donors()
    assert top[0]["nick"] == "B"
    assert len(mem.hall_of_live) == 1
    assert "1050 moedas" in mem.refresh_summary()

    rotator = IdleTopicRotator(
        IdleConfig(
            topics=[
                IdleTopic(id="t1", weight=1, text="Tema 1"),
                IdleTopic(id="t2", weight=1, text="Tema 2"),
                IdleTopic(id="t3", weight=1, text="Tema 3"),
            ]
        )
    )
    picks = [rotator.next_topic().id for _ in range(3)]
    assert len(set(picks)) == 3

    sm = AvatarStateMachine(daily_budget_usd=1.0)
    assert sm.add_cost(0.85) == (True, False)
    assert sm.add_cost(0.20) == (False, True)
    assert sm.economy_mode is True
    sm.engage_kill_switch()
    assert sm.state == AvatarState.SAFE_MODE
    sm.disengage_kill_switch()
    assert sm.state == AvatarState.IDLE


@pytest.mark.unit
def test_minigames_commands():
    mg = ChatMinigameEngine(cooldown_seconds=10.0)
    res1 = mg.handle_command(user_id="u1", user_nick="Leo", text="!danca", now=10.0)
    assert res1.handled is True and res1.action == "dance"
    # Cooldown bloqueia repetição imediata
    res_cd = mg.handle_command(user_id="u1", user_nick="Leo", text="!cor azul", now=12.0)
    assert res_cd.handled is True and res_cd.color_hex is None

    res2 = mg.handle_command(user_id="u2", user_nick="Bia", text="!cor roxo", now=15.0)
    assert res2.handled is True and res2.color_hex == "#8b5cf6"

    res3 = mg.handle_command(user_id="u3", user_nick="Gui", text="!votar a", now=16.0)
    assert res3.handled is True and res3.poll_snapshot is not None
