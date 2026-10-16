"""venues.py: which .env files the bot reads, in which order."""

from deltaneutral import venues as V


def test_env_reads_arcus_then_lighter_then_its_own_and_knows_the_wallet_alias(tmp_path, monkeypatch):
    root = tmp_path / "deltaneutral"
    for d, text in (("arcus", "ARCUS_WALLET_ADDRESS=0xabc\nLIGHTER_ACCOUNT_INDEX=1\nDN_LIVE=0\nOTHER=x\n"),
                    ("lighter", "LIGHTER_ACCOUNT_INDEX=2\n"),
                    ("deltaneutral", "DN_LIVE=1\n")):
        (tmp_path / d).mkdir()
        (tmp_path / d / ".env").write_text(text)
    monkeypatch.setattr(V, "ROOT", root)
    for k in ("ARCUS_ADDRESS", "LIGHTER_ACCOUNT_INDEX", "DN_LIVE"):
        monkeypatch.delenv(k, raising=False)
    env = V.read_env()
    assert env["ARCUS_ADDRESS"] == "0xabc"              # the Arcus bot's alias
    assert env["LIGHTER_ACCOUNT_INDEX"] == "2"          # lighter/.env over arcus/.env, as the Lighter bot reads them
    assert env["DN_LIVE"] == "1" and "OTHER" not in env
