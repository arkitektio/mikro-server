"""What this image answers a hub's installer, asked the way the installer asks it.

``tests/fixtures/hub_facts.yaml`` is what Konstruktor writes for this service in a hub with
every service switched on (its keys are that one generation's, and open nothing).

Standalone — needs no database; run with ``uv run pytest tests/test_contract.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from hub_contract import cli

FACTS = Path(__file__).parent / "fixtures" / "hub_facts.yaml"


@pytest.fixture(autouse=True)
def this_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract is this service's."""
    monkeypatch.setenv("HUB_CONTRACT", "mikro_server.contract")


def test_it_says_what_it_needs_before_it_has_any_config(capsys: pytest.CaptureFixture[str]) -> None:
    """``describe`` needs no config, and names the service."""
    assert cli.main(["describe"]) == 0
    said = json.loads(capsys.readouterr().out)
    assert said["contract"] == 1 and said["name"] == "mikro"


def test_its_config_is_written_from_what_the_hub_says(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The hub's facts in, this release's config out — one it starts on."""
    assert cli.main(["render", "--facts", str(FACTS), "--overrides", str(tmp_path / "none.yaml")]) == 0, capsys.readouterr().err

    config = yaml.safe_load(capsys.readouterr().out)
    assert config["django"]["force_script_name"] == "mikro"
    assert config["postgres"]["db_name"] == "mikro"
    assert config["rekuest_hook"] == {"rekuest_url": "http://rekuest-takt:8080/rekuest"}


def test_a_setting_this_release_does_not_read_is_refused_by_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """What the operator set is laid over; a key no setting claims stops the render."""
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(yaml.safe_dump({"django": {"debug": True}}), encoding="utf-8")
    assert cli.main(["render", "--facts", str(FACTS), "--overrides", str(overrides)]) == 0
    assert yaml.safe_load(capsys.readouterr().out)["django"]["debug"] is True

    overrides.write_text(yaml.safe_dump({"django": {"debgu": True}}), encoding="utf-8")
    assert cli.main(["render", "--facts", str(FACTS), "--overrides", str(overrides)]) == cli.REFUSED
    said = capsys.readouterr()
    assert said.out == "" and "django.debgu" in said.err
