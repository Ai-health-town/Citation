from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_demo_opens_and_every_result_screen_renders():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    button(app, "Open offline demo").click().run()
    assert not app.exception
    assert any(m.label == "Audited statements" and m.value == "3" for m in app.metric)
    for view in ["Sources", "Claim audit", "Proposed draft", "References", "Downloads", "History"]:
        app.radio(key="workspace").set_value(view).run()
        assert not app.exception, f"{view}: {app.exception}"


def test_real_structure_workflow_without_api_or_network():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.text_area(key="paper_input").set_value("The trial found a difference in reported attendance between groups (Smith, 2024).")
    app.text_area(key="references_input").set_value("Smith, J. (2024). A study of appointment attendance.")
    checkbox = next(c for c in app.checkbox if c.label == "Retrieve online source text")
    checkbox.uncheck().run()
    button(app, "Run citation audit").click().run()
    assert not app.exception
    audit = app.session_state["audits"][-1]
    assert audit.mode == "Structure only"
    assert audit.claims[0].status == "needs_review"
    assert audit.corrected_paper == audit.paper_text


def test_optional_password_gate():
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.secrets["APP_PASSWORD"] = "example-password-for-test"
    app.run()
    assert not app.exception
    assert not app.radio
    app.text_input[0].set_value("incorrect")
    button(app, "Open workspace").click().run()
    assert app.error
    app.text_input[0].set_value("example-password-for-test")
    button(app, "Open workspace").click().run()
    assert not app.exception
    assert app.radio


def test_reference_and_paper_input_modes_render():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio(key="paper_mode").set_value("Upload paper").run()
    assert not app.exception
    app.radio(key="reference_mode").set_value("Reference list URL").run()
    assert not app.exception
    app.radio(key="reference_mode").set_value("Upload references").run()
    assert not app.exception
