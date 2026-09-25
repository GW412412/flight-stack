"""Ollama chat loop - ADVISORY flight assistant only. Linux native.

Never hard authority: ELRS RC and mavlink_hub remain the only actuation paths
for flight-critical and spray commands. This agent may call tools that read
status or write QGC plan files for human review — it must not send RC, bypass
SafetyGate, or call DO_SPRAYER except indirectly via hub (hub still gates).

    pip install ollama
    ollama pull gemma2:27b
"""
import json

import ollama

from tools import TOOLS, execute_tool

MODEL = "gemma2:27b"

SYSTEM = (
    "You are an ADVISORY flight operations assistant for a precision-agriculture "
    "drone. You are NOT hard authority. "
    "Rules: "
    "(1) Act only through tools; never invent telemetry. "
    "(2) Never claim an action happened unless a tool returned success. "
    "(3) Before arming or spraying is discussed, run the preflight checklist. "
    "(4) Be concise; the operator is busy. "
    "(5) SafetyGate blockers are absolute — never suggest bypassing them. "
    "(6) Never send RC / ELRS stick commands or tell the operator to ignore the "
    "transmitter; ELRS remains hard flight authority. "
    "(7) Never instruct anyone to fire the sprayer outside mavlink_hub; "
    "request_spray_enable is deprecated and advisory only. "
    "(8) Live spray uses conf>=0.50, georeg>=0.70, 0.5 L concentrate tank, and "
    "dose V=clamp(0.25+1.25*A_cm2, 0.1, 2.5) mL — do not invent other numbers."
)

HISTORY = [{"role": "system", "content": SYSTEM}]


def chat(user_text):
    HISTORY.append({"role": "user", "content": user_text})
    resp = ollama.chat(model=MODEL, messages=HISTORY, tools=TOOLS)
    while resp.message.tool_calls:
        for call in resp.message.tool_calls:
            print(f"[tool] {call.function.name}({call.function.arguments})")
            result = execute_tool(call.function.name, call.function.arguments or {})
            print(f"[tool ->] {json.dumps(result, default=str)[:300]}")
            HISTORY.append({"role": "tool", "name": call.function.name,
                            "content": json.dumps(result, default=str)})
        resp = ollama.chat(model=MODEL, messages=HISTORY, tools=TOOLS)
    HISTORY.append({"role": "assistant", "content": resp.message.content})
    return resp.message.content


if __name__ == "__main__":
    print("Advisory assistant ready (not RC authority). Type 'quit' to exit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quit", "exit"):
            break
        if text:
            print("her>", chat(text))
