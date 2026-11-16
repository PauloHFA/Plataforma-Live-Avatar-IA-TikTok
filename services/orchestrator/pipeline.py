"""Orquestrador principal: conecta ingestão, fila, moderação, LLM, TTS, Godot e SQLite."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any

from services.ingest.base import LiveSource
from services.models import (
    AvatarState,
    Emotion,
    EventType,
    GiftFXCommand,
    GodotInboundMessage,
    NormalizedEvent,
    OverlayCommand,
    PriorityLevel,
    SafeModeCommand,
    SceneCommand,
    SpeakCommand,
    StopSpeechCommand,
    new_event_id,
)
from services.orchestrator.brain.llm_client import (
    BrainEngine,
    LLMClient,
    OpenAICompatibleLLMClient,
    RuleMockLLMClient,
)
from services.orchestrator.config import (
    Settings,
    load_banned_terms,
    load_gift_matrix,
    load_idle_config,
    load_persona_profile,
)
from services.orchestrator.gifts import GiftReactionEngine
from services.orchestrator.metrics import AlertDispatcher, MetricsRegistry
from services.orchestrator.minigames import ChatMinigameEngine
from services.orchestrator.moderation.classifier import ModerationPipeline
from services.orchestrator.obs_control import OBSController
from services.orchestrator.queue import EventPriorityQueue
from services.orchestrator.state import AvatarStateMachine, IdleTopicRotator, SessionMemory
from services.orchestrator.storage import AuditRepository
from services.orchestrator.voice.cache import AudioCache
from services.orchestrator.voice.tts_client import (
    AzureSpeechTTSClient,
    MockSineTTSClient,
    TTSClient,
    VoiceService,
)
from services.orchestrator.ws_server import AvatarWebSocketServer

logger = logging.getLogger(__name__)


class OrchestratorService:
    """Coordena o pipeline completo de eventos até fala e animação na Godot."""

    def __init__(
        self,
        settings: Settings,
        *,
        source: LiveSource | None = None,
        llm_client: LLMClient | None = None,
        tts_client: TTSClient | None = None,
        ws_server: AvatarWebSocketServer | None = None,
        obs_controller: OBSController | None = None,
    ) -> None:
        self.settings = settings
        self.session_id = new_event_id("sess")

        self.persona = load_persona_profile(settings.persona_path)
        self.gift_matrix = load_gift_matrix(settings.gifts_path)
        self.idle_config = load_idle_config(settings.idle_topics_path)
        banned_terms = load_banned_terms(settings.banned_terms_path)

        self.source = source
        self.queue = EventPriorityQueue(mention_phrases=self.persona.mention_phrases)
        self.state_machine = AvatarStateMachine(daily_budget_usd=settings.daily_budget_usd)
        self.state_machine.scene = self.persona.default_scene
        self.memory = SessionMemory()
        self.idle_rotator = IdleTopicRotator(self.idle_config)
        self.gift_engine = GiftReactionEngine(self.gift_matrix)
        self.minigames = ChatMinigameEngine()
        self.moderation = ModerationPipeline(
            banned_terms=banned_terms,
            api_url=settings.moderation_api_url,
            api_key=settings.moderation_api_key,
            enabled=settings.moderation_enabled,
        )

        resolved_llm = llm_client or self._build_llm_client()
        self.brain = BrainEngine(
            persona=self.persona,
            client=resolved_llm,
            model_fast=settings.llm_model_fast,
            model_strong=settings.llm_model_strong,
            timeout_seconds=settings.llm_timeout_seconds,
        )

        resolved_tts = tts_client or self._build_tts_client()
        self.voice = VoiceService(
            client=resolved_tts,
            cache=AudioCache(settings.tts_cache_dir),
            voice=settings.tts_voice,
            timeout_seconds=settings.tts_timeout_seconds,
        )

        self.audit = AuditRepository(settings.sqlite_path)
        self.ws_server = ws_server or AvatarWebSocketServer(
            host=settings.ws_host, port=settings.ws_port
        )
        self.obs = obs_controller or OBSController(
            ws_url=settings.obs_ws_url,
            password=settings.obs_ws_password,
            contingency_scene=settings.obs_contingency_scene,
        )
        self.metrics = MetricsRegistry()
        self.alert_dispatcher = AlertDispatcher(settings.alert_webhook_url)

        self.live_feed: deque[dict[str, Any]] = deque(maxlen=100)
        self.vetoed_utterances: set[str] = set()
        self.peak_viewers = 0
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []
        self._speech_lock = asyncio.Lock()
        self._speech_done_event = asyncio.Event()
        self._speech_done_event.set()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _build_llm_client(self) -> LLMClient:
        if self.settings.llm_api_key and self.settings.llm_api_key != "[REDACTED]":
            return OpenAICompatibleLLMClient(
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key,
            )
        return RuleMockLLMClient()

    def _build_tts_client(self) -> TTSClient:
        if (
            self.settings.tts_provider == "azure"
            and self.settings.tts_api_key
            and self.settings.tts_api_key != "[REDACTED]"
        ):
            return AzureSpeechTTSClient(
                api_key=self.settings.tts_api_key,
                region=self.settings.tts_region,
            )
        return MockSineTTSClient()

    async def start(self, *, start_ws: bool = True, start_source: bool = True) -> None:
        await self.audit.initialize()
        await self.audit.purge_expired(self.settings.retention_days)
        await self.audit.start_session(self.session_id)

        await self.voice.prewarm_phrases(
            [*self.persona.greetings_cache, *self.persona.fallback_lines]
        )

        self.ws_server.on_message(self._on_godot_message)
        if start_ws:
            await self.ws_server.start()

        # Envia selo de IA e meta inicial para Godot
        await self.ws_server.broadcast(
            OverlayCommand(type="ai_badge", data={"label": "Avatar de IA", "visible": True})
        )

        if self.source is not None:
            self.source.on_event(self.ingest_event)
            self.source.on_alert(self._on_source_alert)
            if start_source:
                self._tasks.append(asyncio.create_task(self.source.run_with_reconnect()))

        self._running = True
        self._tasks.append(asyncio.create_task(self._worker_loop()))
        self._tasks.append(asyncio.create_task(self._idle_and_maintenance_loop()))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

        if self.source is not None:
            await self.source.close()
        await self.ws_server.stop()
        await self.audit.end_session(
            self.session_id,
            peak_viewers=self.peak_viewers,
            total_coins=self.memory.total_coins,
            total_events=self.metrics.total_events,
        )
        await self.audit.close()

    def _notify_alert(self, msg: str) -> None:
        self.metrics.push_alert(msg)
        if self.alert_dispatcher.webhook_url:
            try:
                task = asyncio.create_task(self.alert_dispatcher.send(msg))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            except RuntimeError:
                pass

    async def _on_source_alert(self, msg: str) -> None:
        self._notify_alert(msg)

    async def _on_godot_message(self, msg: GodotInboundMessage) -> None:
        if msg.type == "heartbeat":
            if msg.fps is not None:
                self.metrics.godot_fps = msg.fps
            if msg.memory_mb is not None:
                self.metrics.godot_memory_mb = msg.memory_mb
        elif msg.type == "speech_finished":
            if (
                msg.id == self.state_machine.current_utterance_id
                and not self.state_machine.safe_mode
            ):
                self.state_machine.current_utterance_id = None
                self.state_machine.transition(AvatarState.IDLE)
                self._speech_done_event.set()
        elif msg.type == "error":
            self._notify_alert(f"Erro Godot ({msg.code}): {msg.context}")

    async def ingest_event(self, event: NormalizedEvent) -> None:
        """Ponto único de entrada de eventos normalizados (usado pela LiveSource e testes)."""
        self.metrics.total_events += 1
        self.queue.record_arrival()
        changed, rate = self.queue.evaluate_raid_protection()
        if changed:
            if self.queue.slow_mode:
                self._notify_alert(f"Modo slow ativado automaticamente ({rate:.1f} eventos/s)")
            else:
                self._notify_alert(f"Modo slow desativado ({rate:.1f} eventos/s)")
        if event.type == EventType.VIEWER_COUNT and event.payload.viewer_count is not None:
            self.peak_viewers = max(self.peak_viewers, event.payload.viewer_count)
            await self.audit.log_event(event, "viewer_count")
            return

        if event.type == EventType.GIFT and event.payload.gift is not None:
            await self._handle_gift_fast_path(event)
            return

        is_donor = self.memory.is_donor(event.user.id)
        accepted = self.queue.push(event, is_donor=is_donor)
        if not accepted:
            await self.audit.log_event(event, "rate_limited_or_dropped")

    async def _handle_gift_fast_path(self, event: NormalizedEvent) -> None:
        """Seção 5.2: Reação visual/sonora imediata (<=1.5s), fala personalizada em seguida."""
        gift = event.payload.gift
        assert gift is not None
        provisional_tier = self.gift_engine.resolve_tier(gift.coins)
        total_user_coins = self.memory.record_gift(
            user_id=event.user.id,
            nick=event.user.nick,
            gift_name=gift.name,
            coins=gift.coins,
            repeat=gift.repeat,
            tier=provisional_tier.tier_number,
            hall_of_live=provisional_tier.hall_of_live,
        )
        reaction = self.gift_engine.evaluate(
            user_nick=event.user.nick,
            gift=gift,
            live_total_coins=self.memory.total_coins,
        )
        await self.audit.upsert_user(
            user_id=event.user.id,
            nick=event.user.nick,
            coins_delta=gift.coins,
        )

        # Caminho rápido: envia efeito visual e overlays imediatamente
        self.state_machine.transition(AvatarState.REACTING)
        await self.ws_server.broadcast(
            GiftFXCommand(
                gift=gift.name,
                tier=reaction.tier.tier_number,
                repeat=gift.repeat,
                user=event.user.nick,
                fx=reaction.fx,
                sound=reaction.sound,
                gesture=reaction.gesture,
            )
        )
        await self.ws_server.broadcast(
            OverlayCommand(type="recent_gifts", data=list(self.memory.recent_gifts))
        )
        await self.ws_server.broadcast(
            OverlayCommand(type="top_donors", data=self.memory.top_donors())
        )
        await self.ws_server.broadcast(
            OverlayCommand(
                type="goal",
                data={
                    "title": self.gift_matrix.live_goal.title,
                    "current": self.memory.total_coins,
                    "target": self.gift_matrix.live_goal.target_coins,
                },
            )
        )
        if reaction.tier.alert_operator:
            self.metrics.push_alert(
                f"Presente Lendário T5 recebido de {event.user.nick}: "
                f"{gift.name} ({gift.coins} moedas)"
            )
        if reaction.tier.hall_of_live:
            await self.ws_server.broadcast(
                OverlayCommand(type="hall_of_live", data=self.memory.hall_of_live[-10:])
            )

        # Enfileira agradecimento falado com prioridade P1
        event.payload.extra = {
            "total_user_coins": total_user_coins,
            "tier": reaction.tier.tier_number,
            "use_llm": reaction.tier.use_llm,
            "cached_speech": reaction.cached_speech or "",
            "interrupt": reaction.tier.interrupt,
            "combo_milestone": reaction.combo_milestone,
        }
        self.queue.push(event, is_donor=True)

    async def process_next_event(self) -> bool:
        """Processa um único evento da fila (exposto para testes determinísticos)."""
        if self.state_machine.paused or self.state_machine.safe_mode:
            return False

        # Flush de lote follow/share (P3)
        batch = self.queue.pop_follow_share_batch()
        if batch:
            names = ", ".join(dict.fromkeys(e.user.nick for e in batch[:5]))
            text = f"Obrigada pelo carinho e apoio na live, {names}!"
            await self._speak_direct(
                text=text,
                emotion=Emotion.ALEGRE,
                gesture="wave",
                priority=int(PriorityLevel.P3_FOLLOW_SHARE),
                event_id=batch[0].id,
            )
            for ev in batch:
                await self.audit.log_event(ev, "batched_p3")
            return True

        item = self.queue.pop()
        if item is None:
            return False

        ev = item.event
        started = time.perf_counter()

        if ev.type == EventType.CHAT:
            raw_text = (ev.payload.text or "").strip()
            decision = await self.moderation.check_input(ev.user.id, raw_text)
            await self.audit.upsert_user(user_id=ev.user.id, nick=ev.user.nick)
            mod_label = "allowed" if decision.allowed else "blocked"
            await self.audit.log_event(
                ev,
                f"{mod_label}:{decision.layer}:{decision.reason or ''}",
            )
            self._append_feed(ev, decision.allowed, decision.reason)
            if not decision.allowed:
                self.metrics.moderated_blocked += 1
                return True

            clean_text = decision.sanitized_text or raw_text

            # Verifica minijogo/comando (!danca, !cor, !votar)
            mg = self.minigames.handle_command(
                user_id=ev.user.id,
                user_nick=ev.user.nick,
                text=clean_text,
            )
            if mg.handled:
                if mg.action or mg.color_hex:
                    await self.ws_server.broadcast(
                        SceneCommand(action=mg.action, color_hex=mg.color_hex)
                    )
                if mg.poll_snapshot is not None:
                    await self.ws_server.broadcast(
                        OverlayCommand(type="poll", data=mg.poll_snapshot)
                    )
                if mg.speech:
                    await self._speak_direct(
                        text=mg.speech,
                        emotion=Emotion.ALEGRE,
                        gesture="dance" if mg.action == "dance" else "wave",
                        priority=item.priority,
                        event_id=ev.id,
                    )
                self.queue.mark_user_replied(ev.user.id)
                return True

            self.state_machine.transition(AvatarState.THINKING)
            brain_res = await self.brain.respond(
                event_kind="chat",
                user_nick=ev.user.nick,
                content=clean_text,
                session_summary=self.memory.session_summary,
                recent_exchanges=list(self.memory.recent_exchanges),
                use_strong_model=(item.priority == PriorityLevel.P2_MENTION_QUESTION),
                economy_mode=self.state_machine.economy_mode,
            )
            self.metrics.record_llm(failed=brain_res.used_fallback)
            warn, cutoff = self.state_machine.add_cost(brain_res.estimated_cost_usd)
            if warn:
                self.metrics.push_alert("Orçamento diário atingiu 80%!")
            if cutoff:
                self.metrics.push_alert("Orçamento diário atingiu 100%: modo econômico ativado!")

            out_check = await self.moderation.check_output(brain_res.output.fala)
            final_text = (
                out_check.sanitized_text
                if out_check.allowed and out_check.sanitized_text
                else self.brain.pick_fallback().fala
            )
            if self.memory.is_repetitive(final_text):
                final_text = f"{ev.user.nick}, {self.idle_rotator.next_topic().text}"

            await self._deliver_speech(
                text=final_text,
                emotion=brain_res.output.emocao,
                gesture=brain_res.output.gesto,
                priority=item.priority,
                event_id=ev.id,
                llm_ms=brain_res.latency_ms,
                cost_usd=brain_res.estimated_cost_usd,
                started_perf=started,
                interruptible=True,
            )
            self.memory.add_exchange(ev.user.nick, clean_text, final_text)
            self.queue.mark_user_replied(ev.user.id)
            return True

        if ev.type == EventType.GIFT and ev.payload.gift is not None:
            extra = ev.payload.extra
            await self.audit.log_event(ev, f"gift_tier_{extra.get('tier', 1)}")
            self._append_feed(ev, True, f"gift:{ev.payload.gift.name}")

            if not extra.get("use_llm", True) and extra.get("cached_speech"):
                await self._deliver_speech(
                    text=str(extra["cached_speech"]),
                    emotion=Emotion.ALEGRE,
                    gesture="wave",
                    priority=int(PriorityLevel.P1_GIFT),
                    event_id=ev.id,
                    llm_ms=0.0,
                    cost_usd=0.0,
                    started_perf=started,
                    interruptible=not bool(extra.get("interrupt", False)),
                )
                return True

            gift = ev.payload.gift
            milestone = extra.get("combo_milestone")
            combo_txt = f" (combo x{milestone})" if milestone else ""
            prompt_content = (
                f"Enviou presente {gift.name} x{gift.repeat} ({gift.coins} moedas){combo_txt}. "
                f"Total do usuário hoje: {extra.get('total_user_coins', gift.coins)} moedas."
            )
            brain_res = await self.brain.respond(
                event_kind="gift",
                user_nick=ev.user.nick,
                content=prompt_content,
                session_summary=self.memory.session_summary,
                recent_exchanges=list(self.memory.recent_exchanges),
                use_strong_model=int(extra.get("tier", 1)) >= 3,
                economy_mode=self.state_machine.economy_mode,
            )
            self.metrics.record_llm(failed=brain_res.used_fallback)
            self.state_machine.add_cost(brain_res.estimated_cost_usd)

            out_check = await self.moderation.check_output(brain_res.output.fala)
            final_text = (
                out_check.sanitized_text
                if out_check.allowed and out_check.sanitized_text
                else f"Muito obrigada pelo {gift.name}, {ev.user.nick}!"
            )
            await self._deliver_speech(
                text=final_text,
                emotion=brain_res.output.emocao,
                gesture=brain_res.output.gesto,
                priority=int(PriorityLevel.P1_GIFT),
                event_id=ev.id,
                llm_ms=brain_res.latency_ms,
                cost_usd=brain_res.estimated_cost_usd,
                started_perf=started,
                interruptible=not bool(extra.get("interrupt", False)),
            )
            self.memory.add_exchange(ev.user.nick, prompt_content, final_text)
            return True

        await self.audit.log_event(ev, "processed")
        return True

    async def _deliver_speech(
        self,
        *,
        text: str,
        emotion: Emotion,
        gesture: str,
        priority: int,
        event_id: str | None,
        llm_ms: float,
        cost_usd: float,
        started_perf: float,
        interruptible: bool = True,
    ) -> None:
        if self.state_machine.safe_mode or self.state_machine.paused:
            return
        utterance_id = new_event_id("act")
        if utterance_id in self.vetoed_utterances:
            return

        tts_res = await self.voice.synthesize_speech(text, emotion=emotion)
        self.metrics.record_tts(failed=tts_res.used_fallback)

        if self.state_machine.safe_mode or self.state_machine.paused:
            return

        # Aguarda fala anterior terminar, a menos que este comando interrompa (ex: T4/T5)
        if not interruptible and self.state_machine.current_utterance_id is not None:
            await self.ws_server.broadcast(StopSpeechCommand(reason="urgent_gift_interrupt"))
            self._speech_done_event.set()
        elif self.ws_server.client_count > 0 and not self._speech_done_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._speech_done_event.wait(), timeout=8.0)

        async with self._speech_lock:
            self.state_machine.current_utterance_id = utterance_id
            self.state_machine.transition(AvatarState.SPEAKING)
            if self.ws_server.client_count > 0:
                self._speech_done_event.clear()
            cmd = SpeakCommand(
                id=utterance_id,
                priority=priority,
                text=text,
                audio_path=str(tts_res.audio_path),
                visemes=tts_res.visemes,
                emotion=emotion.to_godot(),
                gesture=gesture,
                caption=True,
                interruptible=interruptible,
            )
            await self.ws_server.broadcast(cmd)

        total_ms = (time.perf_counter() - started_perf) * 1000.0
        self.metrics.record_latency(total_ms)
        await self.audit.log_utterance(
            utterance_id=utterance_id,
            event_id=event_id,
            text=text,
            emotion=emotion.value,
            llm_ms=llm_ms,
            tts_ms=tts_res.latency_ms,
            total_ms=total_ms,
            cost_estimate=cost_usd,
        )

    async def _speak_direct(
        self,
        *,
        text: str,
        emotion: Emotion,
        gesture: str,
        priority: int,
        event_id: str | None,
    ) -> None:
        await self._deliver_speech(
            text=text,
            emotion=emotion,
            gesture=gesture,
            priority=priority,
            event_id=event_id,
            llm_ms=0.0,
            cost_usd=0.0,
            started_perf=time.perf_counter(),
        )

    def _append_feed(self, ev: NormalizedEvent, allowed: bool, note: str | None) -> None:
        self.live_feed.appendleft(
            {
                "id": ev.id,
                "ts": ev.ts.isoformat(),
                "type": ev.type.value,
                "user": ev.user.nick,
                "user_id": ev.user.id,
                "text": ev.payload.text or (ev.payload.gift.name if ev.payload.gift else ""),
                "allowed": allowed,
                "note": note,
            }
        )

    # ---- Controles do Operador (RF-15, CA-12, CA-18) ----

    async def activate_kill_switch(self, reason: str = "operator_kill_switch") -> None:
        self.state_machine.engage_kill_switch()
        self.queue.clear_pending_speech_items()
        self._speech_done_event.set()
        await self.ws_server.broadcast(SafeModeCommand(on=True, reason=reason))
        self.obs.switch_to_contingency()
        self._notify_alert(f"Kill switch ativado: {reason}")

    async def veto_utterance(self, utterance_id: str) -> int:
        self.vetoed_utterances.add(utterance_id)
        cleared = self.queue.clear_pending_speech_items()
        if utterance_id == "current" or utterance_id == self.state_machine.current_utterance_id:
            self.state_machine.current_utterance_id = None
            self._speech_done_event.set()
            await self.ws_server.broadcast(StopSpeechCommand(reason="operator_veto"))
        return cleared

    async def resume_from_safe_mode(self) -> None:
        self.state_machine.disengage_kill_switch()
        await self.ws_server.broadcast(SafeModeCommand(on=False))
        self.obs.switch_scene(self.state_machine.scene)

    async def pause(self) -> None:
        self.state_machine.paused = True
        self.state_machine.transition(AvatarState.IDLE)

    async def resume(self) -> None:
        if self.state_machine.safe_mode:
            await self.resume_from_safe_mode()
        else:
            self.state_machine.paused = False

    async def inject_speech(self, text: str, emotion: Emotion = Emotion.ALEGRE) -> None:
        check = await self.moderation.check_output(text)
        final_text = check.sanitized_text or text
        await self._speak_direct(
            text=final_text,
            emotion=emotion,
            gesture="wave",
            priority=int(PriorityLevel.P0_OPERATOR),
            event_id=None,
        )

    async def set_mood_and_scene(
        self, *, mood: Emotion | None = None, scene: str | None = None
    ) -> None:
        if mood is not None:
            self.state_machine.mood = mood
        if scene is not None:
            self.state_machine.scene = scene
            self.obs.switch_scene(scene)
        await self.ws_server.broadcast(
            SceneCommand(
                scene_name=scene,
                mood=mood.to_godot() if mood is not None else None,
            )
        )

    async def ban_user(self, user_id: str, nick: str = "Banido") -> None:
        self.moderation.rules.ban_user(user_id)
        await self.audit.upsert_user(user_id=user_id, nick=nick, banned=True)

    async def trigger_idle_speech(self) -> None:
        topic = self.idle_rotator.next_topic()
        await self._speak_direct(
            text=topic.text,
            emotion=Emotion.ALEGRE,
            gesture="wave",
            priority=int(PriorityLevel.P4_CHAT),
            event_id=None,
        )
        self.memory.add_exchange("sistema", "[idle]", topic.text)

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                did_work = await self.process_next_event()
                if not did_work:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Erro no loop do orquestrador: %s", exc)
                await asyncio.sleep(0.2)

    async def _idle_and_maintenance_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(1.0)
                if self.state_machine.paused or self.state_machine.safe_mode:
                    continue

                now = time.monotonic()
                idle_secs = self.idle_config.idle_interval_seconds
                if (
                    self.queue.size == 0
                    and (now - self.state_machine.last_activity_at) >= idle_secs
                ):
                    await self.trigger_idle_speech()

                if (now - self.memory.last_summary_at) >= 600.0:
                    self.memory.refresh_summary()

                self.metrics.obs_dropped_frame_pct = self.obs.get_dropped_frame_pct()
                self.metrics.check_thresholds(
                    queue_size=self.queue.size,
                    spent_usd=self.state_machine.spent_usd,
                    budget_usd=self.state_machine.daily_budget_usd,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Erro no loop de manutenção: %s", exc)
