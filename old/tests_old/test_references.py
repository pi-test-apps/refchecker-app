from app.extractors import extract_reference_entries

SAMPLE_REFERENCES = """
Introduction text before references.

REFERENCES
Smith, J. A. (2020). Article title. Journal Name, 1(1), 1-10.
Lopez, M., & Patel, R. (2019). Another study. Conference Proceedings, 2(3), 20-30.
Nguyen, T. (2020a). Follow-up work. Journal Name, 4(2), 50-60.
Davis, P., Johnson, L., & Clark, S. (2018). Multi-author. Journal, 5(4), 100-110.
O'Neil, R. (2015). Early research. Journal, 6(1), 5-9.
Chen, Q. (2021). New findings. Journal, 7(1), 15-22.
Kim, H., & Lee, S. (2022). Latest insights. Journal, 8(2), 30-40.
Morgan, A. B., et al. (2017). Team effort. Journal, 9(3), 60-70.
Taylor, K. (2020b). Companion piece. Journal, 10(4), 80-90.
Alvarez, L. (2016). Background study. Journal, 11(5), 120-130.
"""


EXPECTED_KEYS = {
    "smith_2020",
    "lopez_2019",
    "nguyen_2020a",
    "davis_2018",
    "o_neil_2015",
    "chen_2021",
    "kim_2022",
    "morgan_2017",
    "taylor_2020b",
    "alvarez_2016",
}


def test_extracts_entries_after_last_heading():
    references = SAMPLE_REFERENCES + "\nReference list\nExtra, A. (2023). After heading."  # later heading should win
    entries = extract_reference_entries(references)
    # last heading should produce only the final entry
    assert len(entries) == 1
    assert entries[0]["key"] == "extra_2023"


def test_extracts_entries_and_keys_from_reference_section():
    entries = extract_reference_entries(SAMPLE_REFERENCES)
    assert len(entries) == 10
    keys = {item["key"] for item in entries}
    assert keys == EXPECTED_KEYS


def test_splits_on_blank_lines_and_boundaries():
    text = """
    References
    Smith, J. A. (2020). First line continues
    and wraps to a new line.

    Lopez, M., & Patel, R. (2019). Separate entry.
    """
    entries = extract_reference_entries(text)
    assert len(entries) == 2
    assert entries[0]["key"] == "smith_2020"
    assert entries[1]["key"] == "lopez_2019"
