# FactoryOS V2 — Autonomous Industrial Operations & Macho AI Copilot

FactoryOS V2 is an autonomous, full-stack industrial operations intelligence platform and simulation environment. It combines discrete-time manufacturing floor telemetry, multi-order batch scheduling, dynamic shift workforce allocation, and **Macho** — a private, local ReAct AI Operations Copilot powered strictly by **Ollama**, featuring an advanced hybrid **RAG (Retrieval-Augmented Generation) & Knowledge Engine** (BM25 + Dense Vectors + RRF + Cross-Encoder Reranking) and a proactive **Real-Time Telemetry Analysis Reporter**.

---

## 🏭 1. About the Project

### The Problem in Modern Manufacturing
Industrial discrete manufacturing floors (automotive components, precision gearing, aerospace assemblies) operate under tight delivery deadlines and high asset utilization. In traditional plants:
1. **Unplanned Bottlenecks:** When a machine operator is absent on a single-workstation bottleneck stage (e.g., Milling `WS 1/1`), the entire sequential production line halts immediately. Supervisors often take 30–60 minutes to notice, diagnose, and dispatch a certified replacement.
2. **Data Silos & Noise:** Telemetry (machine alarms, scrap rates, downtime logs) is scattered across PLC logs, paper shift rosters, ERP inventory databases, and PDF maintenance binders.
3. **Inventory Starvation:** Raw materials deplete silently across fast-running production shifts; operators only notice material starvation when a machine runs dry, causing sudden line stoppages.
4. **Cloud & Privacy Risks:** Enterprise manufacturers cannot stream proprietary telemetry, internal scrap rates, or workforce records to third-party cloud LLMs due to intellectual property and regulatory boundaries.

### The Solution: FactoryOS V2
FactoryOS V2 bridges physical floor telemetry and autonomous operational action. It pairs a **5-second discrete-time simulation engine** with **Macho**, an autonomous on-premise operations copilot:
- **100% Local & Private:** Powered by **Ollama** running local open-weights LLMs (`llama3.2`, `mistral`) inside the factory firewall with zero cloud data transmission.
- **Proactive Telemetry Watchdog:** Continuously analyzes inventory burn rates, predicts runout hours, and alerts supervisors when raw material stock hits $\le 20\%$ of nominal capacity.
- **Advanced Hybrid RAG Knowledge Engine:** Ingests factory SOPs, manuals, and safety checklists with Parent-Child chunking, BM25 + dense vector search fused via Reciprocal Rank Fusion (RRF), cross-encoder reranking, and confidence gating.
- **Root Cause Determination in Seconds:** Eliminates multi-part corporate boilerplate by providing precise, 2-line root cause diagnosis and recommending instant remediations.
- **Autonomous Floor Actions:** Not just an informational chatbot—Macho executes real operational actions (e.g., querying the standby Free Pool and autonomously deploying certified relief floaters to halted workstations).
- **Multi-Batch Scheduling:** Coordinates sequential multi-order customer contracts, automatically tracking quotas and enabling 1-click batch changeovers.
- **Evidence-Grounded (Zero Hallucination):** Every claim is tagged with verified runtime evidence (`[E#]`), guaranteed by deterministic input/output guardrails.

---

## 🛠️ Complete Technology Stack & Architecture

FactoryOS V2 is built with a strictly decoupled, production-grade architecture combining local generative AI, vector retrieval, and discrete-time physical manufacturing simulation:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MODERN WEB FRONTEND (VANILLA JS/CSS)                  │
│   Floor Monitor  │  Interactive Line Routes  │  Macho Copilot Slide Drawer  │
│   Companion Robot Alert Symbol (⚠️)          │  Telemetry Alert Card        │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ HTTP / JSON
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                           FASTAPI BACKEND SERVER                            │
│  REST Telemetry Endpoints  │  Batch Order Dispatcher  │  Simulator Lifespan │
└──────────┬───────────────────────────┬───────────────────────────┬──────────┘
           │                           │                           │
┌──────────▼──────────┐     ┌──────────▼──────────┐     ┌──────────▼──────────┐
│  DISCRETE SIMULATOR │     │  MACHO AI COPILOT   │     │  ADVANCED HYBRID    │
│  - 5-sec Tick Loop  │     │  - ReAct Loop       │     │     RAG ENGINE      │
│  - Machine Health   │     │  - Multi-Turn Memory│     │  - BM25 + Vector RRF│
│  - BOM Material Use │     │  - Input Guardrails │     │  - Cross-Encoder    │
│  - Telemetry Watch  │     │  - Evidence Engine  │     │  - Parent-Child Chk │
│  - Shift Workforce  │     │  - Telemetry Reporter│    │  - RAGAS Evals      │
└─────────────────────┘     └──────────┬──────────┘     └─────────────────────┘
                                       │ REST API (:11434)
                            ┌──────────▼──────────┐
                            │    LOCAL OLLAMA     │
                            │  (llama3.2/mistral) │
                            │  100% Private / No  │
                            │  Cloud Data Leakage │
                            └─────────────────────┘
```

### 1. Local Large Language Model (LLM) — Ollama
* **Role:** Powers all generative reasoning, structured ReAct tool selection, and conversational synthesis.
* **Technology:** **Ollama** running locally on `http://127.0.0.1:11434`.
* **Models Supported:** `llama3.2:latest`, `mistral:latest`, `phi3:latest`, `qwen3`.
* **Zero Cloud Dependency:** All operational logs, workforce rosters, machine telemetry, and proprietary production data stay 100% local within the plant firewall.

### 2. Advanced Hybrid RAG Engine (BM25 + Vector Search + RRF + Cross-Encoder)
* **Location:** [v2/rag.py](file:///e:/Vishwa/learning/agent/v2/rag.py), [v2/ragas_eval.py](file:///e:/Vishwa/learning/agent/v2/ragas_eval.py)
* **Major Improvements Implemented:**
  1. **Hybrid Search (BM25 + Dense Vectors + RRF):** Combines lexical Okapi BM25 ($k_1=1.5, b=0.75$, Robertson-Spärck Jones IDF smoothing, and suffix normalization) with dense vector embeddings (`all-MiniLM-L6-v2` / Ollama) using **Reciprocal Rank Fusion (RRF, $k=60$)**.
  2. **Cross-Encoder Reranking:** Re-scores merged candidate passages with a joint query-passage cross-encoder (`ms-marco-MiniLM-L-6-v2`) with contextual fallback scoring (query coverage, heading proximity, term co-occurrence).
  3. **Metadata-Aware & Parent-Child Chunking:** Parses markdown structure into hierarchical chunks:
     - **Parent Chunks (~1,400 chars):** Provide complete operational context for the LLM.
     - **Child Chunks (~380 chars):** Fine-grained semantic segments for precision vector and keyword matching.
     - Tracks `document_name`, `section`, `page_number`, and `parent_id`.
  4. **Document Versioning & Incremental Indexing:** Maintains `v2/data/document_manifest.json` with SHA-256 hashes and file modification timestamps (`mtime`). Only re-indexes modified or newly added SOP documents, skipping unchanged files on startup.
  5. **Granular Source Citations:** Standardized evidence citations formatted as `[document_name.md | p. X > Section Title]` (e.g. `[machine_manual.md | p. 1 > Warning signs]`).
  6. **Confidence-Based Retrieval & Insufficient-Context Handling:** Normalizes relevance scores $\in [0, 1]$. Searches falling below the confidence threshold (`min_confidence=0.25`) or matching no relevant documentation return structured `insufficient_context` notices to eliminate hallucinations.
  7. **Automated RAGAS Evaluation Framework:** Comprehensive evaluation suite evaluating **Faithfulness**, **Answer Relevancy**, **Context Precision (MAP@K)**, and **Context Recall** across factory test queries (`python -m v2.evals --ragas`).

### 3. Macho Proactive Telemetry Analysis Reporter & Inventory Depletion Watchdog
* **Location:** [v2/telemetry_reporter.py](file:///e:/Vishwa/learning/agent/v2/telemetry_reporter.py), [v2/api.py](file:///e:/Vishwa/learning/agent/v2/api.py), [v2/frontend/app.js](file:///e:/Vishwa/learning/agent/v2/frontend/app.js)
* **Core Capabilities:**
  - **Autonomous 5-Second Monitoring Loop:** Integrated into the background simulator loop, analyzing live `inventory.csv` telemetry across all active plants.
  - **Warehouse Nominal Capacity Baselines:** Maps material consumption against true warehouse capacities (`NOMINAL_CAPACITIES`: e.g. F001 Steel blanks: 18,000, Aluminum: 5,000, Bearings: 6,000, Fasteners: 24,000, Caliper seals: 12,000, Coolant: 900L).
  - **Calibrated $\le 20\%$ Near-Depletion Threshold:** Alerts trigger strictly when remaining stock falls to **20% or below** of nominal warehouse capacity, preventing premature alarms.
  - **Burn Rate & Runout Estimation:** Calculates hourly burn pace based on active shift consumption and projects remaining operating hours until exhaustion.
  - **Downstream Line Impact Correlation:** Directly maps depleted or near-depletion raw materials to the affected production lines (e.g. `Line 01`, `Line 02`).
  - **Proactive UI Integration:**
    - Displays a clean alert symbol (`⚠️ Ask Macho`) over the companion robot without cluttering the plant floor layout.
    - Clicking Macho immediately opens the slide-over drawer rendering the Telemetry Alert Card at the top of the chat with stock percentages (`[16.4% stock left]`), true capacities, safety thresholds, and 1-click action triggers (`Ask Macho for Restocking & Shortfall Plan →`).
  - **Agent Tool Integration:** Registered `get_telemetry_alerts` tool allowing Macho to formulate autonomous restocking and shortfall avoidance strategies during conversation.

### 4. Agent Reasoning Engine & Tool Registry
* **Location:** [v2/harness.py](file:///e:/Vishwa/learning/agent/v2/harness.py), [v2/tools.py](file:///e:/Vishwa/learning/agent/v2/tools.py), [v2/skills/__init__.py](file:///e:/Vishwa/learning/agent/v2/skills/__init__.py)
* **ReAct Architecture:** Implements a dynamic **Thought → Action → Observation** execution loop.
* **Anti-Hallucination Evidence Engine:** Every tool execution registers structured evidence (`E1`, `E2`, etc.) in the `EvidenceStore`. Claims made by the model must cite indexed evidence tags `[E#]`.
* **Multi-Turn Conversation Memory:** Maintains rolling conversation history (`role: "user"`, `role: "assistant"`) across up to 20 turns, enabling sequential multi-prompt debugging and autonomous actions.
* **Domain Skills & Dynamic SOP Injection:** Automatically matches queries to specialized skill playbooks:
  - `Root Cause Analysis (RCA)`: 5-Whys diagnostic procedure prioritizing staffing bottlenecks before mechanical failure searches.
  - `Shift Workforce Staffing & Attendance Management`: Standby pool vs. deployed floater resolution.
  - `Manufacturing Work Order & Queue Management`: Sequential multi-day batch progress and dispatching.
  - `Material Shortage & Supply Chain Assessment`: BOM starvation analysis.
  - `Preventative Maintenance & Equipment Health`: Service logs and repair timestamps.
* **Multi-Agent Hierarchical Supervisor:** Optional multi-agent mode ([v2/multi_agent/](file:///e:/Vishwa/learning/agent/v2/multi_agent/)) routing queries across dedicated domain specialist agents (Operations Specialist, Workforce Specialist, Inventory Specialist, Maintenance Specialist) with consensus synthesis.

### 5. High-Performance REST API
* **Location:** [v2/api.py](file:///e:/Vishwa/learning/agent/v2/api.py)
* **Framework:** **FastAPI** with **Uvicorn** ASGI server.
* **Validation:** **Pydantic V2** models for strict request/response parameter validation and type safety.
* **Core Endpoints:**
  - `GET /api/factories`: Multi-plant metadata (Automotive, Textiles, Electronics).
  - `GET /api/factories/{id}/production`: Real-time line throughput, achieved units, and shortfall.
  - `GET /api/factories/{id}/orders`: Multi-batch contract schedules, completed orders, active orders, and next in queue.
  - `POST /api/factories/{id}/orders/dispatch-next`: 1-click batch loader for next queued work order.
  - `GET /api/factories/{id}/employees`: Real-time shift roll-call, absentees, and standby Free Pool roster.
  - `POST /api/factories/{id}/employees/assign-floater`: Allocates a relief floater to an unstaffed machine.
  - `POST /api/factories/{id}/employees/unassign-floater`: Releases a relief floater back to the Free Pool.
  - `GET /api/factories/{id}/telemetry-alerts`: Proactive inventory depletion alerts for plant.
  - `GET /api/alerts`: All active factory telemetry alerts.
  - `POST /api/agent/query`: Single-agent ReAct or multi-agent supervisor copilot investigation endpoint.

### 6. Discrete-Time Physical Simulation Engine
* **Location:** [v2/simulator.py](file:///e:/Vishwa/learning/agent/v2/simulator.py)
* **Lifecycle:** Runs as an asynchronous background task within FastAPI's lifespan context manager, ticking every 5 seconds.
* **Simulation Mechanics:**
  - **Sequential & Parallel Routing:** Models stages with single (`WS 1/1`) or multiple parallel workstations (`WS 2/2`, `WS 3/3`). Halts lines if a single-workstation stage fails, or degrades line speed proportionally for parallel stages.
  - **Dynamic Workforce Allocation:** Simulates absent operators and bottleneck resolution when floaters are deployed.
  - **Bill of Materials (BOM) Consumption:** Consumes raw materials per unit produced and halts lines on material starvation.
  - **Sporadic Breakdowns & Cooldowns:** Stochastic Poisson-like machine failures with 20-tick service recovery and plant-wide cooldowns.
  - **Quality Inspection Simulation:** Per-unit defect inspection tracking rework and rejection scrap rates.

### 7. Modern Frontend Dashboard
* **Location:** [v2/frontend/](file:///e:/Vishwa/learning/agent/v2/frontend/)
* **Stack:** Vanilla **HTML5, CSS3, ES6+ JavaScript** (zero bloated node_modules or build steps).
* **UI Features:**
  - Glassmorphic dark/light operations dashboard.
  - Interactive 10-station horizontal process route visualizer with live status dots.
  - Draggable, interactive Macho companion robot with pointer-capture physics and position persistence (`localStorage`).
  - Proactive drawer alert card with direct 1-click prompt triggers.
  - Slide-over Operations Intelligence Drawer with live response streaming and chat bubbles.
  - 1-Click Floater Dispatch & Roster Management modal.

---

## 🔄 2. System Flowcharts & Operational Workflows

### 1. End-to-End System Architecture Flowchart
This flowchart captures the complete data loop between the interactive web dashboard, the asynchronous FastAPI backend, the physical simulation loop, the proactive telemetry analysis engine, the ReAct AI copilot, and the local Ollama LLM runtime:

```mermaid
flowchart TD
    subgraph UI ["Modern Web Frontend (Vanilla HTML5 / CSS3 / ES6+)"]
        Monitor["Production Floor Monitor (10-Station Visual Route)"]
        Drawer["Macho Copilot Slide-Over Drawer"]
        AlertCard["Proactive Telemetry Alert Card ([X% stock left])"]
        Companion["Interactive Physics Companion Robot (⚠️ Badge)"]
    end

    subgraph API ["FastAPI Backend Server (:8000)"]
        Endpoints["REST API Endpoints (/api/factories/*)"]
        AlertEndpoint["Telemetry Alerts Endpoint (/api/factories/{id}/telemetry-alerts)"]
        Lifespan["Async Lifespan Background Coordinator"]
    end

    subgraph Sim ["Discrete-Time Simulation & Telemetry Engine"]
        Tick["5-Second Physical Tick Loop"]
        LineEng["Multi-Stage Production Line Engine"]
        Staffing["Dynamic Floater Allocation Matrix"]
        BOM["Bill of Materials Depletion Tracker"]
        TelReporter["Macho Telemetry Analysis Reporter (<=20% Threshold Watchdog)"]
    end

    subgraph Agent ["Macho AI Operations Copilot"]
        Harness["ReAct Reasoning Loop (Thought -> Action -> Observation)"]
        Guardrails["Input/Output Guardrails & Anti-Prompt Injection"]
        Evidence["EvidenceStore Verification ([E#] Tagging)"]
        Skills["Domain Playbooks (RCA, Staffing, Orders, Inventory)"]
    end

    subgraph Knowledge ["Advanced Hybrid RAG Knowledge Engine"]
        ParentChild["Parent-Child Hierarchical Chunker"]
        Manifest["Manifest Hash Tracker (document_manifest.json)"]
        DenseVector[("Local Dense Vector Store (all-MiniLM-L6-v2)")]
        BM25Index[("Okapi BM25 Inverted Lexical Index")]
        RRFMerge["Reciprocal Rank Fusion (RRF k=60)"]
        CrossEncoder["Cross-Encoder Reranker (ms-marco-MiniLM)"]
        ConfGating{"Confidence >= 0.25"}
    end

    subgraph LLM ["Local LLM Runtime"]
        Ollama["Local Ollama Server (:11434)"]
        Model["llama3.2 / mistral (100% On-Premise)"]
    end

    Monitor <-->|"HTTP / JSON Telemetry"| Endpoints
    AlertCard <-->|"GET /telemetry-alerts"| AlertEndpoint
    Drawer <-->|"POST /api/agent/query"| Endpoints
    Endpoints <--> Lifespan
    Lifespan <--> Sim
    Sim <--> TelReporter
    TelReporter --> AlertEndpoint
    Endpoints <--> Harness
    Harness <--> Guardrails
    Harness <--> Skills
    Harness <--> Evidence
    Harness <-->|"Tool: search_factory_documents"| Knowledge
    Knowledge --> ParentChild
    ParentChild --> DenseVector
    ParentChild --> BM25Index
    DenseVector --> RRFMerge
    BM25Index --> RRFMerge
    RRFMerge --> CrossEncoder
    CrossEncoder --> ConfGating
    Harness <-->|"Local REST API"| Ollama
    Ollama <--> Model
```

---

### 2. Autonomous Incident Triage & Floater Dispatch Sequence
The following sequence diagram maps the live production stoppage scenario and autonomous floater dispatch resolution:

```mermaid
sequenceDiagram
    autonumber
    actor User as Operations Supervisor
    participant UI as Floor Monitor UI
    participant API as FastAPI Backend
    participant Sim as Simulation Engine
    participant Macho as Macho ReAct Copilot
    participant LLM as Local Ollama (llama3.2)

    Note over Sim: Operator Murugan (EMP-103) is absent on Line 1 Stage 3 (Milling WS 1/1)
    Sim->>Sim: WS capacity drops to 0% -> Line 1 Halts at 324/960 units
    Sim->>API: Update Line 1 state = Halted (Bottleneck: Milling)
    API->>UI: Live telemetry push (Red bottleneck badge displayed)

    User->>UI: Opens Macho Copilot: "why is line 1 halted ..??"
    UI->>API: POST /api/agent/query (factory_id="F001", line_id="L01")
    API->>Macho: Execute ReAct query
    Macho->>LLM: Generate Thought + Action
    LLM-->>Macho: Action: get_shortfall_analysis + get_employees_data
    Macho->>Sim: Fetch station staffing & machine status
    Sim-->>Macho: Milling WS 1/1 unstaffed (EMP-103 absent, no floater)
    Macho->>LLM: Synthesize direct 2-line root cause diagnosis
    LLM-->>Macho: 2-line answer citing [E1]
    Macho-->>API: Return response + Evidence citations
    API-->>UI: Macho responds in chat drawer

    User->>UI: Sends: "assign a floater to the WS"
    UI->>API: POST /api/agent/query (multi-turn context retained)
    API->>Macho: Execute ReAct query
    Macho->>Sim: Check Free Pool -> Arunachalam S. (EMP-133) available
    Macho->>Sim: Action: assign_floater(station="L01-M03", floater_id="EMP-133")
    Sim->>Sim: Assign floater, restore Milling capacity to 100%
    Sim-->>Macho: Assignment successful
    Macho-->>UI: Confirmation: "Arunachalam S. (EMP-133) assigned to Milling WS 1/1"
    Sim->>API: Line 1 recovered to RUNNING
    API->>UI: UI turns green, live production resumes advancing (324 -> 338 units)
```

---

### 3. Advanced Hybrid RAG Knowledge Retrieval Pipeline
This flowchart illustrates how unstructured plant documentation is vectorized, indexed, and retrieved during tool execution with Parent-Child chunking, RRF fusion, and Cross-Encoder reranking:

```mermaid
flowchart LR
    subgraph Ingestion ["Ingestion & Incremental Indexing"]
        Docs["SOPs & Manuals (docs/factories/*.md)"]
        Manifest[("Manifest Hash Tracker (document_manifest.json)")]
        PChunker["Parent-Child Chunker (Parent: 1400ch, Child: 380ch)"]
        DenseEmbed["Dense Embeddings (MiniLM / nomic)"]
        BM25Idx[("BM25 Inverted Index (IDF + Suffix Stemming)")]
        VStore[("Vector DB Cache")]
    end

    subgraph Retrieval ["Hybrid Retrieval & Scoring"]
        Query["search_factory_documents(query)"]
        BM25Search["BM25 Lexical Search"]
        VectorSearch["Dense Vector Search"]
        RRF["Reciprocal Rank Fusion (RRF k=60)"]
    end

    subgraph Rerank ["Reranking & Confidence Gating"]
        CrossEnc["Cross-Encoder Joint Reranker"]
        ConfGate{"Confidence >= 0.25?"}
        Fallback["insufficient_context Protocol"]
        Citations["Parent Context & Citations [doc | p.X > sec]"]
    end

    Docs --> Manifest
    Manifest -->|New/Modified| PChunker
    PChunker --> DenseEmbed
    PChunker --> BM25Idx
    DenseEmbed --> VStore
    Query --> BM25Search
    Query --> VectorSearch
    BM25Idx --> BM25Search
    VStore --> VectorSearch
    BM25Search --> RRF
    VectorSearch --> RRF
    RRF --> CrossEnc
    CrossEnc --> ConfGate
    ConfGate -->|No / Low Score| Fallback
    ConfGate -->|Yes| Citations
```

---

### 4. Proactive Telemetry Monitoring & Raw Material Depletion Watchdog Workflow
The following diagram illustrates how Macho continuously monitors inventory telemetry and surfaces proactive depletion alerts:

```mermaid
flowchart TD
    subgraph TelemetryEngine ["Macho Telemetry Analysis Reporter (5s Tick Loop)"]
        InvCSV["inventory.csv Live Telemetry"]
        CapMap["Nominal Warehouse Capacities (F001 / F002 / F003)"]
        CalcBurn["Burn Rate & Runout Hours Calculation"]
        CheckThresh{"Remaining Stock <= 20%?"}
        CriticalCheck{"Available <= 0?"}
        CritAlert["🚨 CRITICAL Alert (Plant Line Halted)"]
        WarnAlert["⚠️ WARNING Alert (Near Depletion)"]
        StoreJSON[("Active Telemetry State (telemetry_alerts.json)")]
        APIEndpoint["GET /api/factories/{id}/telemetry-alerts"]
    end

    subgraph UIInteraction ["User Experience & UI Interaction"]
        RobotBadge["Macho Companion Alert Badge (⚠️ Ask Macho)"]
        UserClick["User Clicks Macho Robot"]
        DrawerAlert["Drawer Slide-Over Alert Card ([X% stock left])"]
        ActionBtn["Button: Ask Macho for Restocking & Shortfall Plan"]
        CopilotChat["Autonomous Shortfall & Procurement Strategy"]
    end

    InvCSV --> CalcBurn
    CapMap --> CalcBurn
    CalcBurn --> CheckThresh
    CheckThresh -->|Yes| CriticalCheck
    CriticalCheck -->|Yes| CritAlert
    CriticalCheck -->|No| WarnAlert
    CritAlert --> StoreJSON
    WarnAlert --> StoreJSON
    StoreJSON --> APIEndpoint
    APIEndpoint --> RobotBadge
    RobotBadge --> UserClick
    UserClick --> DrawerAlert
    DrawerAlert --> ActionBtn
    ActionBtn --> CopilotChat
```

---

### 5. Operational Step-by-Step Workflows

#### Workflow A: Bottleneck Detection & Autonomous Floater Dispatch
1. **Bottleneck Occurrence:** A primary operator is marked absent or a floater is unassigned. If the workstation is a single-machine bottleneck (`WS 1/1`), stage capacity drops to `0%`, and the line halts immediately.
2. **Floor Alerting:** The UI renders an urgent red badge (`BOTTLENECK: [STAGE] (OPERATOR ABSENT)`), turns the stage border red, and halts the active order counter.
3. **Macho Diagnostic Inquiry:** The supervisor opens the Macho drawer and asks for the stoppage cause. Macho triggers `get_shortfall_analysis`, `get_employees_data`, and `get_machine_status`.
4. **2-Line Precision Diagnosis:** Macho delivers a direct, two-line answer identifying the unstaffed workstation, the absent employee ID, and recommending immediate floater assignment.
5. **Autonomous Dispatch:** The supervisor instructs Macho to assign a floater. Macho validates cross-training certifications, selects the top available standby operator from the Free Pool, and calls `assign_floater`.
6. **Recovery & Line Resumption:** The workstation returns to operational capacity, the line turns green (`RUNNING`), and production units immediately begin incrementing.

#### Workflow B: Batch Work Order Loading & Multi-Line Scheduling
1. **Target Quota Monitoring:** Each line runs an active order towards its batch target (e.g., `960` units for `ORD-015`, `900` units for `ORD-013`).
2. **Order Completion Detection:** Once cumulative units meet or exceed the batch target, the line enters `COMPLETED` state.
3. **Queue Selection:** The dispatcher inspects pending work orders in queue for that production line.
4. **1-Click Batch Loading:** Pressing `[► Load: ORD-XXX]` calls `POST /api/factories/{id}/orders/dispatch-next`.
5. **Telemetry Reset:** The active order indicator updates to the new contract, active batch units reset to 0, and sequential workstation flow begins immediately.

#### Workflow C: Proactive Raw Material Depletion Watchdog
1. **Continuous Telemetry Polling:** The background simulator evaluates live stock consumption in `inventory.csv` every 5 seconds.
2. **$\le 20\%$ Threshold Evaluation:** When available stock for any raw material drops to 20% or less of warehouse nominal capacity, Macho generates a `WARNING` alert. If stock drops to 0, a `CRITICAL` alert is triggered.
3. **Non-Intrusive Companion Badge:** The Macho companion robot displays an alert symbol (`⚠️ Ask Macho`) without cluttering the canvas with text popups.
4. **Drawer Alert Card:** Clicking Macho opens the slide-over drawer and renders the alert card displaying the exact remaining percentage (`[16.4% stock left]`), true capacity (`2,954.6 / 18,000.0 pieces`), safety threshold (`3,000.0`), and estimated hours of supply remaining.
5. **Autonomous Restocking Strategy:** Clicking `Ask Macho for Restocking & Shortfall Plan →` triggers Macho to execute an end-to-end BOM assessment and formulate an immediate restocking priority schedule.

---

## 📸 3. Complete System Walkthrough & Screenshots

### 1. Live Production Floor Monitor
![Production Floor Monitor](docs/screenshots/01_floor_monitor.png)
* **Real-Time Visual Routing:** Visualizes 10-station sequential and parallel manufacturing stages across active production lines:
  - **Line 01 (Engine Components):** Real-time bottleneck alert highlighting operator absence (`SLOWED: CNC TURNING (OPERATOR ABSENT - 50% SPEED)`), active batch units, and single-click standby floater deployment.
  - **Line 02 (Transmission Gears):** Active order tracking (`ORD-010 Planetary Ring Gear Set`), progressive shift throughput, and floating Macho robot companion.
  - **Draggable Macho Companion:** Persistent, non-blocking on-floor companion robot that can be dragged anywhere or clicked to launch the operations slide-over console.

---

### 2. Shift Workforce Staffing & Absentee Roll-Call
![Workforce Roll-Call](docs/screenshots/02_macho_absent_operators.png)
* **Plant-Wide Absentee Tracking:** Natural language inquiry (*"list of operators absent today"*) triggers structured roll-call investigation through `get_employees_data`.
* **Exact Workstation Pinpointing:** Identifies each absent operator by name, employee ID, assigned workstation, and line:
  1. **Karthik Subramanian (`EMP-102`)** — CNC Turning (WS 2/2), Line: Engine Components
  2. **Saravanan Natarajan (`EMP-114`)** — Gear Hobbing (WS 3/3), Line: Transmission Gears
* **Evidence Grounding:** Anti-hallucination citations verify claims directly against shift records.

---

### 3. Relief Floater Allocations & Replacement Telemetry
![Relief Floaters](docs/screenshots/03_macho_replacements.png)
* **Distinct Floater Tracking:** Macho distinguishes absent operators from the relief floaters covering them (*"The replacements for the absent operators are as follows..."*):
  - **Murugan Palanisamy** is covered by **Suresh Mani** (`EMP-131`).
  - **Saravanan Natarajan** is covered by **Kishore Kumar** (`EMP-132`).
* **Multi-Turn Context Retention:** Macho remembers previous conversation context across multiple turns without resetting between queries.

---

### 4. Root Cause Analysis (RCA) — Machine Stage Diagnostics
![Stage Diagnostics](docs/screenshots/04_macho_stage3_diagnostic.png)
* **Granular RCA:** Prompting *"why is line 1 stage 3 not running ..?"* initiates an automated 5-Whys diagnostic procedure.
* **Correlated Failure Telemetry:** Directly correlates machine stoppages (tool wear / coolant pump faults) with defect yield metrics (1 rework unit, 1 rejected unit) from inspection records.

---

### 5. Autonomous Incident Remediation: Stoppage Diagnosis & Floater Dispatch

This end-to-end operational sequence illustrates Macho diagnosing an unstaffed bottleneck and deploying relief coverage to restore production:

| 1. Stoppage Diagnosis (*"why is line 1 not running .??"*) | 2. Autonomous Floater Dispatch (*"assign floater to the ws"*) |
| :---: | :---: |
| ![Stoppage Diagnosis](docs/screenshots/05_stoppage_diagnosis.png) | ![Floater Dispatch](docs/screenshots/06_floater_dispatch.png) |
| **Direct 2-Line Diagnosis:** Macho identifies that Line 01 is halted because workstation Milling (`WS 1/1`) is unstaffed due to operator Murugan Palanisamy being absent with no floater assigned, and immediately recommends assigning a relief floater. | **Autonomous Dispatch:** Upon receiving *"assign floater to the ws"*, Macho validates cross-training qualifications, executes `assign_floater`, and deploys **Arunachalam S. (`EMP-133`)** directly to the station. |

#### Restored Line 01 Throughput & Active Floater Relief
![Line 01 Production Resumed](docs/screenshots/07_production_resumed.png)
* **Floor Recovery:** Milling (`WS 1/1`) shows `FLOATER RELIEF: Arunachalam... [✕ Release]` alongside CNC Turning relief (`Suresh Mani`). Line 01 operational status returns to active, safely fulfilling Order `ORD-015` for Aston Martin Lagonda.

---

### 6. Proactive Telemetry Analysis & Raw Material Depletion Watchdog
![Macho Telemetry Depletion Alert](docs/screenshots/10_macho_telemetry_depletion_alert.jpg)
* **Real-Time Telemetry Watchdog:** Macho continuously audits plant floor raw material telemetry against nominal warehouse storage capacity (`NOMINAL_CAPACITIES`).
* **Calibrated $\le 20\%$ Near-Depletion Warning:** When inventory falls below 20% of warehouse capacity, Macho flags the condition with an actionable directive inside the slide-over drawer:
  - **Live Alert Headline:** `Macho Warning: 1 raw material(s) near depletion threshold in F001.`
  - **Material & Stock Breakdown:** Pinpoints `Steel blanks (STL)` at `16.4% stock left` with `2,954.6 / 18,000.0 pieces` in stock against a safety reorder threshold of `3,000.0 pieces`.
  - **Runout & Operational Impact:** Predicts `~469.0 hours of supply remaining` and identifies `Line 01` as the immediate downstream risk.
  - **1-Click Restocking Action:** The `Ask Macho for Restocking & Shortfall Plan →` button triggers Macho to formulate an autonomous batch procurement and production protection strategy.
* **Uncluttered Companion:** Replaces intrusive on-canvas chat bubbles with an unobtrusive pulsing alert badge (`⚠️ Ask Macho`) on the floating companion, keeping the production line route clean until the supervisor clicks to inspect.

---

## 🚀 4. Getting Started

### 1. Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com) installed and added to your system PATH

### 2. Start Ollama
Start Ollama and pull your preferred local LLM:
```powershell
ollama serve
ollama pull llama3.2
```

### 3. Launch FactoryOS V2
From the project root directory:
```powershell
python -m uvicorn v2.api:app --host 127.0.0.1 --port 8000
```

### 4. Access the Platform
- **Production Floor Monitor:** [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Interactive OpenAPI Documentation:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Alternative 3D / Control Room Views:**
  - Creative Layout: [http://127.0.0.1:8000/static/creative.html](http://127.0.0.1:8000/static/creative.html)
  - Industrial Control Room: [http://127.0.0.1:8000/static/control-room.html](http://127.0.0.1:8000/static/control-room.html)

---

## 🧪 5. Testing & Verification Suite

FactoryOS V2 includes automated self-checks and benchmark suites:

### 1. Core End-to-End Self Checks
Validates order contracts, HTTP error status formatting, hybrid RAG searches, citation formatting, insufficient-context guardrails, and Macho telemetry alert endpoints:
```powershell
python -m v2.test_core
```

### 2. RAGAS Evaluation Benchmark Suite
Evaluates Faithfulness, Answer Relevancy, Context Precision (MAP@K), and Context Recall across factory test cases:
```powershell
python -m v2.evals --ragas
```

### 3. Macho Telemetry Analysis Reporter Smoke Test
Executes stand-alone depletion analysis across multi-plant inventories, checking burn rates, nominal warehouse capacities, and $\le 20\%$ threshold filtering:
```powershell
python -m v2.telemetry_reporter
```
