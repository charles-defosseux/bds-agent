"""The router is a pure function of the state: tested without a model, in the course order."""
from langgraph.graph import END

from agent.graph import router


def state(pending, tool_calls=0, steps=1, max_steps=4):
    return {"pending": pending, "tool_calls": [{}] * tool_calls, "steps": steps, "max_steps": max_steps}


def call(tool, **args):
    return {"type": "call", "tool": tool, "args": args}


def test_final_answer_ends_even_when_the_budget_is_spent():
    assert router(state({"type": "final"}, tool_calls=4, steps=5)) == END


def test_budget_reached_gives_up_even_for_a_write():
    assert router(state(call("pay_expense", expense_id="E-001"), tool_calls=4)) == "give_up"
    assert router(state(call("get_member", member_id="M003"), tool_calls=4)) == "give_up"
    assert router(state(call("ask_user", question="?"), tool_calls=4)) == "give_up"


def test_model_calls_are_bounded_too():
    assert router(state(call("get_member", member_id="M003"), tool_calls=0, steps=5)) == "give_up"


def test_ask_user_is_a_control_tool():
    assert router(state(call("ask_user", question="Quel Thomas ?"))) == "ask_user"


def test_dangerous_writes_go_through_the_guard():
    for tool, args in [("pay_expense", {"expense_id": "E-001"}), ("reimburse_member", {"expense_id": "E-003"}),
                       ("refund_event_registrations", {"event_id": "EV01"}), ("cancel_event", {"event_id": "EV01"}),
                       ("update_member_iban", {"member_id": "M009", "new_iban": "FR38"})]:
        assert router(state(call(tool, **args))) == "guard", tool


def test_every_email_goes_through_the_guard():
    assert router(state(call("send_email", recipient_member_ids=["M006", "M009"], subject="s", body="b"))) == "guard"
    assert router(state(call("send_email", recipient_member_ids=["M006"], subject="s", body="b"))) == "guard"


def test_keyed_write_without_approval_goes_straight_to_write_tool():
    assert router(state(call("record_dues_payment", member_id="M003", amount_eur=30))) == "write_tool"


def test_reads_and_unknown_tools_go_to_read_tool():
    assert router(state(call("get_ledger"))) == "read_tool"
    assert router(state(call("drop_database"))) == "read_tool"  # the client answers not_found, nothing runs


def test_unreadable_arguments_go_back_to_the_model():
    assert router(state({"type": "invalid", "tool": "get_member", "args": {}}, tool_calls=1)) == "think"
    assert router(state({"type": "invalid", "tool": "get_member", "args": {}}, tool_calls=4)) == "give_up"


def test_remember_preference_is_a_control_tool():
    assert router(state(call("remember_preference", key="email_signature", value="Léa"))) == "remember"
