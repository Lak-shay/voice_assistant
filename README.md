# Clinic Voice Receptionist

An automated inbound telephone voice agent for healthcare clinic reception operations. Built on the LiveKit Agents SDK, this service handles caller intake, appointment scheduling against Google Calendar, SMS confirmations, emergency medical triage, and live SIP call transfers.

---

## System Architecture

```
 [Caller via PSTN]
         │
         │ (1) Inbound Call (Dial / Conditional Forward)
         ▼
 [Telnyx Telephony Carrier] ──(Emergency / Timeout Failover)──► [Clinic Front Desk Phone]
         │
         │ (2) SIP Trunk Bridge
         ▼
 [LiveKit Cloud SIP Bridge & SFU]
         │
         │ (3) Bi-directional WebRTC Audio Stream (Opus)
         ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      Python Agent Worker (agent.py)                    │
 │                                                                        │
 │   ┌──────────────────────┐              ┌──────────────────────────┐  │
 │   │  Silero VAD          │              │ Cartesia Sonic-3 (TTS)   │  │
 │   │  (Barge-in / Cutoff) │              │ (Streaming Audio Chunks) │  │
 │   └──────────┬───────────┘              └────────────▲─────────────┘  │
 │              │ Audio Frames                          │ Audio Chunks    │
 │              ▼                                       │                 │
 │   ┌──────────────────────┐              ┌────────────┴─────────────┐  │
 │   │ Deepgram Nova-3      │              │ LLM Reasoning Engine     │  │
 │   │ (Streaming STT WS)   │              │ (Claude 3.5 Haiku /      │  │
 │   └──────────┬───────────┘              │  GPT-4o-mini)            │  │
 │              │ Interim/Final Text       └────────────▲─────────────┘  │
 │              ▼                                       │                 │
 │      Turn Detection ─────────────────────────────────┘                 │
 │                             Tool Executions                            │
 │                                    │                                   │
 └────────────────────────────────────┼───────────────────────────────────┘
                                      │
             ┌────────────────────────┴────────────────────────┐
             │                                                 │
             ▼                                                 ▼
 ┌────────────────────────┐                       ┌────────────────────────┐
 │  External Services     │                       │ Telemetry & Tracing    │
 │  ├── Google Calendar   │                       │ └── Langfuse v4        │
 │  └── Telnyx REST (SMS) │                       │     ├── Session Traces │
 └────────────────────────┘                       │     ├── Latency (TTFT) │
                                                  │     └── Token Costs    │
                                                  └────────────────────────┘
```

---

## Core Components

### 1. Real-Time Audio Pipeline
* **VAD (Voice Activity Detection):** Silero VAD configured with tuned speech and silence thresholds (`0.05s` speech, `0.40s` silence). Supports barge-in: caller speech immediately halts TTS playback and flushes downstream audio buffers.
* **STT (Speech-to-Text):** Deepgram Nova-3 over streaming WebSocket for real-time transcription and rapid endpointing.
* **LLM Engine:** Claude 3.5 Haiku or OpenAI GPT-4o-mini via LiveKit Inference, operating with prompt constraints tailored for spoken dialogue.
* **TTS (Text-to-Speech):** Cartesia Sonic-3 emitting streaming PCM audio chunks with sub-120ms first-byte response time.

### 2. Dialogue & Voice UX Constraints
* **Sentence Length:** Responses are strictly limited to 1 to 2 short conversational sentences per turn.
* **Sanitization:** Markdown syntax (asterisks, headers, bullets, backticks) and emojis are stripped before audio synthesis.
* **Phonetic Output:** Numbers, dates, times, and phone digits are spoken phonetically so the synthesis engine produces clear pronunciations.
* **Caller ID Detection:** The agent automatically inspects SIP metadata for caller ID (`P-Asserted-Identity`, `Remote-Party-ID`, SIP `From`). If present, the agent avoids asking for a phone number and confirms the caller's name before booking. If absent, the agent reads back the caller's phone number digit-by-digit for explicit confirmation.

### 3. Asynchronous Tool Suite
* `check_availability`: Queries open slots for a target date, grouping openings into morning, afternoon, and evening periods.
* `book_appointment`: Validates slot status, acquires an internal mutex write-lock to prevent race conditions during concurrent bookings, writes the calendar event, and records patient details.
* `send_confirmation_sms`: Dispatches post-booking SMS confirmations with appointment details via the Telnyx REST API.
* `transfer_to_human`: Initiates a SIP REFER blind transfer to route callers to the clinic's physical staff line when requested or when an inquiry exceeds booking capabilities.

### 4. Multi-Layer Guardrails
* **Emergency Triage (`guardrails/emergency.py`):** Deterministic regex detection for acute symptoms (chest pain, severe shortness of breath, heavy bleeding, stroke symptoms, anaphylaxis, overdose). Immediately bypasses LLM reasoning, instructs the caller to hang up and dial 911, and ends the turn without taking scheduling actions.
* **Prompt Injection Defense (`guardrails/input_guardrails.py`):** Blocks attempts to override system instructions or extract internal configuration.
* **Secret Masking (`guardrails/output_guardrails.py`):** Redacts API keys, tokens, and authorization headers from outbound TTS streams.
* **Input Validation (`guardrails/tool_guardrails.py`):** Enforces bounds on booking dates (no past dates, maximum advance booking limit), verifies patient names, and validates E.164 phone numbers.

### 5. Telemetry & Non-Blocking Logging
* **Langfuse v4:** Session traces are anchored to the LiveKit room name (`trace_id`). Tracks turn latencies, tool execution durations, token costs, and interruption counts.
* **Decoupled Queue Logging (`logger.py`):** Logs use `QueueHandler` on the main loop and `QueueListener` on a dedicated worker thread to prevent thread contention or I/O stalls during WebRTC audio processing.

---

## Conversational Latency Budget

To maintain natural conversational rhythm over telephone networks, the end-to-end turnaround targets an average of ~800ms:

| Pipeline Stage | Target Latency | Optimization Mechanism |
| --- | --- | --- |
| **VAD Silence Detection** | 350ms - 450ms | Tuned silence threshold preventing premature speaker cutoff |
| **STT Finalization** | 150ms - 200ms | Deepgram WebSocket streaming with interim results |
| **LLM Time-to-First-Token** | 200ms - 300ms | Compact system instructions on low-latency inference models |
| **TTS First Audio Chunk** | 80ms - 120ms | Cartesia Sonic streaming chunk generation |
| **Total Turnaround** | **780ms - 1070ms** | Typical response time matches natural telephone turn-taking |

---

## Project Structure

```
voice_assistant/
├── agent.py                 # LiveKit worker entrypoint, agent loop, and lifecycle handlers
├── config.py                # Pydantic-settings configuration loading (.env.dev / .env.prod)
├── logger.py                # Decoupled QueueHandler / QueueListener logging architecture
├── startup.py               # Pre-flight health checks (LiveKit, Langfuse, Calendar, Telnyx)
├── telemetry.py             # Langfuse v4 trace setup and metrics recording
├── requirements.txt         # Core dependencies with pinned version ranges
├── Dockerfile               # Production container image definition
├── pytest.ini               # Pytest configuration
├── .env.example             # Environment variable template
├── guardrails/
│   ├── emergency.py         # Emergency medical keyword detection and 911 dispatch advice
│   ├── input_guardrails.py  # Prompt injection and medical advice guardrails
│   ├── output_guardrails.py # Secret redaction and text cleaning for TTS
│   └── tool_guardrails.py   # Parameter validation for names, dates, and phone numbers
├── tools/
│   ├── appointment_tools.py # LiveKit @function_tool declarations
│   ├── calendar_service.py  # Google Calendar API client and in-memory mock backend
│   └── sms_service.py       # Telnyx REST SMS client and E.164 number sanitizer
├── simulations/
│   ├── scenarios.yaml       # Conversational test scenarios (booking, transfer, triage)
│   ├── scenarios_contention.yaml # High-concurrency booking contention scenarios
│   └── simulate.ps1         # PowerShell simulation runner script
└── tests/
    ├── test_agent_pipeline.py
    ├── test_guardrails_emergency.py
    ├── test_guardrails_input.py
    ├── test_guardrails_output.py
    ├── test_guardrails_tools.py
    ├── test_startup.py
    ├── test_telemetry.py
    └── test_tools.py
```

---

## Prerequisites

1. **Python:** 3.12 or 3.13
2. **LiveKit Cloud Account:** Access to LiveKit Cloud URL, API Key, and Secret with SIP service enabled.
3. **Telnyx Account:** A provisioned telephone number with an active SIP trunk connected to LiveKit.
4. **Langfuse Account:** Public key, secret key, and base URL (Cloud or self-hosted).
5. **Google Cloud Service Account (Optional):** Required if running with `CALENDAR_BACKEND=google`. Provide credentials with access to the target Google Calendar ID.

---

## Installation & Setup

### 1. Clone Repository & Create Virtual Environment

```bash
git clone https://github.com/Lak-shay/voice_assistant.git
cd voice_assistant

python -m venv .venv

# On Linux / macOS:
source .venv/bin/activate

# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Configuration

Copy the example configuration to your target environment file:

```bash
cp .env.example .env.dev
```

Edit `.env.dev` with your credentials:

```ini
# Application Environment (dev | prod)
APP_ENV=dev

# LiveKit Cloud
LIVEKIT_URL=wss://<your-project>.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Observability & Tracing (Langfuse)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com

# Telephony (Telnyx SIP & Carrier Failover)
TELNYX_API_KEY=your_telnyx_api_key
TELNYX_PHONE_NUMBER=+15550001111
CLINIC_FAILOVER_PHONE=+15551234567
SIP_TRUNK_ID=your_sip_trunk_id

# LiveKit Inference Models
STT_PROVIDER=deepgram
STT_MODEL=nova-3
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
TTS_PROVIDER=cartesia
TTS_MODEL=sonic-3
TTS_VOICE_ID=9626c31c-bec5-4cca-baa8-f8ba9e84c8bc

# Silero VAD & Turn Tuning
VAD_MIN_SPEECH_DURATION=0.05
VAD_MIN_SILENCE_DURATION=0.40

# Clinic Operations & Booking
CLINIC_NAME="Dr. Smith's Clinic"
CLINIC_TIMEZONE=America/New_York
APPOINTMENT_DEFAULT_DURATION_MINUTES=30

# Integrations (mock | google for calendar, mock | telnyx for sms)
CALENDAR_BACKEND=mock
CALENDAR_API_KEY=
CALENDAR_ID=
SMS_BACKEND=mock

# Health Checks & Concurrency
STARTUP_CHECK_TIMEOUT=8.0
NUM_IDLE_PROCESSES=10

# Guardrails
GUARDRAILS_ENABLED=true
GUARDRAILS_MAX_BOOKING_DAYS_AHEAD=60
GUARDRAILS_EMERGENCY_TRIAGE_ENABLED=true
GUARDRAILS_INJECTION_DEFENSE_ENABLED=true
GUARDRAILS_TTS_SANITIZER_ENABLED=true
```

---

## Running the Application

### Pre-Flight Verification
Run the standalone startup check to verify network reachability, API authentication, and model routing:

```bash
python startup.py
```

### Local Development Mode
Start the LiveKit worker in interactive development mode:

```bash
python agent.py dev
```

### Production Worker Mode
Run the agent as a background worker process:

```bash
python agent.py start
```

### Docker Deployment
Build and run the containerized worker:

```bash
docker build -t clinic-voice-assistant .
docker run --env-file .env.prod --rm clinic-voice-assistant
```

---

## Testing & Simulations

### Unit and Integration Tests
The test suite validates agent instructions, guardrails, calendar concurrency locks, SIP transfer logic, and telemetry handling:

```bash
pytest -v
```

### Synthetic Caller Simulations
Simulate multi-turn caller conversations against defined scenarios using the LiveKit CLI:

```powershell
# Run scenarios in text mode with concurrency 1
powershell simulations/simulate.ps1 -Mode text -Concurrency 1

# Run audio simulation against contention scenarios
powershell simulations/simulate.ps1 -Mode audio -Concurrency 5 -Scenarios scenarios_contention.yaml
```

Available scenarios in `simulations/scenarios.yaml`:
* **Standard Appointment Booking:** Caller requests an appointment, evaluates periods, confirms details, and completes booking.
* **Emergency Triage:** Caller describes acute chest pain; agent immediately intervenes with 911 dispatch guidance.
* **Human Staff Transfer:** Caller asks to speak with an office receptionist; agent executes `transfer_to_human`.
* **Slot Contention:** Concurrent simulated callers attempt to book the same calendar opening; verifies mutex lock handling and fallback suggestions.

---

## Telephony Setup (Telnyx to LiveKit)

1. **Number Provisioning:** Purchase or port a DID phone number in Telnyx (`TELNYX_PHONE_NUMBER`).
2. **SIP Trunk Association:** Create an Outbound SIP Trunk in Telnyx pointing to the LiveKit SIP domain (`<project>.sip.livekit.cloud`).
3. **Failover Routing:** Configure Telnyx carrier-level failover: if SIP handshake times out, forward inbound calls directly to the clinic front desk (`CLINIC_FAILOVER_PHONE`).
4. **Conditional Forwarding:** On the clinic's local phone system, configure conditional call forwarding (Busy / No Answer after 3 rings) to the Telnyx phone number.
