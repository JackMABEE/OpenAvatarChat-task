# Deployment Plan — UH GPU Machine (single host, SSH, no SLURM)

Read `PHASE0_RECON.md` first so the port and config references below are anchored to
real lines.

**Target environment (per maintainer):** a single University of Houston GPU
machine, reachable over SSH after connecting to the UH VPN (Cisco Secure Client).
It is a *long-lived, always-on* server — not a SLURM batch node. So launching is
done with `tmux` (or `screen` / `systemd --user`, whichever the maintainer prefers
on that host), not via `srun`.

**Active config (LiteAvatar, smallest variant):**
`config/chat_with_openai_compatible_bailian_cosyvoice.yaml`

---

## 0. Pre-flight checks (do these once on first deploy)

- Connect to the UH VPN via Cisco Secure Client on your laptop. Confirm SSH
  reachability to the GPU machine (`ssh user@host`).
- On the host, confirm:
  - GPU is present and visible: `nvidia-smi`.
  - GPU driver version is recent enough to host **CUDA 12.8** runtime libs (the
    repo's torch is pinned to **CUDA 12.8** wheels — verified in
    `pyproject.toml:74-75`: `pytorch-cu128` index, plus `torch==2.8.0`,
    `torchvision==0.23.0`, `torchaudio==2.8.0` at lines 33-35).
  - Disk space: LiteAvatar weights + torch wheels + dashscope/silero/sensevoice
    download to the order of several GB; budget at least 20 GB free in the
    project directory.
  - Outbound HTTPS reachable to `dashscope.aliyuncs.com` (Bailian) and to
    `modelscope.cn` (avatar weights). The UH VPN may not gate this, but UH egress
    sometimes does — confirm with a `curl -I` to both.

**Flag:** I do not know whether UH's firewall transparently allows the egress to
DashScope (`dashscope.aliyuncs.com`) and ModelScope (`www.modelscope.cn`).
Verify before the first run. If not, ask UH IT for an outbound allow-list entry
for those two hosts on TCP/443.

---

## 1. Clone the repo (only the submodules we need)

Per `AGENTS.md` §3 the project spans the parent and the WebUI fork submodule; the
maintainer's deployable layout is documented in `submission/REPORT.md:18-32`.

The strict-minimum clone (parent + WebUI submodule only — *not* the heavy vendor
submodules) is:

```bash
git clone <parent-repo-url> OpenAvatarChat
cd OpenAvatarChat
git submodule update --init src/service/frontend_service/frontend
```

For a **full-stack** deployment (avatar / TTS / VAD), additionally initialize
**only the submodules the active config actually needs** — explicitly, per
`AGENTS.md` §5, those are `silero_vad` and `liteavatar`. Initialize them by
explicit path; **do not** run any recursive submodule command:

```bash
# Example — confirm exact submodule paths with `git config -f .gitmodules -l` first.
git submodule update --init src/handlers/vad/silerovad/silero_vad
git submodule update --init src/handlers/avatar/liteavatar/algo/liteavatar
```

**Flag:** the exact path strings under `.gitmodules` for those two submodules
need a one-line check on the host before this step — I will not guess them in
this doc. The maintainer's `AGENTS.md` §3 makes the cost of a recursive command
high enough that the explicit path matters.

---

## 2. Python environment via uv

Per `AGENTS.md` §5 the previous setup used **uv 0.11.18** and **Python 3.11.x**.
The root `pyproject.toml:6` pins `requires-python = ">=3.11.7, <3.12"`. On the
host:

```bash
# Install uv (if not already)
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL

# Create the venv (uv reads requires-python automatically)
cd ~/OpenAvatarChat
uv venv

# Install dependencies for the LiteAvatar + Bailian config in one pass.
# install.py inspects the YAML to figure out which handler pyprojects to merge.
uv run install.py --config config/chat_with_openai_compatible_bailian_cosyvoice.yaml
```

The torch wheels come from the explicit `pytorch-cu128` index defined in
`pyproject.toml:73-76`, so the CUDA 12.8 build is selected automatically. After
install, sanity-check from a Python REPL inside the venv:

```python
import torch
torch.cuda.is_available()           # must be True
torch.version.cuda                  # should report 12.8
torch.cuda.get_device_name(0)
```

If `cuda.is_available()` returns False, the driver on the host is older than
the CUDA 12.8 runtime; stop and report rather than downgrading the torch pin.

---

## 3. Download LiteAvatar weights via ModelScope

The convenience script is `scripts/download_liteavatar_weights.sh`, which
pushes into `src/handlers/avatar/liteavatar/algo/liteavatar/` and calls that
subdir's `download_model.sh`. From the project root, inside the activated
venv (or via `uv run`):

```bash
# scripts/download_liteavatar_weights.sh checks for modelscope CLI and installs
# it via `uv pip install modelscope` if the .venv is present (lines 7-13 of the
# script). Confirm the .venv is at ./.venv before running.
bash scripts/download_liteavatar_weights.sh
```

Alternative single-handler path (also in the README quick-start):
`uv run scripts/download_models.py --handler liteavatar`. Either is fine — the
shell script is more direct.

Default cache root: the demo sets `MODELSCOPE_CACHE` based on
`engine_config.model_root` (default `models/`, per
`config/chat_with_openai_compatible_bailian_cosyvoice.yaml:10`). See
`src/demo.py:83-85`. So weights land under `OpenAvatarChat/models/`.

**Flag (ModelScope auth):** anonymous downloads of public models work on
modelscope.cn, but rate-limit hits or future paywalled weights would require
`modelscope login` with a token. Not currently needed for `liteavatar` (public),
but worth being aware of.

---

## 4. .env — Bailian API key

The TTS handler reads `DASHSCOPE_API_KEY` from the environment (see
`tts_handler_cosyvoice_bailian.py:169-173`) and the LLM handler's
default is the same env var (`llm_handler_openai_compatible.py:28`).

**Critical clarification:** this is the **Bailian Console API key**, not an
Aliyun RAM AccessKey. Per `AGENTS.md` §5: *"Requires a Bailian API key (from
`bailian.console.aliyun.com`) in a `.env` file — this is distinct from the Aliyun
AccessKey ID."*

Create `OpenAvatarChat/.env` (file lives next to `pyproject.toml` — `python-dotenv`
is in deps at `pyproject.toml:36`):

```
DASHSCOPE_API_KEY=sk-...your-bailian-console-key...
```

Then:

```bash
chmod 600 .env
```

**Do not** commit `.env` (it is already covered by repo .gitignore patterns;
`AGENTS.md` §6 makes this explicit). If a key is ever exposed in logs, rotate it
in the Bailian console.

---

## 5. SSL certs

The active config points to `ssl_certs/localhost.crt` and `ssl_certs/localhost.key`
(`config/chat_with_openai_compatible_bailian_cosyvoice.yaml:7-8`). For a
**local-only over SSH-forward** deployment (see §7), localhost self-signed
certs are sufficient; the browser will accept them after a one-time
"trust this site" click *because we are reaching the server via
`https://localhost:<forwarded-port>/`*.

The repo includes `scripts/create_ssl_certs.sh`; run it once on the host:

```bash
bash scripts/create_ssl_certs.sh
ls -l ssl_certs/   # expect localhost.crt and localhost.key
```

If those already exist, leave them as-is.

---

## 6. Ports the service binds (from the actual code, not guesses)

From `config/chat_with_openai_compatible_bailian_cosyvoice.yaml:5-8`:

- **HTTPS / FastAPI / Gradio / WebRTC signaling** — **TCP 8282** (bind
  `0.0.0.0`). This is the single inbound port the browser talks to: it serves
  the page, the data channel signaling, and the WebRTC SDP exchange. Confirmed
  by `src/demo.py:96` constructing
  `uvicorn.Config(demo_app, host=service_config.host, port=service_config.port, ...)`.

WebRTC media (audio+video) flow uses **UDP** between peer and host; with port
forwarding (see §7) those are tunneled through the established connection
**when STUN/TURN are not in play**. Browsers normally do peer-to-peer over UDP;
when forwarded through SSH, the media falls back to TCP/relay paths. There is a
coturn helper (`scripts/setup_coturn.sh`) that configures TURN on `3478/UDP+TCP`
and `5349/TLS`, but the active config does not require coturn — there's no
`turn_config` block set in
`config/chat_with_openai_compatible_bailian_cosyvoice.yaml`.

**Practical answer for a single inbound port over SSH:** **only TCP 8282
needs to be reachable from the browser.** Confirmed via direct read of the
active YAML, not from `setup_coturn.sh` or `docker-compose.yml`.

**Flag:** if WebRTC media stops working through the SSH tunnel (UDP semantics
forced over TCP), the maintainer may need to enable a TURN server. That is a
follow-up; do not pre-emptively spin up coturn.

---

## 7. SSH local port forwarding

Forward the host's `8282` to a local port on the laptop. Pick a local port
that's free (e.g. `18282`):

```bash
ssh -N -L 18282:127.0.0.1:8282 user@uh-gpu-host
```

Then open `https://localhost:18282/` in a local browser. Accept the self-signed
cert once. The "start chat" UI renders from the Gradio mount at `/gradio` —
the rest of the page is the WebUI build.

Notes:
- `-L 18282:127.0.0.1:8282` (binding the remote side to localhost) is safer
  than `-L 18282:0.0.0.0:8282` because the server listens on `0.0.0.0` already.
  This avoids exposing the port to other users on the GPU machine.
- If the VPN drops, the SSH tunnel dies; reconnecting requires re-establishing
  both. tmux/screen on the *server* side keeps the service alive across that.
- **Microphone permission** in the browser only works on `localhost` or HTTPS
  contexts; the `https://localhost:18282/` URL satisfies both, so the mic
  prompt will appear correctly. Reaching the server by raw IP would *not* —
  always forward via localhost.

---

## 8. Launching persistently

Inside `tmux` on the host (so the service survives the SSH session ending):

```bash
tmux new -s avatar
# inside the tmux session:
cd ~/OpenAvatarChat
uv run src/demo.py --config config/chat_with_openai_compatible_bailian_cosyvoice.yaml
# detach with Ctrl-b d
```

Re-attach later with `tmux attach -t avatar`. The server logs to stdout (loguru
defaults). When you need a restart: `tmux send-keys -t avatar C-c` then re-run.

Alternatives, in order of complexity:
- `screen -S avatar` — same idea, different muscle memory.
- A `systemd --user` unit — only worth it if the maintainer wants the service
  to come back automatically across host reboots. Defer until the manual
  workflow is proved.

---

## 9. Verifying the deployment (end-to-end)

1. Tunnel up, browser at `https://localhost:18282/`, accept the cert.
2. The UI loads; the personalization form (per `submission/REPORT.md:104-122`)
   is visible.
3. Allow microphone access at the prompt.
4. Click **start chat** — the WebRTC data channel opens; backend logs show
   `[<session_id>] H.264 encoder: ...` from `client_handler_rtc.py:128`.
5. Speak a short prompt. Expected log path:
   - `vad_handler_silero.py` logs `Start of human speech` then `End of human
     speech, entering POST_END monitoring`.
   - SenseVoice ASR posts a transcript; LLM logs `llm input qwen-plus <text>`.
   - CosyVoice logs `streaming_call <text>` then `TTS: Synthesis complete`.
   - The avatar speaks and lip-syncs.
6. **Barge-in smoke test:** start a long bot reply, then talk over it. Backend
   logs `InterruptHandler: Received INTERRUPT signal, source_type=CLIENT,
   source_name=rtc` (from `interrupt_handler.py:84-87`) and
   `RtcClient: flushed N buffered ... frames on interrupt` (from
   `client_handler_rtc.py:354`). The bot should stop within a few hundred ms.
7. **Personalization smoke test:** fill the form, click start, ask "what's my
   name?". The LLM should answer with the name. Logs show
   `Set participant_info for session: {...}` from `rtc_stream.py:300`.

---

## 10. Things this plan deliberately does not do

- No Docker. The repo has `Dockerfile` / `docker-compose.yml` and
  `build_cuda128.sh`, but on a single always-on host they add a layer for no
  gain. If the maintainer prefers Docker later, the CUDA 12.8 base is already
  selected.
- No coturn / TURN. Not required by the active config; only needed if browser
  WebRTC negotiation fails through the SSH tunnel. Then run
  `sudo bash scripts/setup_coturn.sh` and add a `turn_config:` block to the
  YAML — out of scope here.
- No reverse proxy / nginx. Single-user SSH-forwarded localhost access has no
  need for one.
- No SLURM scripts. The host is not SLURM-managed.

---

## 11. Open items to confirm before first run

These are the things I cannot answer from reading the code alone:

1. The exact hostname / username for the UH GPU machine, and whether your UH
   account can already SSH after VPN connection (or needs a separate ticket).
2. Whether the host's egress allows `dashscope.aliyuncs.com` and
   `modelscope.cn` over TCP/443.
3. Whether GPU access is already provisioned (driver + visible `nvidia-smi`),
   or whether a UH IT ticket is still pending.
4. The exact `.gitmodules` submodule paths for `silero_vad` and `liteavatar`
   on the current parent commit — do a `git config -f .gitmodules -l` on the
   host before running selective `git submodule update --init <path>` commands
   (do not run a recursive command — `AGENTS.md` §3 is non-negotiable).
5. Disk quota in the user's home directory on the host — LiteAvatar weights
   plus the venv consume several GB.
