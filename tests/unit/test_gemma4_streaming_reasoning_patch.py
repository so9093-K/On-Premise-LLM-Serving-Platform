from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCH_SCRIPT = ROOT / "ops" / "patches" / "apply_gemma4_streaming_reasoning_patch.py"


OLD_REASONING = '''    def adjust_initial_state_from_prompt(self, prompt_token_ids: Sequence[int]) -> None:
        """Pre-initialise the engine to ``REASONING`` when the prompt does
        not already end with reasoning concluded.

        This covers the post-tool-response continuation case where the chat
        template leaves the prompt ending inside an open ``<|channel>``
        block (issue #45834). It is also safe in the common new-turn case
        where the model itself emits ``<|channel>`` first: the no-op
        ``(REASONING, THINK_START)`` transition swallows it, and the
        ``thought\\n`` prefix in the first reasoning chunk is stripped by
        ``_events_to_delta`` as it already is in the default flow.
        """
        if self.is_reasoning_end(list(prompt_token_ids)):
            return
        self._engine.reset(initial_state=ParserState.REASONING)
        # Prevent a later default ``initialize_streaming()`` (e.g. from
        # ``ParserEngineReasoningAdapter.extract_reasoning_streaming``) from
        # clobbering this with ``CONTENT``.
        self._streaming_initialized = True
'''

UPSTREAM_REASONING = '''    def _prompt_ends_in_open_reasoning(self, prompt_token_ids: Sequence[int]) -> bool:
        return True

    def adjust_initial_state_from_prompt(self, prompt_token_ids: Sequence[int]) -> None:
        if not self._prompt_ends_in_open_reasoning(prompt_token_ids):
            return
        self._engine.reset(initial_state=ParserState.REASONING)
        self._streaming_initialized = True
'''

COMMON = '''
TOOL_CALL_END = "<tool_call|>"

def config():
    return ParserEngineConfig(
        terminals={
            "TOOL_END": TOOL_CALL_END,
            "CALL_PREFIX": "call:",
        },
        token_id_terminals={
            "THINK_START": CHANNEL_START,
            "THINK_END": CHANNEL_END,
            "TOOL_START": TOOL_CALL_START,
            "TOOL_END": TOOL_CALL_END,
        },
        transitions={
            # Absorb a bare <channel|> that arrives after we already
            # returned to CONTENT; prevents leaking it as TEXT_CHUNK.
            (ParserState.CONTENT, "THINK_END"): Transition(
                ParserState.CONTENT,
                (),
            ),
        },
    )
'''


def _run_patch(tmp_path: Path, reasoning: str) -> tuple[str, str]:
    target = tmp_path / "gemma4.py"
    target.write_text(COMMON + "\n" + reasoning, encoding="utf-8")
    env = os.environ.copy()
    env["GEMMA4_PARSER_PATH"] = str(target)

    completed = subprocess.run(
        [sys.executable, str(PATCH_SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    return target.read_text(encoding="utf-8"), completed.stdout


def _assert_turn_end_contract(text: str) -> None:
    assert 'TURN_END = "<turn|>"' in text
    assert text.count('"TURN_END": TURN_END') == 2
    assert '(ParserState.CONTENT, "TURN_END")' in text
    assert '(ParserState.REASONING, "TURN_END")' in text


def test_patch_backports_reasoning_fix_on_qualified_layout(tmp_path):
    text, output = _run_patch(tmp_path, OLD_REASONING)

    assert "def _prompt_ends_in_open_reasoning" in text
    assert "if not self._prompt_ends_in_open_reasoning(prompt_token_ids):" in text
    _assert_turn_end_contract(text)
    assert "reasoning_fix=backported" in output
    assert "turn_end=applied" in output


def test_patch_accepts_upstream_reasoning_fix_and_keeps_local_turn_end(tmp_path):
    text, output = _run_patch(tmp_path, UPSTREAM_REASONING)

    assert text.count("def _prompt_ends_in_open_reasoning") == 1
    _assert_turn_end_contract(text)
    assert "reasoning_fix=upstream" in output
    assert "turn_end=applied" in output
