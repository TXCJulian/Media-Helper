"""Auto-encoder feature: rule-driven HandBrake encoding of library files.

Owns all state and policy -- watch config, presets, rules, job history, and
every filesystem mutation. The HandBrake_Video-Encoder service is a stateless
executor reached over HTTP; it decides nothing.
"""
