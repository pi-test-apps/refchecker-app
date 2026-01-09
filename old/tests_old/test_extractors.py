from app.extractors import extract_apa_citations


CASES = [
    "A simple cite (Smith, 2020) in text.",
    "Narrative citation by Lopez (2019) within the sentence.",
    "Multiple authors example (Garcia & Patel, 2021) for collaboration.",
    "Group cite (Morgan et al., 2018) shows et al format.",
    "Year suffix handled in (Chen, 2020a) correctly.",
    "Narrative with suffix: Nguyen (2020b) demonstrates variation.",
    "Hyphenated surname in Johnson-Smith (2022).",
    "Another et al narrative: Davis et al. (2017) followed by text.",
    "Sentence with two cites (Alvarez, 2015) and later (Alvarez, 2015) duplicates removed.",
    "Mixed symbols (O'Neil & McKay, 2016) include apostrophes.",
]


EXPECTED_KEYS = {
    "smith_2020",
    "lopez_2019",
    "garcia_patel_2021",
    "morgan_et_al_2018",
    "chen_2020a",
    "nguyen_2020b",
    "johnson_smith_2022",
    "davis_et_al_2017",
    "alvarez_2015",
    "o_neil_mckay_2016",
}


def test_extracts_expected_unique_keys():
    combined = " \n".join(CASES)
    citations = extract_apa_citations(combined)
    keys = {c["key"] for c in citations}
    assert keys == EXPECTED_KEYS


def test_includes_raw_strings():
    sample = "Text with cite (Smith, 2020) and narrative Lee (2021)."
    citations = extract_apa_citations(sample)
    raw_values = {c["raw"] for c in citations}
    assert "(Smith, 2020)" in raw_values
    assert "Lee (2021)" in raw_values


def test_ignores_duplicates():
    text = "(Smith, 2020) something else (Smith, 2020)"
    citations = extract_apa_citations(text)
    assert len(citations) == 1
    assert citations[0]["key"] == "smith_2020"


def test_handles_et_al_narrative_and_parenthetical():
    text = "Davis et al. (2017) found results, unlike (Davis et al., 2017)."
    citations = extract_apa_citations(text)
    keys = {c["key"] for c in citations}
    assert keys == {"davis_et_al_2017"}


def test_handles_ampersand_and_year_suffix():
    text = "We tested (Taylor & Gomez, 2020a) alongside Taylor & Gomez (2020a)."
    citations = extract_apa_citations(text)
    keys = {c["key"] for c in citations}
    assert keys == {"taylor_gomez_2020a"}
