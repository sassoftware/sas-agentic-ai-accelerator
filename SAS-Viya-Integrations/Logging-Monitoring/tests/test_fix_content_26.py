"""Create-Optimization-Dataset.sas in the state the live pass of round 26 measured (fix-content-26).

The state: _optbuild connected, and the option SESSREF on a session zzs that was ended (`cas _optbuild; cas zzs;
cas zzs terminate;`). The program started no session, as it should, but left the option on zzs, and both
`proc casutil session=_optbuild;` steps halted with 'ERROR: No active Cloud Analytic Services session.'; the DATA step
then could not promote over the table that was not dropped, and the run ended with syscc=1012
(live-26a out/C7_cod_S1pp.log). With the option on a connected session the same program ran clean (C7_cod_S1p.log, S0).
The program now sets the option to _optbuild in that state, as the other programs do for their own session.

SAS can not run here: the session statements are read from the program as text and run by the model of
test_fix_content_23.py.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
from test_fix_content_23 import SasSession, _run

PROGRAM = "Prompt-Optimization/Create-Optimization-Dataset.sas"


def test_the_state_round_26_measured():
    session = SasSession(["_OPTBUILD"], "ZZS")
    during = _run(PROGRAM, session, {})

    # the CAS steps run with the option on the connected _optbuild, and the program starts and ends no session
    assert during == ("_OPTBUILD", {"_OPTBUILD"})
    assert session.warnings == [] and session.started == set() and session.ended == set()
    assert session.connected == {"_OPTBUILD"}


def test_a_connected_session_the_option_names_is_left_as_it_is():
    """S1p of the live pass: the option on the connected zzs, _optbuild connected - the program ran clean and changes
    neither."""
    session = SasSession(["_OPTBUILD", "ZZS"], "ZZS")
    during = _run(PROGRAM, session, {})

    assert during == ("ZZS", {"_OPTBUILD", "ZZS"})
    assert session.started == set() and session.ended == set()
