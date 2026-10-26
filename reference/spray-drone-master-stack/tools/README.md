# Development tools

This directory is reserved for non-flight development, validation and diagnostic utilities.

Any tool added here must document its inputs, outputs, dependencies and failure modes. Diagnostics must default to read-only. Tools must not provide a path around the command gateway or pilot takeover latch. Do not store credentials, personal data or operational secrets in this directory.

No hardware-control utility is implemented here yet.