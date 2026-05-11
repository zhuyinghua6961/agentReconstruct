from agent_core.question_anchor import ANCHOR_PROMPT_SALT, prepend_question_anchor


def test_prepend_question_anchor_inserts_block():
    body = "BODY"
    out = prepend_question_anchor(body, "What is LFP?")
    assert out.startswith("=== USER QUESTION ANCHOR")
    assert "What is LFP?" in out
    assert out.endswith("\n\nBODY")
    assert ANCHOR_PROMPT_SALT == "|ht_question_anchor=v1|"


def test_prepend_question_anchor_empty_question_noop():
    assert prepend_question_anchor("x", "") == "x"
    assert prepend_question_anchor("x", "   ") == "x"
