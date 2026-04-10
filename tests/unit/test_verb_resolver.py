from kora_v2.tools.verb_resolver import DomainVerbResolver


def test_resolve_known_verb():
    vr = DomainVerbResolver()
    assert "create_reminder" in vr.resolve("remind")


def test_resolve_unknown_verb():
    vr = DomainVerbResolver()
    assert vr.resolve("foobar") == []


def test_suggest_tools_single_verb():
    vr = DomainVerbResolver()
    tools = vr.suggest_tools("please remind me to buy milk")
    assert "create_reminder" in tools


def test_suggest_tools_multiple_verbs():
    vr = DomainVerbResolver()
    tools = vr.suggest_tools("research and track this project")
    assert "search_web" in tools
    assert "create_item" in tools


def test_suggest_tools_no_match():
    vr = DomainVerbResolver()
    assert vr.suggest_tools("hello world") == []


def test_suggest_tools_deduplicates():
    vr = DomainVerbResolver()
    tools = vr.suggest_tools("search and find something")
    # Both "search" and "find" map to "recall" — should appear once
    assert tools.count("recall") == 1


def test_all_verbs_have_nonempty_tools():
    vr = DomainVerbResolver()
    for verb, tools in vr.VERB_MAP.items():
        assert len(tools) > 0, f"Verb '{verb}' has empty tools list"
