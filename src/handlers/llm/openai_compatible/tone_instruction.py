"""Optional emotional-tone suffix for the LLM system prompt (Task 2-ALT).

Standalone, no project imports — unit-testable in isolation alongside
``filler_controller.py``. Called from the LLM handler AFTER
``build_personalized_system_prompt`` has returned, so the participant block
(between the ``[BEGIN PARTICIPANT INFO]`` / ``[END PARTICIPANT INFO]`` markers
created by ``participant_info.py``) is treated as **opaque**: this module only
ever appends; it never slices, parses, or rewrites the participant block.

Contract
--------
``apply_tone_instruction(prompt, enabled, instruction)`` returns ``prompt``
**unchanged** when:
  - ``enabled`` is False, or
  - ``instruction`` is empty / whitespace-only.

Otherwise it returns ``prompt.rstrip() + "\\n\\n" + instruction.strip()``. The
full input prompt (including any personalization block) appears as a prefix of
the output, byte-for-byte.
"""


def apply_tone_instruction(prompt: str, enabled: bool, instruction: str) -> str:
    if not enabled:
        return prompt
    text = (instruction or "").strip()
    if not text:
        return prompt
    return prompt.rstrip() + "\n\n" + text
