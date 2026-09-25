"""Ollama chat loop - the flight assistant. Linux native.

    pip install ollama
    ollama pull gemma2:27b        # or another tool-capable model you have

Run with SITL + mav_node both up. Everything is logged to the console.
"""
import json

import ollama

from tools import TOOLS, execute_tool

MODEL = "gemma2:27b"

SYSTEM = (
    "You are the flight operations assistant for a precision-agriculture drone. "
    "Rules: (1) You act only through tools. (2) You never claim an action happened "
    "unless the tool returned success. (3) Before arming or spraying is discussed, "
    "run the preflight checklist. (4) Be concise; the operator is busy. "
    "(5) Safety gate blockers are absolute - never suggest bypassing them."
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
    print("Flight assistant ready. Type 'quit' to exit. (Start SITL + mav_node first.)")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quit", "exit"):
            break
        if text:
            print("her>", chat(text))
