"""All three worker profiles must expose the same /irtt endpoint.

The probe used to be a 276-line block copied verbatim into `main`, `main_local`
and `main_aws`. The profiles genuinely differ in how they *shape* (WAN-interface
detection, qtrace polling), so they cannot simply be merged -- but the probe
does not differ at all, and three hand-kept copies drift. It now lives once in
`substrate.probes` and is attached by `register_irtt_routes`.

These tests fail if a profile stops registering the endpoint, if the routes
disagree, or if the shared probe registry is copied instead of shared -- the
three failure modes the extraction is meant to make impossible.
"""

from unittest.mock import MagicMock, patch

import pytest

PROFILES = ("substrate.main", "substrate.main_local", "substrate.main_aws")


def _load(name):
    with patch(
        "subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")
    ), patch("subprocess.Popen"):
        return __import__(name, fromlist=["*"])


def _irtt_routes(mod):
    return sorted(
        (sorted(r.methods - {"HEAD", "OPTIONS"})[0], r.path)
        for r in mod.app.routes
        if getattr(r, "methods", None) and "/irtt" in r.path
    )


EXPECTED = [
    ("DELETE", "/irtt/{irtt_id}"),
    ("GET", "/irtt/{irtt_id}"),
    ("POST", "/irtt"),
]


@pytest.mark.parametrize("name", PROFILES)
def test_every_profile_registers_the_probe(name):
    assert _irtt_routes(_load(name)) == EXPECTED


def test_profiles_agree_on_the_routes():
    shapes = {name: _irtt_routes(_load(name)) for name in PROFILES}
    assert len(set(map(tuple, shapes.values()))) == 1, shapes


@pytest.mark.parametrize("name", PROFILES)
def test_profile_shares_the_probe_registry_rather_than_copying_it(name):
    from substrate import probes

    # Shared by reference: a test (or a handler) that mutates the registry
    # through the profile must be mutating the one the endpoint actually reads.
    assert _load(name).ACTIVE_IRTT is probes.ACTIVE_IRTT


@pytest.mark.parametrize("name", PROFILES)
def test_profile_does_not_keep_its_own_copy_of_the_handlers(name):
    """The point of the extraction: no profile defines the probe itself.

    Asserted on the functions the routes are actually bound to, not on the
    profile's namespace -- a profile that simply stopped importing the names
    would satisfy a namespace check while still carrying its own copy.
    """
    mod = _load(name)
    bound = {
        r.path: r.endpoint
        for r in mod.app.routes
        if getattr(r, "methods", None) and "/irtt" in r.path
    }
    assert bound, f"{name} registered no /irtt routes at all"
    for path, fn in bound.items():
        assert fn.__module__ == "substrate.probes", (
            f"{name} serves {path} from {fn.__module__}; it should come from "
            f"substrate.probes"
        )


def test_response_models_are_attached():
    from substrate import probes

    mod = _load("substrate.main")
    by_path = {
        (sorted(r.methods - {"HEAD", "OPTIONS"})[0], r.path): r
        for r in mod.app.routes
        if getattr(r, "methods", None) and "/irtt" in r.path
    }
    assert by_path[("POST", "/irtt")].response_model is probes.IrttResponse
    assert (
        by_path[("GET", "/irtt/{irtt_id}")].response_model is probes.IrttStatusResponse
    )
