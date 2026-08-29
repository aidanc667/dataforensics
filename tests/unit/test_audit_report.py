from dataforensics.audit_report import (
    PII_EVIDENCE_MASK,
    build_audit_report_html,
    build_investigation_findings,
    categorical_frequency,
    numeric_summary,
)

ROWS = [
    {"id": "1", "age": "45", "status": "yes"},
    {"id": "2", "age": "50", "status": "Yes"},
    {"id": "3", "age": "52", "status": "no"},
    {"id": "4", "age": "", "status": "no"},
]

DICTIONARY = {
    "id": {"non_null_pct": 100.0, "unique_count": 4, "null_count": 0, "levels": None, "outliers": None, "top_code_spike": None},
    "age": {"non_null_pct": 75.0, "unique_count": 3, "null_count": 1, "levels": None, "outliers": None, "top_code_spike": None},
    "status": {"non_null_pct": 100.0, "unique_count": 3, "null_count": 0, "levels": ["Yes", "no", "yes"], "outliers": None, "top_code_spike": None},
}

COLUMN_TYPES = {"id": "identifier", "age": "numeric", "status": "categorical"}


def _base_findings_kwargs(**overrides) -> dict:
    kwargs = dict(
        rows=ROWS,
        dup_rows=[],
        sentinels={},
        approved_sentinels={},
        ambiguous_dates={},
        category_clusters={},
        distribution_columns=[],
        dictionary=DICTIONARY,
        missingness_columns=[],
        clinical_range_findings={},
        conflicting_id_findings={},
        invalid_fips_findings={},
        invalid_zip_findings={},
        survey_weight_columns=[],
        duplicate_entities=[],
        birth_date_findings={},
        quasi_identifier_columns=[],
        id_like_defaults=["id"],
        column_types=COLUMN_TYPES,
        mutations=[],
    )
    kwargs.update(overrides)
    return kwargs


def _safety_report(*, rows_before=4, rows_after=4, modified=None, unmodified=None) -> dict:
    modified = modified or []
    unmodified = unmodified if unmodified is not None else ["id", "age", "status"]
    return {
        "row_count": {"before": rows_before, "after": rows_after, "passed": rows_before == rows_after},
        "column_count": {"before": 3, "after": 3, "passed": True},
        "primary_key_uniqueness": {"before": rows_before, "after": rows_after, "passed": True},
        "modified_columns": modified,
        "unmodified_columns": unmodified,
        "all_passed": True,
    }


class TestCategoricalFrequency:
    def test_basic_counts_and_percentages(self):
        result = categorical_frequency(ROWS, "status")
        by_value = {r["value"]: r for r in result}
        assert by_value["no"]["count"] == 2
        assert by_value["no"]["pct"] == 50.0
        assert by_value["yes"]["count"] == 1

    def test_skips_null_values(self):
        rows = [{"x": "a"}, {"x": ""}, {"x": "b"}]
        result = categorical_frequency(rows, "x")
        total = sum(r["count"] for r in result)
        assert total == 2

    def test_empty_column_returns_empty_list(self):
        rows = [{"x": ""}, {"x": ""}]
        assert categorical_frequency(rows, "x") == []

    def test_caps_at_max_levels_with_other_bucket(self):
        rows = [{"x": str(i)} for i in range(12)]
        result = categorical_frequency(rows, "x", max_levels=8)
        assert len(result) == 9
        assert "(4 other value(s))" == result[-1]["value"]
        assert result[-1]["count"] == 4


class TestNumericSummary:
    def test_basic_stats(self):
        rows = [{"x": "1"}, {"x": "2"}, {"x": "3"}, {"x": "4"}]
        summary = numeric_summary(rows, "x")
        assert summary["count"] == 4
        assert summary["min"] == 1
        assert summary["max"] == 4
        assert summary["median"] == 2.5
        assert summary["mean"] == 2.5

    def test_skips_null_values(self):
        rows = [{"x": "10"}, {"x": ""}, {"x": "20"}]
        summary = numeric_summary(rows, "x")
        assert summary["count"] == 2

    def test_none_for_non_numeric_column(self):
        rows = [{"x": "abc"}, {"x": "def"}]
        assert numeric_summary(rows, "x") is None

    def test_none_when_any_value_is_nan_or_infinite(self):
        rows = [{"x": "1"}, {"x": "nan"}, {"x": "3"}]
        assert numeric_summary(rows, "x") is None

    def test_none_for_column_with_no_values(self):
        rows = [{"x": ""}, {"x": ""}]
        assert numeric_summary(rows, "x") is None


class TestBuildInvestigationFindings:
    def test_no_findings_when_nothing_flagged(self):
        findings = build_investigation_findings(**_base_findings_kwargs())
        assert findings == []

    def test_duplicate_rows_produce_high_tier_finding(self):
        dup_rows = [{"row_index": 3, "duplicate_of_row_index": 0}]
        findings = build_investigation_findings(**_base_findings_kwargs(dup_rows=dup_rows))
        assert len(findings) == 1
        assert findings[0]["tier"] == "high"
        assert findings[0]["resolved"] == 0
        assert findings[0]["total"] == 1
        assert "row 4" in findings[0]["evidence"][0]
        assert "row 1" in findings[0]["evidence"][0]

    def test_sentinel_finding_masks_pii_like_column_evidence(self):
        findings = build_investigation_findings(
            **_base_findings_kwargs(sentinels={"ssn": ["99"]})
        )
        assert len(findings) == 1
        title = findings[0]["title"]
        assert "99" in title  # sentinel values themselves are not PII

    def test_conflicting_id_finding_masks_pii_column_value(self):
        findings = build_investigation_findings(
            **_base_findings_kwargs(
                conflicting_id_findings={
                    "ssn": [{"id_value": "123-45-6789", "row_indices": [0, 1]}]
                }
            )
        )
        assert len(findings) == 1
        evidence_line = findings[0]["evidence"][0]
        assert PII_EVIDENCE_MASK in evidence_line
        assert "123-45-6789" not in evidence_line

    def test_conflicting_id_finding_shows_raw_value_for_non_pii_column(self):
        findings = build_investigation_findings(
            **_base_findings_kwargs(
                conflicting_id_findings={
                    "study_id": [{"id_value": "S001", "row_indices": [0, 1]}]
                }
            )
        )
        assert "S001" in findings[0]["evidence"][0]

    def test_category_cluster_resolved_tracks_mutations(self):
        clusters = {
            "status": [
                {
                    "values": ["yes", "Yes"],
                    "suggested_canonical": "Yes",
                    "confidence": "high",
                }
            ]
        }
        unresolved = build_investigation_findings(**_base_findings_kwargs(category_clusters=clusters))
        assert unresolved[0]["resolved"] == 0

        mutations = [
            {
                "row_key": {"id": "1"},
                "column": "status",
                "original_value": "yes",
                "new_value": "Yes",
                "transformation_rule": "category_mapping:status",
                "reason": "Approved by user during interactive review",
            }
        ]
        resolved = build_investigation_findings(**_base_findings_kwargs(category_clusters=clusters, mutations=mutations))
        assert resolved[0]["resolved"] == 1

    def test_missingness_finding_is_informational_with_no_confidence(self):
        findings = build_investigation_findings(**_base_findings_kwargs(missingness_columns=["age"]))
        assert findings[0]["tier"] == "info"
        assert findings[0]["confidence"] == "N/A"

    def test_duplicate_entities_finding_masks_quasi_identifier_pii(self):
        rows = [
            {"id": "1", "dob": "1980-01-01", "zip": "10001"},
            {"id": "2", "dob": "1980-01-01", "zip": "10001"},
        ]
        entities = [{"row_indices": [0, 1], "id_values": ["1", "2"]}]
        findings = build_investigation_findings(
            **_base_findings_kwargs(
                rows=rows,
                duplicate_entities=entities,
                quasi_identifier_columns=["dob", "zip"],
                id_like_defaults=["id"],
            )
        )
        evidence_line = findings[0]["evidence"][0]
        assert PII_EVIDENCE_MASK in evidence_line
        assert "1980-01-01" not in evidence_line


class TestBuildAuditReportHtml:
    def _build(self, **overrides):
        kwargs = dict(
            file_name="patients.csv",
            file_size_bytes=2048,
            rows=ROWS,
            transformed_rows=ROWS,
            columns=["id", "age", "status"],
            column_types=COLUMN_TYPES,
            dictionary=DICTIONARY,
            findings=[],
            mutations=[],
            safety=_safety_report(),
            validation_report={"errors": [], "warnings": [], "suggestions": []},
            applied_at="2026-08-29T00:00:00Z",
            dataset_type="generic",
        )
        kwargs.update(overrides)
        return build_audit_report_html(**kwargs)

    def test_contains_all_ten_numbered_sections(self):
        html = self._build()
        for i in range(1, 11):
            assert f">{i}. " in html, f"missing section {i}"

    def test_readiness_is_green_when_no_findings(self):
        html = self._build(findings=[])
        assert "🟢" in html
        assert "Ready with review" in html

    def test_readiness_is_red_when_unresolved_high_tier_finding(self):
        findings = [{
            "tier": "high", "title": "3 exact duplicate records", "evidence": [], "more": 0,
            "detection": "x", "suggested_action": "y", "confidence": "Medium", "resolved": 0, "total": 3,
        }]
        html = self._build(findings=findings)
        assert "🔴" in html
        assert "Significant issues remain" in html

    def test_readiness_is_yellow_when_unresolved_review_tier_only(self):
        findings = [{
            "tier": "review", "title": "ambiguous date format", "evidence": [], "more": 0,
            "detection": "x", "suggested_action": "y", "confidence": "Low", "resolved": 0, "total": 1,
        }]
        html = self._build(findings=findings)
        assert "🟡" in html
        assert "Review recommended" in html

    def test_resolved_findings_do_not_count_against_readiness(self):
        findings = [{
            "tier": "high", "title": "resolved thing", "evidence": [], "more": 0,
            "detection": "x", "suggested_action": "y", "confidence": "Medium", "resolved": 1, "total": 1,
        }]
        html = self._build(findings=findings)
        assert "🟢" in html

    def test_never_claims_dataset_is_clean(self):
        html = self._build()
        assert "no issues detected by the checks performed" in html.lower()
        # "clean" must never appear as a claim about the dataset itself.
        assert "dataset is clean" not in html.lower()
        assert "data is clean" not in html.lower()

    def test_analysis_readiness_section_has_no_numeric_score(self):
        html = self._build()
        readiness_start = html.index("9. Analysis Readiness")
        readiness_end = html.index("10. Reproducibility")
        readiness_html = html[readiness_start:readiness_end]
        assert "0–100" in readiness_html or "not a" in readiness_html.lower()
        import re
        assert not re.search(r"\b\d{1,3}\s*/\s*100\b", readiness_html)

    def test_transformation_log_reflects_applied_mutations(self):
        mutations = [
            {
                "row_key": {"id": "1"},
                "column": "status",
                "original_value": "yes",
                "new_value": "Yes",
                "transformation_rule": "category_mapping:status",
                "reason": "Approved by user during interactive review",
            }
        ]
        html = self._build(mutations=mutations, safety=_safety_report(modified=["status"], unmodified=["id", "age"]))
        assert "category_mapping:status" in html
        assert "Approved by user during interactive review" in html

    def test_provenance_section_includes_file_name_and_version(self):
        from dataforensics import __version__
        html = self._build()
        assert "patients.csv" in html
        assert __version__ in html

    def test_pii_masking_survives_end_to_end_into_rendered_html(self):
        rows = [
            {"id": "1", "dob": "1980-01-01", "zip": "10001"},
            {"id": "2", "dob": "1980-01-01", "zip": "10001"},
        ]
        entities = [{"row_indices": [0, 1], "id_values": ["1", "2"]}]
        findings = build_investigation_findings(
            **_base_findings_kwargs(
                rows=rows,
                duplicate_entities=entities,
                quasi_identifier_columns=["dob", "zip"],
                id_like_defaults=["id"],
            )
        )
        html = self._build(rows=rows, transformed_rows=rows, findings=findings)
        assert "1980-01-01" not in html
        assert PII_EVIDENCE_MASK in html
