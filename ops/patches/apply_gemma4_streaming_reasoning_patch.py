#!/usr/bin/env python3
"""Backport vLLM PR #48262 to the pinned vLLM 0.25.1 image.

With Gemma4 thinking enabled, the pre-fix streaming parser treated every new
model turn as an already-open reasoning channel. If the model returned a plain
final answer without channel markers, streaming emitted it only as
``delta.reasoning`` whereas non-streaming correctly returned ``content``.
"""

from __future__ import annotations

import os

import vllm


target = os.environ.get(
    "GEMMA4_PARSER_PATH",
    os.path.join(os.path.dirname(vllm.__file__), "parser", "gemma4.py"),
)
source = open(target, encoding="utf-8").read()

old = '''    def adjust_initial_state_from_prompt(self, prompt_token_ids: Sequence[int]) -> None:
        \"\"\"Pre-initialise the engine to ``REASONING`` when the prompt does
        not already end with reasoning concluded.

        This covers the post-tool-response continuation case where the chat
        template leaves the prompt ending inside an open ``<|channel>``
        block (issue #45834). It is also safe in the common new-turn case
        where the model itself emits ``<|channel>`` first: the no-op
        ``(REASONING, THINK_START)`` transition swallows it, and the
        ``thought\\n`` prefix in the first reasoning chunk is stripped by
        ``_events_to_delta`` as it already is in the default flow.
        \"\"\"
        if self.is_reasoning_end(list(prompt_token_ids)):
            return
        self._engine.reset(initial_state=ParserState.REASONING)
        # Prevent a later default ``initialize_streaming()`` (e.g. from
        # ``ParserEngineReasoningAdapter.extract_reasoning_streaming``) from
        # clobbering this with ``CONTENT``.
        self._streaming_initialized = True
'''

new = '''    def _prompt_ends_in_open_reasoning(self, prompt_token_ids: Sequence[int]) -> bool:
        \"\"\"Whether the prompt tail is inside an open ``<|channel>`` block.\n\n        Scans backwards: a ``<|channel>`` start token seen before any\n        closing or turn-boundary token means the block is still open.\n        \"\"\"
        start_id = self._reasoning_start_token_id
        if start_id is None:
            return False
        boundary_ids = {
            tid
            for tid in (
                self._reasoning_end_token_id,
                self._tool_call_token_id,
                self._new_turn_token_id,
                self._tool_response_token_id,
            )
            if tid is not None
        }
        for tid in reversed(prompt_token_ids):
            if tid == start_id:
                return True
            if tid in boundary_ids:
                return False
        return False

    def adjust_initial_state_from_prompt(self, prompt_token_ids: Sequence[int]) -> None:
        \"\"\"Pre-initialise the engine only for an open reasoning channel.\n\n        A plain new model turn must start as content: the model may answer\n        directly without channel markers, and streaming must then agree with\n        the non-streaming parser.\n        \"\"\"
        if not self._prompt_ends_in_open_reasoning(prompt_token_ids):
            return
        self._engine.reset(initial_state=ParserState.REASONING)
        # Prevent a later default ``initialize_streaming()`` (e.g. from
        # ``ParserEngineReasoningAdapter.extract_reasoning_streaming``) from
        # clobbering this with ``CONTENT``.
        self._streaming_initialized = True
'''

assert old in source, "Gemma4 streaming parser pre-fix anchor not found"
open(target, "w", encoding="utf-8").write(source.replace(old, new, 1))

verified = open(target, encoding="utf-8").read()
assert "def _prompt_ends_in_open_reasoning" in verified
assert "if not self._prompt_ends_in_open_reasoning(prompt_token_ids):" in verified
print("gemma4 streaming reasoning parser patch applied: upstream PR #48262")
