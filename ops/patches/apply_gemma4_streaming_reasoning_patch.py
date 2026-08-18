#!/usr/bin/env python3
"""Backport vLLM PR #48262 to the pinned vLLM 0.25.1 image, plus one local fix.

With Gemma4 thinking enabled, the pre-fix streaming parser treated every new
model turn as an already-open reasoning channel. If the model returned a plain
final answer without channel markers, streaming emitted it only as
``delta.reasoning`` whereas non-streaming correctly returned ``content``.

Part 1 is the verbatim upstream fix. Part 2 is *not* upstream: the parser never
declared ``<turn|>`` (the turn-end token, vocab id 106 -- distinct from the
``<|turn>`` opener the parser already knows) as a terminal, so once part 1 lets
a turn start in ``CONTENT`` the token falls through to the client as a literal
``<turn|>`` at the end of every streamed answer. Non-streaming never showed it.
"""

from __future__ import annotations

import os

import vllm


target = os.environ.get(
    "GEMMA4_PARSER_PATH",
    os.path.join(os.path.dirname(vllm.__file__), "parser", "gemma4.py"),
)
source = open(target, encoding="utf-8").read()


def replace_once(text: str, old: str, new: str, what: str) -> str:
    """anchor가 정확히 한 번 나올 때만 치환한다."""
    count = text.count(old)
    assert count == 1, f"{what}: expected exactly 1 anchor, found {count}"
    return text.replace(old, new, 1)


# ── Part 1: upstream PR #48262 ─────────────────────────────────────────────
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

source = replace_once(source, old, new, "adjust_initial_state_from_prompt")

# ── Part 2 (local): absorb the <turn|> turn-end token ──────────────────────
source = replace_once(
    source,
    'TOOL_CALL_END = "<tool_call|>"\n',
    'TOOL_CALL_END = "<tool_call|>"\nTURN_END = "<turn|>"\n',
    "TURN_END constant",
)

source = replace_once(
    source,
    '''            "TOOL_END": TOOL_CALL_END,
            "CALL_PREFIX": "call:",''',
    '''            "TOOL_END": TOOL_CALL_END,
            "TURN_END": TURN_END,
            "CALL_PREFIX": "call:",''',
    "terminals TURN_END",
)

source = replace_once(
    source,
    '''        token_id_terminals={
            "THINK_START": CHANNEL_START,
            "THINK_END": CHANNEL_END,
            "TOOL_START": TOOL_CALL_START,
            "TOOL_END": TOOL_CALL_END,
        },''',
    '''        token_id_terminals={
            "THINK_START": CHANNEL_START,
            "THINK_END": CHANNEL_END,
            "TOOL_START": TOOL_CALL_START,
            "TOOL_END": TOOL_CALL_END,
            "TURN_END": TURN_END,
        },''',
    "token_id_terminals TURN_END",
)

source = replace_once(
    source,
    '''            # Absorb a bare <channel|> that arrives after we already
            # returned to CONTENT; prevents leaking it as TEXT_CHUNK.
            (ParserState.CONTENT, "THINK_END"): Transition(
                ParserState.CONTENT,
                (),
            ),
        },''',
    '''            # Absorb a bare <channel|> that arrives after we already
            # returned to CONTENT; prevents leaking it as TEXT_CHUNK.
            (ParserState.CONTENT, "THINK_END"): Transition(
                ParserState.CONTENT,
                (),
            ),
            # -- Turn end --
            # ``<turn|>`` closes the model turn. It carries no content, so
            # absorb it rather than leaking it as TEXT_CHUNK; from REASONING
            # it also implicitly concludes the reasoning block.
            (ParserState.CONTENT, "TURN_END"): Transition(
                ParserState.CONTENT,
                (),
            ),
            (ParserState.REASONING, "TURN_END"): Transition(
                ParserState.CONTENT,
                (EventType.REASONING_END,),
            ),
        },''',
    "TURN_END transitions",
)

open(target, "w", encoding="utf-8").write(source)

verified = open(target, encoding="utf-8").read()
assert "def _prompt_ends_in_open_reasoning" in verified
assert "if not self._prompt_ends_in_open_reasoning(prompt_token_ids):" in verified
assert verified.count('"TURN_END": TURN_END') == 2
assert '(ParserState.CONTENT, "TURN_END")' in verified
assert '(ParserState.REASONING, "TURN_END")' in verified
print("gemma4 parser patch applied: upstream PR #48262 + local <turn|> absorption")
