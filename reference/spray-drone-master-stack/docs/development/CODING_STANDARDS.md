# Coding standards

- Use Python type hints for public functions.
- Prefer small functions and explicit state transitions.
- Validate external input at component boundaries.
- Use monotonic time for local expiry and timeout measurements; document any wall-clock timestamps used for logs.
- Avoid hidden global state and circular imports.
- Keep hardware I/O behind explicit interfaces.
- Never place credentials in source, config examples or logs.
- Add tests for fault paths as well as normal behavior.
- Use descriptive names and explain safety-critical decisions in comments and documentation.