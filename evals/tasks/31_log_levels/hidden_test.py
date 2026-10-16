
import itertools
from logger import LEVELS, log, should_log

def test_all_pairs():
    for lvl, thr in itertools.product(LEVELS, LEVELS):
        assert should_log(lvl, thr) == (LEVELS.index(lvl) >= LEVELS.index(thr)), (lvl, thr)

def test_log_uses_config():
    out = []
    log("ERROR", "boom", sink=out.append)
    log("INFO", "quiet", sink=out.append)
    assert out == ["[ERROR] boom"]
