from app.matcher import build_report


def test_basic_matching():
    citations = ["smith_2020", "lopez_2019", "smith_2020"]
    references = ["smith_2020", "lopez_2019", "chen_2021"]
    report = build_report(citations, references)

    assert report["total_citations"] == 3
    assert report["total_references"] == 3
    assert report["missing_in_references"] == []
    assert report["unused_in_text"] == ["chen_2021"]
    assert report["citation_examples"] == ["smith_2020", "lopez_2019"]
    assert report["reference_examples"] == ["smith_2020", "lopez_2019", "chen_2021"]


def test_detects_missing_and_unused():
    citations = ["a_2020", "b_2021", "c_2022"]
    references = ["a_2020", "d_2019"]
    report = build_report(citations, references)

    assert report["missing_in_references"] == ["b_2021", "c_2022"]
    assert report["unused_in_text"] == ["d_2019"]


def test_examples_are_capped_and_unique():
    citations = [f"cite_{i}" for i in range(10)] + ["cite_1"]
    references = [f"ref_{i}" for i in range(8)] + ["ref_2"]
    report = build_report(citations, references)

    assert len(report["citation_examples"]) == 5
    assert report["citation_examples"] == [f"cite_{i}" for i in range(5)]
    assert len(report["reference_examples"]) == 5
    assert report["reference_examples"] == [f"ref_{i}" for i in range(5)]
