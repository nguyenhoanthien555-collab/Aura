# AURA System Architecture

## Overview
AURA is an autonomous AI companion & assistant system built with Python, FastAPI, AsyncIO, and specialized capability registries.

## Core Subsystems
1. **Core Runtime (`core/`)**:
   - `runtime.py`: Lifecycle management, background task orchestrator.
   - `pipeline.py`: Step-by-step prompt-to-tool-execution pipeline.
   - `capabilities/`: System capabilities (time, processes, desktop, memory, android).
2. **Server (`server/`)**:
   - FastAPI server managing WebSocket streaming and REST APIs.
   - `routes/settings.py`: Dynamic settings management with restart triggers.
3. **Tools & Providers (`tools/`)**:
   - `executor.py`: Safe subprocess and tool call execution with timeouts.
   - `registry.py`: Capability and tool discovery.
4. **Android Companion (`android/`)**:
   - ADB bridge, XML UI tree parser, visual screen capture, remote touch injection.
5. **Local LLM Integration**:
   - 100% Local OpenAI-compatible API (`http://127.0.0.1:8080/v1`) via `llama-server.exe` running `gpt-oss-20b-MXFP4`.
