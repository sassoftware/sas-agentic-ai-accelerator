"""Build-RAG-Cost-View.sas: the last line of a run names the table it wrote.

The program ended with the NOTE 'RAG_RUN_COST built in <caslib>.' whatever its output table
was called - measured on 2026-10-12 with the output table ZZ_VERIFY_R16_COST, which the log
called RAG_RUN_COST.

The SAS code can not run here. The test reads it as text.
"""
import re
from pathlib import Path

PROGRAM = Path(__file__).resolve().parents[1] / "Build-RAG-Cost-View.sas"


def code():
    """The program without its comments."""
    text = PROGRAM.read_text(encoding="utf-8").replace("\r\n", "\n")
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def test_the_closing_note_of_the_cost_view_names_the_table_it_wrote():
    program = code()

    assert "%put NOTE: &_rcv_out_table. built in &_rcv_out_caslib..;" in program
    assert "RAG_RUN_COST built in" not in program
    # the name the program comes with stands in one statement only, the one that sets the output table
    named = [line.strip() for line in program.split("\n") if "RAG_RUN_COST" in line]
    assert len(named) == 1 and "_rcv_out_table" in named[0]
    # the NOTE stands behind the statements that publish the table
    positions = [program.index(marker) for marker in (
        'load data=work._rcv_cost casout="&_rcv_out_table.";', 'save casdata="&_rcv_out_table." casout="&_rcv_out_table." replace;',
        "%put NOTE: &_rcv_out_table. built in &_rcv_out_caslib..;")]
    assert positions == sorted(positions)
