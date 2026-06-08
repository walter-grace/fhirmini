"""Dialysis analysis unit tests — pure logic (no services/network), runs in CI."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from analysis.dialysis import METRICS, MED_CLASSES, in_target
from analysis import synth


def test_in_target_directions():
    assert in_target(METRICS["phosphorus"], 4.5)        # in 3.5-5.5
    assert not in_target(METRICS["phosphorus"], 6.2)
    assert in_target(METRICS["ktv"], 1.4)               # ge 1.2
    assert not in_target(METRICS["ktv"], 1.0)
    assert in_target(METRICS["idwg"], 2.0)              # le 2.5
    assert not in_target(METRICS["idwg"], 3.1)
    assert in_target(METRICS["albumin"], 4.2)           # ge 4.0
    assert not in_target(METRICS["albumin"], 3.5)


def test_metric_codes_unique():
    codes = [m["loinc"] for m in METRICS.values()]
    assert len(codes) == len(set(codes)), "duplicate LOINC codes in METRICS"


def test_generator_and_analyzer_share_every_metric():
    # the synth generator must produce every metric the analyzer knows (no drift)
    assert set(synth.BASE) == set(METRICS), "synth BASE drifted from METRICS"


def test_med_groupings_well_formed():
    for cls, groups in MED_CLASSES.items():
        for label, kws in groups.items():
            assert kws and all(isinstance(k, str) for k in kws)
