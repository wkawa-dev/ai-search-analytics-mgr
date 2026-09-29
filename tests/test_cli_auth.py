import json
import urllib.parse

import responses

from asam import auth, cli
from asam.ops import REGISTRY


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_every_op_has_doc_and_cli_parser_builds():
    cli.build_parser()
    for o in REGISTRY.values():
        assert o.doc, o.name


def test_guide_and_config_roundtrip(capsys):
    code, out = run(capsys, "guide", "filters")
    assert code == 0 and "GSC filters" in out["text"]
    run(capsys, "config", "set", "default_site", "example.com")
    code, out = run(capsys, "config", "show")
    assert out["values"]["default_site"] == "example.com"


def test_error_is_json_with_fix(capsys):
    code, out = run(capsys, "sites")
    assert code == 1 and out["error"]["code"] == "NOT_AUTHENTICATED" and out["error"]["fix"]


def test_destructive_requires_confirm(capsys):
    code, out = run(capsys, "gsc", "site-delete", "sc-domain:x.com")
    assert code == 1 and out["error"]["code"] == "CONFIRM_REQUIRED"


def test_list_args_split(capsys, monkeypatch):
    from asam import gsc

    seen = {}
    monkeypatch.setattr(gsc, "query", lambda *a: seen.update(dims=a[2], filters=a[3]) or {"dimensions": a[2], "rows": []})
    run(capsys, "gsc", "query", "--dimensions", "query,page", "--filters", "country in usa,deu", "--compact")
    assert seen["dims"] == ["query", "page"] and seen["filters"] == ["country in usa,deu"]


def test_positional_or_flag_and_missing(capsys, monkeypatch):
    from asam import web

    monkeypatch.setattr(web, "audit_page", lambda url: {"url": url})
    assert run(capsys, "audit", "https://a.com/")[1]["url"] == "https://a.com/"
    assert run(capsys, "web", "audit", "--url", "https://b.com/")[1]["url"] == "https://b.com/"
    code, out = run(capsys, "web", "audit")
    assert code == 1 and out["error"]["code"] == "MISSING_ARGUMENT"


def test_two_step_oauth_login(tmp_path, capsys):
    client = tmp_path / "client_secret_test.json"
    client.write_text(json.dumps({"installed": {"client_id": "123-abc.apps.googleusercontent.com",
                                                "client_secret": "s3cret"}}))
    code, out = run(capsys, "auth", "login")  # not a TTY -> returns URL (agent flow)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(out["auth_url"]).query)
    assert q["client_id"] == ["123-abc.apps.googleusercontent.com"] and q["code_challenge_method"] == ["S256"]
    assert "https://www.googleapis.com/auth/webmasters" in q["scope"][0]
    state = q["state"][0]
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
        rs.post(auth.TOKEN_URI, json={"access_token": "at", "refresh_token": "rt", "scope": "a b"})
        rs.get("https://oauth2.googleapis.com/tokeninfo", json={"email": "me@example.com", "scope": "a b"})
        auth._cached["creds"] = type("C", (), {"token": "at", "valid": True})()
        auth._cached["kind"] = "oauth_user:test"
        code, out = run(capsys, "auth", "finish", f"http://127.0.0.1:8765/?state={state}&code=4/xyz&scope=a")
    assert code == 0 and out["ok"]
    stored = json.loads(auth.token_path().read_text())
    assert stored["refresh_token"] == "rt" and stored["type"] == "authorized_user"
    assert oct(auth.token_path().stat().st_mode)[-3:] == "600"


def test_state_mismatch_rejected(tmp_path, capsys):
    (tmp_path / "client_secret_x.json").write_text(json.dumps({"installed": {"client_id": "1", "client_secret": "2"}}))
    run(capsys, "auth", "login")
    code, out = run(capsys, "auth", "finish", "http://127.0.0.1:8765/?state=WRONG&code=abc")
    assert code == 1 and out["error"]["code"] == "OAUTH_STATE_MISMATCH"


def test_use_service_account(tmp_path, capsys):
    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"type": "service_account", "client_email": "bot@p.iam.gserviceaccount.com"}))
    code, out = run(capsys, "auth", "use", str(sa))
    assert out["type"] == "service_account" and "bot@p.iam" in out["next"]
