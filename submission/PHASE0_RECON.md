# Phase 0 — Code Reconnaissance Report

**Config under test:** `config/chat_with_openai_compatible_bailian_cosyvoice.yaml`
**Pipeline:** RTC mic → Silero VAD → SenseVoice ASR → OpenAI-compatible LLM (qwen-plus
on Bailian) → Bailian CosyVoice TTS → LiteAvatar → RTC out.
**Scope:** READ-ONLY. No source code modified.

---

## TL;DR — the two facts that decide Phase 1

1. **Does CosyVoice expose emotion / style / prosody control?**
   **No, not in the way it is currently wired.** The handler creates
   `SpeechSynthesizer(model=handler.model_name, voice=handler.voice,
   callback=callback, format=AudioFormat.PCM_24000HZ_MONO_16BIT)` and then drives it
   purely with text chunks via `session.synthesizer.streaming_call(text)` (and a final
   `streaming_complete()`). See
   `src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py:106-115`. The
   config (`config/chat_with_openai_compatible_bailian_cosyvoice.yaml:33-37`) only sets
   `voice: "longxiaochun"` and `model_name: "cosyvoice-v1"` — no instruction / emotion /
   style / SSML parameter is passed, and the handler exposes none in its config
   (`TTSConfig`: `ref_audio_path`, `ref_audio_text`, `voice`, `sample_rate`, `api_key`,
   `model_name` — that is the full set, lines 29-35).
   The DashScope CosyVoice tts_v2 SDK is text-in/audio-out at this call site; the
   `cosyvoice-v1` model used here does not accept an emotion tag through the
   `SpeechSynthesizer` constructor signature actually being used. **This is a blocking
   constraint for Task 2; alternatives are discussed in `PHASE1_PLAN.md`.**

2. **How is the LLM streamed?**
   **Token-by-token, with `stream=True`, into a per-stream `AVATAR_TEXT` streamer.** The
   LLM handler at
   `src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py:211-237` calls
   `context.client.chat.completions.create(..., stream=True,
   stream_options={"include_usage": True})` and forwards each non-empty
   `chunk.choices[0].delta.content` to the downstream stream via
   `streamer.stream_data(output)`. So Phase 1 Task 1 (latency-filler) can hook either
   (a) the gap between ASR's last-text → first LLM chunk on the same input stream, or
   (b) any custom "first-chunk hook" we add around line 231. The same downstream stream
   key is reused for every chunk of one user turn, which is what makes the filler
   replaceable by the real answer — both can share the cancel/flush plumbing.

---

## 0.1 Repo + submodule state

Both repos are present; the frontend WebUI submodule lives at
`src/service/frontend_service/frontend` and tracks the maintainer's fork
`JackMABEE/OpenAvatarChat-WebUI` on a feature branch (see
`submission/REPORT.md:24` and the previously committed barge-in code under that path,
which is what I read from in this report). Per AGENTS.md §3 I did **not** run any
recursive submodule command; I only read files in place.

Heavy third-party submodules visible under `src/handlers/.../<vendor>/` and
`src/third_party/` look initialized on disk (the read-time path resolution worked) —
not relisted here to avoid implying any sync action was taken.

---

## 0.2 Pipeline call path — verified findings

### LLM call site & streaming

- **File:** `src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py`
- **Class / method:** `HandlerLLM.handle(...)` (line 168 onward).
- **Streaming:** `stream=True` (line 216) — token-by-token chunks flow into the
  `AVATAR_TEXT` streamer at line 237 via `streamer.stream_data(output)`.
- **Per-turn stream lifecycle:** a fresh output stream is opened on the first chunk of
  a turn (lines 184-187) with `ChatStreamConfig(cancelable=True)`. The same stream
  carries every text chunk and is closed with `finish_stream=True` on completion
  (lines 261-263).
- **Cancel handling inside the LLM:** the handler watches its own
  `active_stream_keys` set inside the streaming `for chunk in completion:` loop
  (line 223). When `on_signal(...)` removes the stream key (lines 265-271, on
  `ChatSignalType.STREAM_CANCEL`), the next loop iteration sets `cancelled=True`,
  calls `completion.close()`, and exits without finishing the stream — so a cancel
  mid-token genuinely stops generation, not just discards downstream audio.
- **Latency gap (qualitative):** the gap that Task 1 needs to cover is "user finished
  speaking (ASR `is_last_data` arrives) → first non-empty `chunk.choices[0].delta.content`
  arrives". The OpenAI client uses `timeout=5.0` (line 132). The exact end-to-end
  numbers can only be measured live, but the structural gap is real because the LLM
  call is synchronous-blocking in `handle()` until the first chunk returns.

### CosyVoice TTS interface (the critical one)

- **File:** `src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py`
- **Constructor call (line 106-108):**
  ```python
  session.synthesizer = SpeechSynthesizer(
      model=handler.model_name, voice=handler.voice,
      callback=callback, format=AudioFormat.PCM_24000HZ_MONO_16BIT)
  ```
- **Text feed (lines 109-115):**
  ```python
  session.synthesizer.streaming_call(text)        # for each non-final text chunk
  ...
  session.synthesizer.streaming_call(text)        # final text chunk
  session.synthesizer.streaming_complete()        # closes the stream
  ```
- **Cancel (lines 50-53):** `session.synthesizer.streaming_cancel()` is invoked from
  `BailianTTSSession.reset()`. Cancellation is also routed through `on_signal` for
  `STREAM_CANCEL` (lines 199-218), which finds the right session via either input or
  output stream key and resets it.
- **Emotion / style / prosody parameter:** **none in this call site.** The only knobs
  exposed via `TTSConfig` are `voice`, `model_name`, `sample_rate`, `ref_audio_path`,
  `ref_audio_text`, `api_key` (lines 29-35). No instruction / style / emotion field is
  in the constructor; no SSML wrapping is applied; the text passed to `streaming_call`
  is straight-through (only stripped of `<|...|>` ASR markers via the regex at line 94).
- **Implication for Task 2:** the SDK class supports cosyvoice models that take an
  "instruct" channel only for specific newer voices/models (e.g. cosyvoice-v2 family
  with instruct-capable voices); the project uses `cosyvoice-v1` + `longxiaochun`,
  which from this code path does not accept an instruction. This contradicts the
  task-spec's optimistic phrasing "exposes any emotion/style/prosody control" — see
  the alternatives in `PHASE1_PLAN.md`.

### Barge-in implementation — verification of the prior notes

The prior notes (and `submission/BARGEIN_DESIGN.md`) referenced symbols including
`rtc_stream.py`, `interrupt_handler`, and a "STREAM_CANCEL branch in the RTC client
handler". All three exist; the exact present-day names are:

- **Frontend mic VAD:**
  `src/service/frontend_service/frontend/src/renderer/src/helpers/bargeInDetector.ts`
  — class `BargeInDetector`, attached in
  `src/service/frontend_service/frontend/src/renderer/src/store/webrtc.ts:156-179`
  (`setupBargeIn`) and armed only while `chatStore.replying === true` and the
  feature is enabled (line 172-178). On fire it calls `videoChatStore.interrupt()`
  (line 162-164), which sends the data-channel message —
  **the same code path as the manual interrupt button.**

- **Wire format sent on barge-in:** `webrtc.ts:140-148` sends
  `{header: {name: WsProtocol.Interrupt, request_id: nanoid()}, payload: {}}` over
  `chatDataChannel`.

- **Backend ingress (the `rtc_stream` cancel path):**
  `src/service/rtc_service/rtc_stream.py:283-291`. The message handler matches
  `message['header']['name'] == 'Interrupt'` and emits
  `ChatSignal(type=ChatSignalType.INTERRUPT, source_type=ChatSignalSourceType.CLIENT,
  source_name="rtc")` via `client_session_delegate.emit_signal(...)`.

- **`interrupt_handler` — verified to exist exactly as described:**
  `src/handlers/logic/interrupt/interrupt_handler.py` — class `InterruptHandler`,
  filters `ChatSignalType.INTERRUPT` (line 65), and on receipt calls
  `stream_manager.cancel_stream_chain(target_stream)` (line 107) or
  `stream_manager.cancel_streams_by_type(ChatDataType.CLIENT_PLAYBACK)` (line 113)
  when no specific stream is named. It also records the interrupt in
  `context.session_history` (lines 119-131).

- **`STREAM_CANCEL` branch in the RTC client handler — verified:**
  `src/handlers/client/rtc_client/client_handler_rtc.py:613-642` (`on_signal`). For
  signals targeting `CLIENT_PLAYBACK`, it (a) calls
  `context.client_session_delegate.flush_output()` on `STREAM_CANCEL` (lines 629-633)
  — see next item — and (b) forwards a `ChatSignalMessage` over the data channel so
  the frontend can mark `chatStore.replying = false`.

- **Per-handler propagation of cancel:** every downstream handler subscribes to
  `STREAM_CANCEL` and resets its in-flight work:
  - LLM clears the stream from `active_stream_keys`
    (`llm_handler_openai_compatible.py:265-271`);
  - TTS resets its `BailianTTSSession`, which calls `streaming_cancel()`
    (`tts_handler_cosyvoice_bailian.py:199-218`, `46-53`);
  - LiteAvatar calls `context.interrupt()` on `STREAM_CANCEL` of `CLIENT_PLAYBACK`
    (`avatar_handler_liteavatar.py:162-169`);
  - VAD re-enables input on `STREAM_CANCEL` of `CLIENT_PLAYBACK`
    (`vad_handler_silero.py:600-602`).

**No contradiction with the prior notes;** only the file at `rtc_stream.py` lives at
`src/service/rtc_service/rtc_stream.py` (full path), the cancel branch is in the *RTC
client handler* not in `rtc_stream.py`, and the message-name check is the exact
string `Interrupt` (no `WsProtocol.` prefix on the wire — it's matched by string).

### Frontend mic VAD structure & feasibility of a "user is speaking" signal

- `BargeInDetector` already does an in-browser energy VAD with hysteresis
  (`startThresholdDb` / `stopThresholdDb`), min-duration (`minSpeechMs`), and a
  cooldown. It only counts above-threshold time while the bot is replying
  (`armed === true`).
- **Adding a separate "user is currently speaking" signal:** structurally easy. The
  existing dBFS loop in `bargeInDetector.ts:144-177` already evaluates the level
  every animation-frame. A second, always-on threshold (independent of `armed`) plus
  a small message — e.g. a data-channel `UserSpeechState` — could be emitted on
  enter/leave. The signal would need its own debounce/hysteresis to avoid being
  fired by the bot's own playback when echo cancellation is imperfect.
- **Honest caveat for Task 3 (backchannels):** today the *only* "user is speaking"
  evidence the backend uses while the bot speaks comes from the same browser VAD
  whose firing also triggers barge-in. So a backchannel keyed off "user is speaking"
  is by construction prone to the same false-positive failure mode as barge-in.

### Outbound send path & flush-on-interrupt

- **Outbound send path:** `src/handlers/client/rtc_client/client_handler_rtc.py`
  — `RtcClientSessionDelegate` keeps an `output_queues` dict
  (`AUDIO`/`VIDEO`/`TEXT` `asyncio.Queue`s, lines 258-262). `handle()` (lines 597-608)
  pushes `AVATAR_AUDIO`/`AVATAR_VIDEO` frames into the corresponding queue with
  `data_queue.put_nowait(inputs)`. Text echoes go over the data channel directly
  (lines 597-605, `_send_text_to_chat_channel`).
- **Where the frames leave the queue:** `RtcStream.emit()` / `video_emit()` in
  `src/service/rtc_service/rtc_stream.py:166-225` await
  `client_session_delegate.get_data(...)` from those same queues and hand frames to
  fastrtc.
- **`flush_output()` exists and is wired up — verified:**
  `RtcClientSessionDelegate.flush_output(...)` in `client_handler_rtc.py:333-360`
  drains the `AUDIO` + `VIDEO` queues. It is called from `on_signal()` at
  `client_handler_rtc.py:629-633` on `STREAM_CANCEL` of `CLIENT_PLAYBACK`. Because
  `on_signal` may run off the emit loop, the drain is scheduled via
  `loop.call_soon_threadsafe(_drain)` (line 357-358), exactly as the prior notes
  described.
- This is the same path Phase 1 Task 1's filler must reuse: when the real LLM/TTS
  output is ready, the filler is cancelled like any other stream and `flush_output`
  drops any queued filler audio before real audio plays.

### Personalization hook

- **File:** `src/handlers/llm/openai_compatible/participant_info.py`.
- **Function (verified to exist with that exact name):**
  `build_personalized_system_prompt(base_system_prompt, participant)` (line 104).
- **Where it is called:**
  1. `LLMConfig` carries optional `participant_info` (Option B, config-driven)
     (`llm_handler_openai_compatible.py:36`).
  2. `HandlerLLM.create_context(...)` merges it once into `context.system_prompt`
     (lines 117-123).
  3. `_refresh_system_prompt(context)` is called at the top of every `handle(...)`
     turn (lines 154-166) to pick up Option A (runtime / per-session) info that
     arrives via `shared_states.participant_info`, populated when the frontend
     sends a `SetParticipantInfo` data-channel message handled in
     `rtc_stream.py:292-300`.
- **Phase 1 collision risk:** none of the planned Phase 1 changes need to touch
  the participant block. Any new prompt-augmenting Phase 1 work must layer **after**
  `_refresh_system_prompt` and treat the participant block as opaque, so as not to
  re-open the prompt-injection surface that `build_personalized_system_prompt`
  carefully closed (markers, sanitization, length cap — see
  `participant_info.py:79-101, 118-135`).

---

## Flags / contradictions with the task spec

1. **Task spec Section 0.2 → "TTS (CosyVoice) interface ... is there any emotion / style /
   prosody control parameter available":** **No, not via the current call site.** The
   `SpeechSynthesizer(...)` constructor is invoked with only `model`, `voice`, `callback`,
   `format`, and there is no instruction/SSML field downstream. The `cosyvoice-v1` model
   used in the active config is text-only at this surface. Task 2's framing ("derive a
   tag and pass it to CosyVoice") needs to change shape — see `PHASE1_PLAN.md` for
   alternatives.
2. **Prior notes referenced "STREAM_CANCEL branch in the RTC client handler" and a
   `flush_output()` call — both are exactly as described**, only the exact file path is
   `src/handlers/client/rtc_client/client_handler_rtc.py` (`on_signal`, lines 613-642 and
   `flush_output`, lines 333-360). No contradiction.
3. **Task 3 (backchannels) sits on a hidden coupling** to the same browser VAD used by
   barge-in. The task spec acknowledges the risk but it is worth re-stating: there is
   currently no architectural separation between "the avatar is making sound" and "user
   speech arming barge-in" — both run off `chatStore.replying`. The plan deals with this
   explicitly in `PHASE1_PLAN.md`.
