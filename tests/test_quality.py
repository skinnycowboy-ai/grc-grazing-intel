from grc_pipeline.quality.checks import check_herd_config_valid


def test_herd_config_valid_pass():
    r = check_herd_config_valid({"animal_count": 10, "daily_intake_kg_per_head": 8.5})
    assert r.passed is True


def test_herd_config_valid_fail():
    r = check_herd_config_valid({"animal_count": 0, "daily_intake_kg_per_head": 0})
    assert r.passed is False
    assert "animal_count must be > 0" in r.details["problems"]
