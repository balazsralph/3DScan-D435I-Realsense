import json

import pytest

from scanner3d.profiles import DEFAULT_PROFILE, PROFILES, QUALITIES, build_profile, get_profile


@pytest.mark.parametrize("profile", PROFILES.values(), ids=list(PROFILES))
def test_preset_exists_and_matches_depth_units(profile) -> None:
    preset = json.loads(profile.preset_path.read_text())
    params = preset.get("parameters", preset)
    depth_unit_m = float(params["param-depthunits"]) * 1e-6  # le preset compte en micromètres
    assert depth_unit_m * profile.depth_scale == pytest.approx(1.0)
    assert preset["viewer"]["stream-width"] == "848" and preset["viewer"]["stream-height"] == "480"


@pytest.mark.parametrize("profile", PROFILES.values(), ids=list(PROFILES))
def test_profile_ranges_are_consistent(profile) -> None:
    assert profile.depth_min_m < profile.guide_near_m < profile.guide_far_m <= profile.depth_max_m
    assert (profile.object is not None) == (profile.kind == "object")
    assert (profile.room is not None) == (profile.kind == "room")
    assert profile.tips


def test_build_profile_scales_voxel_and_keeps_original() -> None:
    fine = build_profile("objet", "fin")
    assert fine.voxel_mm == pytest.approx(PROFILES["objet"].voxel_mm * QUALITIES["fin"].voxel_factor)
    assert PROFILES["objet"].voxel_mm == 2.0  # profil d'origine inchangé (immuable)


def test_build_profile_depth_override() -> None:
    assert build_profile("objet", depth_max_m=0.45).depth_max_m == 0.45
    with pytest.raises(ValueError):
        build_profile("objet", depth_max_m=0.1)  # sous la profondeur min


@pytest.mark.parametrize(("profile_id", "quality"), [("inconnu", "standard"), ("objet", "ultra")])
def test_unknown_profile_or_quality(profile_id: str, quality: str) -> None:
    with pytest.raises(ValueError):
        build_profile(profile_id, quality)


def test_default_profile_exists() -> None:
    assert get_profile(DEFAULT_PROFILE).kind == "object"
