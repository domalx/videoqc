"""配置解析：缺文件、语法错、越界钳位、未知键、哈希稳定。无 Qt/ffmpeg。"""
import json

from videoqc.app.config import DEFAULTS, Config, config_hash, load_config


def test_missing_file_uses_defaults(tmp_path):
    cfg, warns = load_config(tmp_path / "nope.json")
    assert cfg == Config()
    assert len(warns) == 1 and "不存在" in warns[0]


def test_bad_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not json", encoding="utf-8")
    cfg, warns = load_config(p)
    assert cfg == Config()
    assert "语法错误" in warns[0]


def test_clamp_and_unknown(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"jobs": 99, "silence_db": -200, "wtf": 1}), encoding="utf-8")
    cfg, warns = load_config(p)
    assert cfg.jobs == 8 and cfg.silence_db == -90
    assert any("未知配置项 wtf" in w for w in warns)


def test_invalid_type_falls_back(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"jobs": "two", "quick_check_on_select": "yes"}), encoding="utf-8")
    cfg, warns = load_config(p)
    assert cfg.jobs == DEFAULTS["jobs"] and cfg.quick_check_on_select is True
    assert len(warns) == 2


def test_hash_is_stable_and_sensitive():
    a, b = Config(), Config()
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash(Config(jobs=3))
