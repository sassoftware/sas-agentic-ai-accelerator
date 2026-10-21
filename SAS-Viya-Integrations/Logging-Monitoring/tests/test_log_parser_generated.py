"""The parser on generated files with the line of the collection script in front of every part: what its rows hold, and
whether the numbers of its NOTEs are the rows they speak of.

The generator is the one gate-19 read the parser with, with the seeds of its files: six parts a file, each the lines
of one container with two to four requests of one model. One worker scores in the order the requests came in, and the
first lines of a later request arrive between two lines of an earlier one. About a fifth of the requests fail with
the ERROR lines of a transaction. In three of five parts the lines end on a running request - behind its first
lines, in the middle of its lines of the score code, or on the last two requests. The next part starts two minutes
or half a minute earlier, or five seconds, a minute or ten minutes later, and now and then a line is stamped a few
seconds late.

Every line is made up by the builders of the harness.
"""
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from log_parser_harness import failed_block, is_log_line, lines_of, llm_block, log_units, parse, run_parser

FILES = 120


def missing(value):
    return value is None or value != value


def collected(pod):
    return f"#=# llm-log-collection pod={pod} container=scr collected=2026-01-05T12:00:00Z #=#"


def _stamped(lines, start, rng):
    """The log lines one second apart; now and then a line that is no request line carries an earlier time."""
    moment, out = start, []
    for line in lines:
        if is_log_line(line):
            moment += timedelta(seconds=1)
            stamp = moment
            if rng.random() < 0.04 and " - Request: POST" not in line:
                stamp = moment - timedelta(seconds=rng.choice([2, 3, 5]))
            line = stamp.strftime("%Y-%m-%d %H:%M:%S") + line[19:]
        out.append(line)
    return out, moment


def generated_file(seed, parts=6):
    """Returns the text of a file and, in the order of their request lines, what the generator knows of each request
    whose request line is in the file: its number, whether it fails, whether its part holds the line that ends it -
    its 'Response:' line or, for a request that fails, the last ERROR line of its transaction - and whether its part
    holds an ERROR line of it. And for the reading without the lines of the script: the part it stands in, and
    whether that part holds the line that names it as answered, its 'Response Data:' line."""
    rng = random.Random(seed)
    known, text, serial = [], [], 0
    clock = datetime(2026, 1, 5, 9, 0, 0)
    for part in range(parts):
        model = f"model_{rng.randint(1, 2)}"
        requests = []
        for _ in range(rng.randint(2, 4)):
            serial += 1
            fails = rng.random() < 0.2
            if fails:
                block = failed_block("2026-01-05 09:00:00", model, [("systemPrompt", f"S{serial}."), ("userPrompt", f"Q{serial}?"),
                                                                     ("options", "{temperature:0.2}")])
            else:
                block = llm_block(timestamp="2026-01-05 09:00:00", model=model, system_prompt=f"S{serial}.", user_prompt=f"Q{serial}?",
                                  prompt_length=1000 + serial, output_length=5000 + serial, run_time=serial + 0.5, response=f"A{serial}.")
            requests.append((serial, fails, log_units(block)))
        # the first eight units of a request are its first lines and the first three lines of its transaction
        written = list(requests[0][2][:8])
        arrived = {0}
        rest = [units[8:] for _, _, units in requests]
        for index in range(len(requests)):
            if index not in arrived:
                written += requests[index][2][:8]
                arrived.add(index)
            for unit in rest[index]:
                written.append(unit)
                following = len(arrived)
                if following < len(requests) and rng.random() < 0.35:
                    written += requests[following][2][:8]
                    arrived.add(following)
        ending = rng.choice(["whole", "whole", "behind the first lines", "in the score lines", "on two requests"])
        if ending == "behind the first lines":
            written = written[:len(written) - len(rest[-1])]
        elif ending == "in the score lines":
            written = written[:len(written) - max(1, len(rest[-1]) - rng.randint(1, max(1, len(rest[-1]) - 1)))]
        elif ending == "on two requests":
            written = written[:len(written) - (len(rest[-1]) + len(rest[-2]) - 1)]
        in_part = set(id(unit) for unit in written)
        for number, fails, units in requests:
            if id(units[0]) not in in_part:
                continue
            # what ends a request: its 'Response:' line, or the last ERROR line of its transaction where it fails
            last = [unit for unit in units if " - ERROR - " in unit[0]][-1] if fails else units[14]
            assert fails or (" - api - INFO - Response: Status Code " in last[0] and " - api - INFO - Response Data: " in units[15][0])
            known.append({"serial": number, "fails": fails, "ended": id(last) in in_part,
                          "error in its part": any(id(unit) in in_part and " - ERROR - " in unit[0] for unit in units[8:]),
                          "part": part, "response data in its part": not fails and id(units[15]) in in_part})
        stamped, clock = _stamped(lines_of(written), clock + timedelta(seconds=rng.choice([-120, -30, 5, 60, 600])), rng)
        text += [collected(f"pod-{part}")] + stamped
    return "".join(line + "\n" for line in text), known


OVERLAP = re.compile(r"(\d+) request\(s\) were running at the same time as another request.*?: (\d+) request\(s\) can have missing values, "
                     r"and failed is missing for (\d+) request\(s\)\.")
ENDED = re.compile(r"(\d+) request\(s\) were running where the lines of their container end ")
NO_RESPONSE = re.compile(r"(\d+) request\(s\) have no response: ")
QUOTED = re.compile(r"(\d+) line\(s\) that read as a request line ")


def note_numbers(messages):
    """What the NOTEs count: the requests that can have missing values, the ones whose failed is missing although the
    lines of their container do not end on them, the ones the lines end on, and the ones without a response."""
    numbers = {"can have missing values": 0, "failed missing": 0, "the lines end on": 0, "no response": 0}
    for kind, message in messages:
        assert kind == "NOTE", (kind, message)
        if OVERLAP.match(message):
            numbers["can have missing values"], numbers["failed missing"] = (int(number) for number in OVERLAP.match(message).group(2, 3))
        elif ENDED.match(message):
            numbers["the lines end on"] = int(ENDED.match(message).group(1))
        elif NO_RESPONSE.match(message):
            numbers["no response"] = int(NO_RESPONSE.match(message).group(1))
        else:
            assert QUOTED.match(message), message
    return numbers


@pytest.fixture(scope="module")
def parsed_files(tmp_path_factory):
    """Every generated file as (seed, rows with what the generator knows of their request, numbers of the NOTEs). The
    Python of the parser is run once; parse_log_file is then called for every file. A file has a row for every
    request line, in their order."""
    folder = tmp_path_factory.mktemp("generated")
    patch = pytest.MonkeyPatch()
    try:
        empty = folder / "empty.log"
        empty.write_text("", encoding="utf-8")
        run = run_parser(patch, empty)
        files = []
        for seed in range(FILES):
            text, known = generated_file(seed)
            path = folder / "one.log"
            path.write_text(text, encoding="utf-8", newline="")
            del run.sas.messages[:]
            frame = run.namespace["parse_log_file"](str(path))
            assert len(frame) == len(known), seed
            files.append((seed, [(row, request) for (_, row), request in zip(frame.iterrows(), known)], note_numbers(run.sas.messages)))
    finally:
        patch.undo()
    return files


def test_the_generator_makes_what_it_says():
    """Two runs of a seed are one file, and the files hold what the tests below need: requests the lines of their part
    end on, some of them with an ERROR line in the part, and requests that failed."""
    assert generated_file(7) == generated_file(7)
    requests = [request for seed in range(FILES) for request in generated_file(seed)[1]]
    assert len(requests) > 1500 and sum(1 for request in requests if not request["ended"]) > 300
    assert sum(1 for request in requests if request["fails"]) > 200
    assert sum(1 for request in requests if not request["ended"] and request["error in its part"]) > 5


def test_no_row_holds_a_value_of_another_request(parsed_files):
    for seed, rows, _ in parsed_files:
        for row, request in rows:
            serial = request["serial"]
            own = {"user_prompt": f"Q{serial}?", "system_prompt": f"S{serial}.", "prompt_length": 1000 + serial,
                   "output_length": 5000 + serial, "runtime": serial + 0.5, "response": f"A{serial}."}
            for column, value in own.items():
                if missing(row[column]):
                    continue
                # a request that fails writes no line of the score code but its error
                assert column.endswith("_prompt") or not request["fails"], (seed, serial, column, row[column])
                assert row[column] == value, (seed, serial, column, row[column])


def test_failed_is_what_the_part_of_a_request_says(parsed_files):
    """Review-19 B4: a request the lines of its container end on is never failed = 1 - unless its part holds an ERROR
    line of it, which is what failed = 1 says -, a request that was answered never is, and a request that failed is
    never failed = 0."""
    for seed, rows, _ in parsed_files:
        for row, request in rows:
            if request["error in its part"]:
                assert row["failed"] == 1 or missing(row["failed"]), (seed, request, row["failed"])
            elif request["ended"]:
                assert row["failed"] == 0 or missing(row["failed"]), (seed, request, row["failed"])
            else:
                assert missing(row["failed"]), (seed, request, row["failed"])


def test_the_numbers_of_the_notes_are_the_rows_they_speak_of(parsed_files):
    """Gate-19: a request that was cut off at a line of the collection script while another request ran beside it was
    counted in the NOTE of the requests that ran at the same time, and in 33 of 400 files that NOTE counted fewer
    requests than rows had a missing value. Every request whose failed is missing is counted once now: in the NOTE
    of the requests the lines of their container end on where its part does not hold the line that ends it, and in
    'failed is missing for <n> request(s)' of the other NOTE where it does. And every row of an answered request
    with a value missing is one of the rows the NOTEs count."""
    cut_off = overlapped_and_cut = 0
    for seed, rows, numbers in parsed_files:
        failed_missing = [request for row, request in rows if missing(row["failed"])]
        assert numbers["the lines end on"] == sum(1 for request in failed_missing if not request["ended"]), (seed, numbers)
        assert numbers["failed missing"] == sum(1 for request in failed_missing if request["ended"]), (seed, numbers)
        # a request that was answered holds its four values of the score code and its status
        incomplete = sum(1 for row, request in rows if request["ended"] and not request["fails"]
                         and any(missing(row[column]) for column in ("prompt_length", "output_length", "runtime", "response", "status_code")))
        assert incomplete <= numbers["can have missing values"] + numbers["no response"], (seed, incomplete, numbers)
        cut_off += numbers["the lines end on"]
        overlapped_and_cut += 1 if numbers["the lines end on"] and numbers["can have missing values"] else 0
    # the case of the finding is in the files: parts that end on requests while requests ran at the same time
    assert cut_off > 250 and overlapped_and_cut > 40


def without_the_lines_of_the_script(text):
    return "".join(line + "\n" for line in text.split("\n")[:-1] if not line.startswith("#=# "))


# What `failed` says wrong in a file without the lines of the script, each with the test of test_log_parser.py that
# holds two files of it which are the same byte for byte and need different rows
WITHOUT_A_CLOSING_LINE = "failed = 1 for a request whose part holds neither its 'Response:' line nor an ERROR line of it"
A_RESPONSE_LINE_ALONE = "failed = 1 for a request whose part holds its 'Response:' line and not its 'Response Data:' line"
THE_RESPONSE_LINE_OF_ANOTHER = "failed = 0 for a request whose part does not hold its 'Response:' line"
THE_ERROR_LINE_OF_ANOTHER = "failed = 1 and the text of an error for a request whose part holds no ERROR line of it"
PROOFS = {WITHOUT_A_CLOSING_LINE: "test_a_request_without_a_closing_line_is_two_files_in_one",
          A_RESPONSE_LINE_ALONE: "test_a_response_line_without_its_response_data_line_is_two_files_in_one",
          THE_RESPONSE_LINE_OF_ANOTHER: "test_a_response_line_without_its_response_data_line_is_two_files_in_one",
          THE_ERROR_LINE_OF_ANOTHER: "test_an_error_line_of_the_score_code_behind_a_request_without_a_closing_line_is_two_files_in_one"}


def wrong_failed_values(run, folder, seeds):
    """Every row of the generated files, read WITHOUT the lines of the script, whose `failed` is not what the part of its
    request says - as (seed, class, the request), the class being one of the four above or 'other' - and the number
    of files that have a row for every request line."""
    found, files = [], 0
    for seed in seeds:
        text, known = generated_file(seed)
        path = folder / "one.log"
        path.write_text(without_the_lines_of_the_script(text), encoding="utf-8", newline="")
        frame = run.namespace["parse_log_file"](str(path))
        if len(frame) != len(known):
            continue
        files += 1
        last_request_line = len(known) - 1
        for position, ((_, row), request) in enumerate(zip(frame.iterrows(), known)):
            failed = row["failed"]
            if missing(failed):
                continue
            if request["error in its part"]:
                right, kind = failed == 1, "other"
            elif failed == 1 and not missing(row["error_text"]):
                # the ERROR line the score code writes names no request: another request of its part wrote one
                theirs = any(other["part"] == request["part"] and other is not request and other["error in its part"] for other in known)
                right, kind = False, THE_ERROR_LINE_OF_ANOTHER if theirs else "other"
            elif request["ended"]:
                right = failed == 0
                # its 'Response:' line names no request: only the 'Response Data:' line behind it does
                kind = A_RESPONSE_LINE_ALONE if not request["response data in its part"] else "other"
            elif failed == 1:
                # nothing of the file says how the request ended, and a request line stands behind it: of its container,
                # or of the next one
                right, kind = False, WITHOUT_A_CLOSING_LINE if position < last_request_line else "other"
            else:
                # the status is the one of a 'Response:' line that names no request: another request of its part has
                # such a line, without the 'Response Data:' line that would name it
                alone = any(other["part"] == request["part"] and other is not request and other["ended"] and not other["fails"]
                            and not other["response data in its part"] for other in known)
                right, kind = False, THE_RESPONSE_LINE_OF_ANOTHER if alone else "other"
            if not right:
                found.append((seed, kind, request))
    return found, files


def test_every_wrong_failed_without_the_lines_of_the_script_is_of_a_class_that_two_files_hold(tmp_path):
    """Review-20 and gate-20: in files without the lines of the script `failed` is wrong for some requests, and the
    guide gave that as a limit without saying of which kinds the wrong values are. Here every generated file is read
    without the lines, and every `failed` that is not what the part of its request says is put to one of four
    classes by what the generator knows of the request. No value is left over. For each class a test of
    test_log_parser.py builds two files that are the same byte for byte and need different rows, so no reading of
    such a file is right for both. With the lines of the script none of the four occurs
    (test_failed_is_what_the_part_of_a_request_says)."""
    patch = pytest.MonkeyPatch()
    try:
        empty = tmp_path / "empty.log"
        empty.write_text("", encoding="utf-8")
        run = run_parser(patch, empty)
        found, files = wrong_failed_values(run, tmp_path, range(FILES))
    finally:
        patch.undo()
    classes = {}
    for seed, kind, request in found:
        classes.setdefault(kind, []).append((seed, request["serial"]))

    assert "other" not in classes, classes["other"][:5]
    assert set(classes) <= set(PROOFS) and len(classes[WITHOUT_A_CLOSING_LINE]) > 50
    # the files that are counted are nearly all: a file with a row fewer is one with a request line in a response
    assert files >= FILES - 5
    # the tests that hold the two files of each class are there
    source = (Path(__file__).resolve().parent / "test_log_parser.py").read_text(encoding="utf-8")
    for name in set(PROOFS.values()):
        assert f"\ndef {name}(" in source, name


def test_the_same_lines_without_the_lines_of_the_script_are_not_read_that_well(monkeypatch, tmp_path):
    """What the line of the collection script is for: the first files of the generator without it. The parts are found
    by the time of their lines then, a part that starts later than the one in front of it is not found, and rows hold
    values of other requests - the limit the guide gives for such files."""
    wrong = 0
    for seed in range(12):
        text, known = generated_file(seed)
        folder = tmp_path / str(seed)
        folder.mkdir()
        frame = parse(monkeypatch, folder, without_the_lines_of_the_script(text))
        prompt_length = {f"Q{request['serial']}?": 1000 + request["serial"] for request in known}
        for _, row in frame.iterrows():
            if not missing(row["prompt_length"]) and row["prompt_length"] != prompt_length.get(row["user_prompt"]):
                wrong += 1
    assert wrong > 0
