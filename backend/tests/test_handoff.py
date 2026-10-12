"""3.6: the developer hand-off text (pure rendering)."""

from __future__ import annotations

from typing import Any

from searchos.models import Action
from searchos.planner import handoff
from searchos.planner.catalog import ACTION_TYPES

URL = "https://shop.example/guide"


def act(type_: str, value: Any, *, field: str = "f", target: str | None = URL, **kw: Any) -> Action:
    return Action(
        type=type_,
        risk_class=ACTION_TYPES[type_].risk_class,
        target_url=target,
        field=field,
        proposed_value=value,
        evidence=kw.pop("evidence", {"why": ["Because."]}),
        **kw,
    )


def lines(action: Action) -> str:
    return "\n".join(handoff.snippet(action))


def test_title_and_meta_are_escaped_ready_to_paste_html() -> None:
    assert lines(act("rewrite_title", 'Beans & "Brews" <best>')) == (
        "Replace the <title> element with: <title>Beans &amp; &quot;Brews&quot; &lt;best&gt;</title>"
    )
    assert (
        lines(act("rewrite_meta", 'It "works"'))
        == 'Set: <meta name="description" content="It &quot;works&quot;">'
    )


def test_alt_names_the_image() -> None:
    assert lines(act("add_alt", "Dark roast beans", field="alt:/img/dark.png")) == (
        'On the image /img/dark.png: alt="Dark roast beans"'
    )


def test_jsonld_is_wrapped_in_a_script_block() -> None:
    out = lines(act("add_jsonld", {"@type": "Organization", "name": "Beanly"}))
    assert out.startswith("Add this block in <head>:") and '<script type="application/ld+json">' in out
    assert out.endswith("</script>") and '"name": "Beanly"' in out
    assert lines(act("fix_jsonld", "{}")).startswith("Replace the existing block:")


def test_technical_changes_are_spelled_out() -> None:
    assert lines(act("fix_canonical", "https://shop.example/a")) == (
        'Set: <link rel="canonical" href="https://shop.example/a">'
    )
    redirect = act(
        "collapse_redirect",
        {"from": "https://shop.example/old", "to": "https://shop.example/new", "status": 301},
    )
    assert "single 301 redirect to https://shop.example/new" in lines(redirect)
    assert "Remove the link to https://shop.example/x" in lines(
        act("fix_link", {"from": "https://shop.example/x", "mode": "remove"})
    )
    replace = act(
        "fix_link", {"from": "https://shop.example/x", "to": "https://shop.example/y", "mode": "replace"}
    )
    assert lines(replace) == "Change the link https://shop.example/x to https://shop.example/y."
    links = act("add_internal_links", {"links": [{"to": "https://shop.example/b", "anchor": "burr grinder"}]})
    assert lines(links) == 'In the existing text, link the words "burr grinder" to https://shop.example/b'


def test_a_value_less_action_has_no_snippet_and_briefs_list_steps() -> None:
    assert handoff.snippet(act("rewrite_title", None)) == []
    assert lines(act("refresh_content", {"kind": "brief", "steps": ["One", "Two"]})) == "- One\n- Two"


def test_render_groups_page_changes_and_technical_tasks() -> None:
    page = act("rewrite_title", "A title that is long enough to be sensible", current_value="Old")
    ticket = act(
        "dev_fix",
        {"kind": "brief", "steps": ["Fix the 404s."]},
        target=None,
        evidence={"why": ["Errors."], "urls": [f"https://shop.example/{i}" for i in range(40)]},
    )
    edited = act("fix_canonical", "https://shop.example/a", edited_by_user=True)
    text = handoff.render("Shop", "shop.example", [page, ticket, edited], generated_on="2026-10-13")
    assert text.startswith("Developer hand-off for Shop (shop.example)")
    assert "3 change(s)" in text
    assert text.index("PAGE CHANGES") < text.index("TECHNICAL TASKS")
    assert "Now: Old" in text and "Affected URLs (40):" in text and "... and 10 more" in text
    assert text.count("https://shop.example/") > 30
    assert "(The wording was edited by a person.)" in text
    assert "1. Rewrite" in text and "3. Technical fix" in text


def test_nothing_to_hand_over() -> None:
    text = handoff.render("Shop", "shop.example", [], generated_on="2026-10-13")
    assert "0 change(s)" in text and "Nothing is approved yet" in text
