import random

from dataforensics.key_insights import (
    _age_verdict,
    _ratio_verdict,
    build_key_insights,
    find_concentration,
    find_dominant_values,
    find_likelihood_gaps,
    related_by_definition,
)


def _build(rows, **kw):
    args = dict(
        rows=rows, continuous_columns=[], grouping_columns=[], exclude_codes={}, associations=[],
        group_missingness=[], trends={}, numeric_summary=[],
    )
    args.update(kw)
    return build_key_insights(**args)


def _rows_with_lift():
    # Smokers: 60 rows, 30 with diabetes (50%). Non-smokers: 140 rows, 14 (10%).
    # Overall 44/200 = 22%; smokers' lift = 0.5 / 0.22 = 2.27.
    rows = [{"smoker": "Yes", "diabetes": "Yes" if i < 30 else "No"} for i in range(60)]
    rows += [{"smoker": "No", "diabetes": "Yes" if i < 14 else "No"} for i in range(140)]
    return rows


def test_likelihood_gap_reports_the_group_and_its_lift():
    found = find_likelihood_gaps(_rows_with_lift(), ["smoker", "diabetes"])
    smokers = next(f for f in found if f["group_column"] == "smoker" and f["target"] == "diabetes")
    assert smokers["group"] == "Yes" and smokers["outcome"] == "Yes"
    assert round(smokers["rate"], 2) == 0.5
    assert round(smokers["overall"], 2) == 0.22
    assert round(smokers["lift"], 2) == 2.27


def test_likelihood_gap_ignores_small_groups_and_weak_lifts():
    # 10-row group: too small however big its rate is.
    rows = [{"g": "rare", "t": "Y"}] * 10 + [{"g": "common", "t": "Y" if i % 5 == 0 else "N"} for i in range(190)]
    assert not [f for f in find_likelihood_gaps(rows, ["g", "t"]) if f["group"] == "rare"]
    # Same rate in both groups: no lift.
    rows = [{"g": "a" if i % 2 else "b", "t": "Y" if i % 4 < 2 else "N"} for i in range(200)]
    assert find_likelihood_gaps(rows, ["g", "t"]) == []


def test_concentration_top_decile_share():
    # 10 rows of 1000 and 90 rows of 10: top 10% hold 10000 / 10900 = 91.7%.
    rows = [{"income": "1000"}] * 10 + [{"income": "10"}] * 90
    (found,) = find_concentration(rows, ["income"])
    assert round(found["share"], 3) == 0.917
    assert len(found["deciles"]) == 10
    # Not an amount-like column name: not reported.
    assert find_concentration([{"age": r["income"]} for r in rows], ["age"]) == []


def test_dominant_value():
    rows = [{"status": "Active"}] * 45 + [{"status": "Closed"}] * 5
    (found,) = find_dominant_values(rows, ["status"])
    assert found["value"] == "Active" and found["share"] == 0.9


def test_build_key_insights_ranks_dedupes_and_caps():
    rows = _rows_with_lift()
    cards = build_key_insights(
        rows=rows,
        continuous_columns=[],
        grouping_columns=["smoker", "diabetes"],
        exclude_codes={},
        associations=[],
        group_missingness=[],
        trends={},
        numeric_summary=[],
    )
    # smoker -> diabetes and diabetes -> smoker are one pair: only one card.
    likelihood = [c for c in cards if c["kind"] == "Higher likelihood"]
    assert len(likelihood) == 1
    assert likelihood[0]["chart"]["type"] == "bar"
    assert all(0 <= c["score"] <= 1 for c in cards)
    assert [c["score"] for c in cards] == sorted((c["score"] for c in cards), reverse=True)


def test_missing_patterns_for_the_same_group_merge_into_one_card():
    gm = [
        {"column": col, "group_column": "age", "kind": "numeric_split", "gap": 0.36,
         "highest": {"group": "age < 35", "rate": 0.36, "n": 100}, "lowest": {"group": "age ≥ 35", "rate": 0.0, "n": 100},
         "groups": [{"group": "age < 35", "rate": 0.36, "n": 100}, {"group": "age ≥ 35", "rate": 0.0, "n": 100}]}
        for col in ("wages", "income")
    ]
    cards = build_key_insights(
        rows=[], continuous_columns=[], grouping_columns=[], exclude_codes={}, associations=[],
        group_missingness=gm, trends={}, numeric_summary=[],
    )
    (card,) = cards
    assert card["title"] == "wages and income are missing far more often for age < 35"
    assert "36%" in card["detail"] and "0%" in card["detail"]


def test_column_names_containing_arrows_do_not_crash():
    # Regression (review): the column pair was recovered by splitting the
    # "a ↔ b" label, so a column named "score → final" crashed the run.
    rows = [{"score → final": str(i), "time ↔ x": str(i * 2)} for i in range(40)]
    assoc = [{"Columns": "score → final ↔ time ↔ x", "Measure": "Spearman ρ", "Effect": 1.0, "Strength": "strong", "N": 40}]
    cards = build_key_insights(
        rows=rows, continuous_columns=["score → final", "time ↔ x"], grouping_columns=[], exclude_codes={},
        associations=assoc, group_missingness=[], trends={}, numeric_summary=[],
    )
    (card,) = cards
    assert card["columns"] == ("score → final", "time ↔ x")


def test_missing_pattern_chart_labels_keep_spaces_in_group_column():
    gm = [{
        "column": "income", "group_column": "Home Region", "kind": "category", "gap": 0.4,
        "highest": {"group": "Home Region = North", "rate": 0.5, "n": 40},
        "lowest": {"group": "Home Region = South", "rate": 0.1, "n": 40},
        "groups": [{"group": "Home Region = North", "rate": 0.5, "n": 40}, {"group": "Home Region = South", "rate": 0.1, "n": 40}],
    }]
    (card,) = build_key_insights(
        rows=[], continuous_columns=[], grouping_columns=[], exclude_codes={}, associations=[],
        group_missingness=gm, trends={}, numeric_summary=[],
    )
    assert [d["label"] for d in card["chart"]["data"]] == ["North", "South"]


def test_amount_names_match_whole_words_only():
    rows = [{"country": "1000"}] * 10 + [{"country": "10"}] * 90
    assert find_concentration(rows, ["country"]) == []
    assert find_concentration([{"visit_count": r["country"]} for r in rows], ["visit_count"])


def test_card_caps_per_kind_and_total():
    # Many independent strong lifts: at most 4 "Higher likelihood" cards.
    rows = []
    for i in range(400):
        row = {f"g{k}": "a" if (i >> k) % 2 else "b" for k in range(6)}
        row["t"] = "Y" if row["g0"] == "a" and i % 3 else "N"
        for k in range(1, 6):
            row[f"t{k}"] = "Y" if row[f"g{k}"] == "a" and i % 4 else "N"
        rows.append(row)
    cats = [*(f"g{k}" for k in range(6)), "t", *(f"t{k}" for k in range(1, 6))]
    cards = build_key_insights(
        rows=rows, continuous_columns=[], grouping_columns=cats, exclude_codes={}, associations=[],
        group_missingness=[], trends={}, numeric_summary=[],
    )
    assert 1 <= sum(1 for c in cards if c["kind"] == "Higher likelihood") <= 4
    assert len(cards) <= 12


def _retirement_rows(diabetes_follows):
    """200 young (age 25) and 200 old (age 70) people. Retired: 10% of the
    young, 90% of the old. Diabetes: 5% of the young, 50% of the old, and
    within each age group independent of retirement -- or, with
    diabetes_follows="smoker", driven by smoking and not by age at all."""
    rows = []
    for age, n_retired, rate in ((25, 20, 0.05), (70, 180, 0.5)):
        for i in range(200):
            retired = i < n_retired
            if diabetes_follows == "age":
                # Same rate among retired and non-retired of this age.
                pool, idx = (n_retired, i) if retired else (200 - n_retired, i - n_retired)
                diabetic = idx < round(pool * rate)
                rows.append({"age": str(age), "retired": "Y" if retired else "N", "diabetes": "Y" if diabetic else "N"})
            else:
                smoker = i % 4 == 0  # 25% in both age groups
                diabetic = smoker and i % 8 == 0 or (not smoker and i % 20 == 1)
                rows.append({"age": str(age), "smoker": "Y" if smoker else "N", "diabetes": "Y" if diabetic else "N"})
    return rows


def test_pattern_explained_by_age_is_held_back_with_a_reason():
    # Retired: 91/200 diabetic (45.5%) vs 110/400 overall (27.5%) = 1.65x.
    # Expected from their ages alone: (20 x 5% + 180 x 50%) / 200 = 45.5%,
    # so the age-adjusted ratio is 1.0 -- the pattern is age in disguise.
    rows = _retirement_rows("age")
    cards = _build(rows, continuous_columns=["age"], grouping_columns=["retired", "diabetes"], adjust_for=["age"])
    likelihood = [c for c in cards if c["kind"] == "Higher likelihood"]
    assert likelihood and all(c["hidden"] for c in likelihood)
    assert all("explained by age" in c["hidden"] for c in likelihood)
    # Hidden cards come after every visible one.
    flags = [bool(c["hidden"]) for c in cards]
    assert flags == sorted(flags)
    # Without an age column the same pattern is shown.
    plain = _build(rows, grouping_columns=["retired", "diabetes"])
    assert any(c["kind"] == "Higher likelihood" and not c["hidden"] for c in plain)


def test_pattern_that_survives_age_adjustment_says_so():
    # Smokers are 25% of both age groups, so age can't explain their excess.
    cards = _build(_retirement_rows("smoker"), continuous_columns=["age"], grouping_columns=["smoker", "diabetes"],
                   adjust_for=["age"])
    (card,) = [c for c in cards if c["kind"] == "Higher likelihood"]
    assert card["hidden"] is None
    assert "Holds after accounting for age" in card["detail"]


def test_related_by_definition():
    assert related_by_definition("wages_income", "total_personal_income") == "incom"
    assert related_by_definition("age_years", "total_income") is None
    # Generic name parts alone don't make two columns the same thing.
    assert related_by_definition("status_code", "type_code") is None
    assert related_by_definition("employment_status", "marital_status") is None
    # Sharing only a modifier, or a unit, doesn't make two columns the same measure.
    assert related_by_definition("blood_pressure", "blood_glucose") is None
    assert related_by_definition("had_heart_attack", "heart_disease") is None
    assert related_by_definition("work_hours", "sleep_hours") is None


def test_definitional_relationship_is_held_back():
    rows = [{"wages_income": str(i * 100), "total_income": str(i * 120 + 5)} for i in range(50)]
    assoc = [{"Columns": "wages_income ↔ total_income", "Measure": "Spearman ρ", "Effect": 1.0, "Strength": "strong", "N": 50}]
    (card,) = _build(rows, continuous_columns=["wages_income", "total_income"], associations=assoc)
    assert card["kind"] == "Relationship"
    assert "by definition" in card["hidden"]


def test_relationship_driven_by_age_is_held_back():
    # Both measures track age; their own noise is independent, so with age
    # held fixed the link vanishes.
    rnd = random.Random(0)
    rows = []
    for _ in range(300):
        age = rnd.uniform(20, 80)
        rows.append({"age": f"{age:.1f}", "bp": f"{age + rnd.gauss(0, 5):.1f}", "chol": f"{age + rnd.gauss(0, 5):.1f}"})
    assoc = [{"Columns": "bp ↔ chol", "Measure": "Spearman ρ", "Effect": 0.85, "Strength": "strong", "N": 300}]
    (card,) = _build(rows, continuous_columns=["age", "bp", "chol"], associations=assoc, adjust_for=["age"])
    assert "explained by age" in card["hidden"]


def test_group_difference_driven_by_age_is_held_back():
    # Retired people have higher blood pressure only because they're older:
    # within each age band, blood pressure is the same for both groups.
    # Five people at each age 20..79; whether each is retired depends on
    # which of the five they are (i // 60), never on their exact age.
    rows = []
    for i in range(300):
        age, person = 20 + i % 60, i // 60
        retired = "Y" if (age >= 50 and person < 4) or (age < 50 and person == 0) else "N"
        rows.append({"age": str(age), "retired": retired, "bp": str(100 + age + (i * 7) % 5)})
    assoc = [{"Columns": "retired → bp", "Measure": "η²", "Effect": 0.4, "Strength": "strong", "N": 300}]
    (card,) = _build(rows, continuous_columns=["age", "bp"], grouping_columns=["retired"], associations=assoc,
                     adjust_for=["age"])
    assert card["kind"] == "Group difference"
    assert "explained by age" in card["hidden"]


def test_skip_pattern_is_labelled_and_ranked_last():
    gm = [
        {"column": "cigs_per_day", "group_column": "ever_smoked", "kind": "category", "gap": 1.0,
         "highest": {"group": "ever_smoked = No", "rate": 1.0, "n": 80}, "lowest": {"group": "ever_smoked = Yes", "rate": 0.0, "n": 80},
         "groups": [{"group": "ever_smoked = No", "rate": 1.0, "n": 80}, {"group": "ever_smoked = Yes", "rate": 0.0, "n": 80}]},
        {"column": "income", "group_column": "region", "kind": "category", "gap": 0.3,
         "highest": {"group": "region = North", "rate": 0.4, "n": 80}, "lowest": {"group": "region = South", "rate": 0.1, "n": 80},
         "groups": [{"group": "region = North", "rate": 0.4, "n": 80}, {"group": "region = South", "rate": 0.1, "n": 80}]},
    ]
    cards = _build([], group_missingness=gm)
    assert [c["kind"] for c in cards] == ["Missing data pattern", "Skip pattern"]
    assert "skipped by design" in cards[1]["detail"]


def test_ranking_prefers_a_large_absolute_difference_over_a_large_ratio():
    # g1 = x: 60% vs 30% overall (2.0x). g2 = x: 10% vs 3% overall (3.3x).
    # A 30-point gap matters more than a 7-point one, despite the smaller ratio.
    rows = []
    for i in range(400):
        g1, g2 = i % 4 == 0, (i // 4) % 4 == 0
        j1, j2 = i // 4, i // 16  # position within the g1 / g2 group
        t1 = (j1 % 10 < 6) if g1 else (i % 5 == 1)
        t2 = (j2 % 10 == 0) if g2 else (i % 150 == 7)
        rows.append({"g1": "x" if g1 else "o", "t1": "Y" if t1 else "N", "g2": "x" if g2 else "o", "t2": "Y" if t2 else "N"})
    cards = _build(rows, grouping_columns=["g1", "t1", "g2", "t2"])
    titles = [c["title"] for c in cards if c["kind"] == "Higher likelihood"]
    first = next(i for i, t in enumerate(titles) if t.startswith("g1 = x"))
    second = next(i for i, t in enumerate(titles) if t.startswith("g2 = x"))
    assert first < second


def _missing_rows(age_drives_it):
    """400 people; income is blank (or the code -9) for some. With
    age_drives_it, North is mostly old and the old skip income more, but
    within each age North and South are the same. Otherwise North skips
    income 8x as often as South at every age."""
    rows = []
    for age in (25, 70):
        north_n = (20 if age == 25 else 180) if age_drives_it else 100
        for i in range(200):
            north = i < north_n
            pool, idx = (north_n, i) if north else (200 - north_n, i - north_n)
            if age_drives_it:
                rate = 0.05 if age == 25 else 0.4
            else:
                rate = 0.4 if north else 0.05
            missing = idx < round(pool * rate)
            income = ("" if idx % 2 else "-9") if missing else "50000"
            rows.append({"age_years": str(age), "region": "North" if north else "South", "income": income})
    return rows


def _missing_entry(rows):
    rates = {}
    for region in ("North", "South"):
        group = [r for r in rows if r["region"] == region]
        rates[region] = sum(r["income"] in ("", "-9") for r in group) / len(group)
    groups = [{"group": f"region = {k}", "rate": v, "n": 200} for k, v in rates.items()]
    hi, lo = max(groups, key=lambda g: g["rate"]), min(groups, key=lambda g: g["rate"])
    return [{"column": "income", "group_column": "region", "kind": "category", "gap": hi["rate"] - lo["rate"],
             "highest": hi, "lowest": lo, "groups": groups}]


def test_missing_pattern_explained_by_age_is_held_back():
    # North: (1 + 72) / 200 = 36.5% missing vs 22.5% overall (1.6x); expected
    # from North's ages alone: (20 x 5% + 180 x 40%) / 200 = 36.5% -> 1.0x.
    rows = _missing_rows(age_drives_it=True)
    (card,) = _build(rows, continuous_columns=["age_years"], exclude_codes={"income": {"-9"}},
                     group_missingness=_missing_entry(rows), adjust_for=["age_years"])
    assert card["kind"] == "Missing data pattern"
    assert card["hidden"].startswith("Mostly explained by age_years")


def test_missing_pattern_that_survives_age_counts_missing_codes_and_says_so():
    # North: 40% missing at both ages, South 5%: 8x the rest within each
    # age, as it is overall. Half the blanks are the code -9.
    rows = _missing_rows(age_drives_it=False)
    (card,) = _build(rows, continuous_columns=["age_years"], exclude_codes={"income": {"-9"}},
                     group_missingness=_missing_entry(rows), adjust_for=["age_years"])
    assert card["hidden"] is None
    assert "Holds after accounting for age_years (8.0× as often as the rest)." in card["detail"]


def test_constant_age_adjusts_nothing():
    rows = [{**r, "age": "40"} for r in _retirement_rows("age")]
    cards = _build(rows, continuous_columns=["age"], grouping_columns=["retired", "diabetes"], adjust_for=["age"])
    card = next(c for c in cards if c["kind"] == "Higher likelihood")
    assert card["hidden"] is None and "age" not in card["detail"]


def test_age_check_skipped_when_most_of_the_group_has_no_age():
    # Regression (review): the adjusted ratio was computed only on the rows
    # with an age but compared with a crude ratio over every row, so a group
    # whose age was mostly blank could be hidden as "explained by age".
    rows = _retirement_rows("age")
    for i, r in enumerate(rows):
        if r["retired"] == "Y" and r["age"] == "70":
            r["age"] = ""
        elif r["age"]:
            r["age"] = str(int(r["age"]) + i % 10)  # spread ages so there are several age bands
    cards = _build(rows, continuous_columns=["age"], grouping_columns=["retired", "diabetes"], adjust_for=["age"])
    likelihood = [c for c in cards if c["kind"] == "Higher likelihood"]
    assert likelihood and all(c["hidden"] is None and "age" not in c["detail"] for c in likelihood)


def test_strong_effect_that_age_only_shrinks_stays_visible():
    # Reporting floors: 1.5x for a rate ratio, |rho| 0.3, eta squared 0.06.
    assert _ratio_verdict(5.0, 2.5) == "partly"      # 2.5x is still reportable
    assert _ratio_verdict(5.0, 1.3) == "explained"
    assert _ratio_verdict(2.0, 1.9) == "holds"
    assert _age_verdict(0.9, 0.35, 0.3) == "partly"
    assert _age_verdict(0.9, 0.2, 0.3) == "explained"
    assert _age_verdict(0.5, 0.45, 0.3) == "holds"
    # Regression (review): a pattern already near the floor that age leaves
    # unchanged was hidden as "explained by age".
    assert _ratio_verdict(1.29, 1.29) != "explained"
    assert _age_verdict(0.28, 0.28, 0.3) != "explained"


def test_missing_pattern_age_does_not_change_is_not_blamed_on_age():
    # North: 45% missing, South: 25% (detected on North vs South), at every
    # age -- ten ages, 20 people each per region, exactly 9 vs 5 missing.
    # The crude ratio against the 35% overall is only 1.29, but age explains
    # none of it, so the card must stay visible.
    rows = [
        {"age_years": str(20 + 6 * (idx // 20)), "region": region, "income": "" if idx % 20 < k else "1"}
        for region, k in (("North", 9), ("South", 5))
        for idx in range(200)
    ]
    (card,) = _build(rows, continuous_columns=["age_years"], group_missingness=_missing_entry(rows),
                     adjust_for=["age_years"])
    assert card["hidden"] is None


def test_relationship_independent_of_age_says_it_holds():
    rnd = random.Random(1)
    rows = []
    for _ in range(300):
        x = rnd.uniform(0, 100)
        rows.append({"age": f"{rnd.uniform(20, 80):.1f}", "bp": f"{x:.1f}", "chol": f"{x + rnd.gauss(0, 10):.1f}"})
    assoc = [{"Columns": "bp ↔ chol", "Measure": "Spearman ρ", "Effect": 0.95, "Strength": "strong", "N": 300}]
    (card,) = _build(rows, continuous_columns=["age", "bp", "chol"], associations=assoc, adjust_for=["age"])
    assert card["hidden"] is None
    assert "Holds after accounting for age" in card["detail"]


def test_pattern_explained_by_sex_is_held_back():
    # Night shifts: 90% of men, 10% of women. Heart condition: 50% of men,
    # 5% of women, the same for both shift types within each sex. Night
    # workers: (180 x 50% + 20 x 5%) / 200 = 45.5% vs 27.5% overall = 1.65x,
    # and 1.0x once sex is accounted for.
    rows = []
    for sex, night_share, rate in (("M", 0.9, 0.5), ("F", 0.1, 0.05)):
        n_night = round(200 * night_share)
        for i in range(200):
            night = i < n_night
            pool, idx = (n_night, i) if night else (200 - n_night, i - n_night)
            rows.append({"sex": sex, "night_shift": "Y" if night else "N",
                         "heart": "Y" if idx < round(pool * rate) else "N"})
    cards = _build(rows, grouping_columns=["night_shift", "heart", "sex"], adjust_for=["sex"])
    night = [c for c in cards if c["kind"] == "Higher likelihood" and set(c["columns"]) == {"night_shift", "heart"}]
    assert night and all(c["hidden"].startswith("Mostly explained by sex") for c in night)


def test_name_match_without_data_evidence_is_not_held_back():
    # "mother_education" and "education_years" share a name but are different
    # people's measures: no part-whole, ratio or near-identical ranking.
    rnd = random.Random(2)
    rows = []
    for _ in range(200):
        own = rnd.randint(8, 20)
        rows.append({"mother_education": str(max(0, own + rnd.randint(-8, 3))), "education_years": str(own)})
    assoc = [{"Columns": "mother_education ↔ education_years", "Measure": "Spearman ρ", "Effect": 0.6,
              "Strength": "strong", "N": 200}]
    (card,) = _build(rows, continuous_columns=["mother_education", "education_years"], associations=assoc)
    assert card["hidden"] is None


def test_derived_ratio_column_is_held_back_as_calculated():
    rows = [{"total_income": str(1000 * i), "income_to_poverty_ratio": str(round(1000 * i / 150))} for i in range(1, 60)]
    assoc = [{"Columns": "total_income ↔ income_to_poverty_ratio", "Measure": "Spearman ρ", "Effect": 0.7,
              "Strength": "strong", "N": 59}]
    (card,) = _build(rows, continuous_columns=["total_income", "income_to_poverty_ratio"], associations=assoc)
    assert "calculated from the other" in card["hidden"]


def _gate_entry(rows, column, gate):
    levels = sorted({r[gate] for r in rows})
    groups = []
    for lvl in levels:
        members = [r for r in rows if r[gate] == lvl]
        groups.append({"group": f"{gate} = {lvl}", "rate": sum(r[column] == "" for r in members) / len(members),
                       "n": len(members)})
    hi, lo = max(groups, key=lambda g: g["rate"]), min(groups, key=lambda g: g["rate"])
    return {"column": column, "group_column": gate, "kind": "category", "gap": hi["rate"] - lo["rate"],
            "highest": hi, "lowest": lo, "groups": groups}


def _smoking_rows(break_off):
    """Ever smoked: 1 = yes (100), 2 = no (150), 7 = don't know (30).
    'cigs_per_day' is blank for 2 and 7. With break_off, the 2s also left
    every other question blank."""
    rows = []
    for code, n in (("1", 100), ("2", 150), ("7", 30)):
        for i in range(n):
            gone = break_off and code == "2"
            rows.append({
                "ever_smoked": code,
                "cigs_per_day": "10" if code == "1" else "",
                "health": "" if gone else str(1 + i % 5),
                "weight": "" if gone else str(120 + i % 80),
                "exercise": "" if gone else ("Y" if i % 3 else "N"),
            })
    return rows


def test_skip_pattern_covers_every_skipped_answer():
    rows = _smoking_rows(break_off=False)
    (card,) = _build(rows, group_missingness=[_gate_entry(rows, "cigs_per_day", "ever_smoked")])
    assert card["kind"] == "Skip pattern"
    assert card["title"] == "cigs_per_day is blank whenever ever_smoked = 2 or 7"


def test_rows_blank_everywhere_are_flagged_as_a_possible_break_off_not_a_skip():
    rows = _smoking_rows(break_off=True)
    (card,) = _build(rows, group_missingness=[_gate_entry(rows, "cigs_per_day", "ever_smoked")])
    assert card["kind"] == "Missing data pattern"
    assert "break-off" in card["detail"]


def test_skip_gate_explains_the_columns_other_missing_patterns():
    # Healthy people (health 1) skip cigs_per_day more only because fewer
    # of them ever smoked: within each answer to ever_smoked, it's the same.
    rows = []
    for health, smokers in (("1", 20), ("2", 80)):
        for i in range(100):
            smoked = i < smokers
            rows.append({"health": health, "ever_smoked": "1" if smoked else "2", "cigs_per_day": "10" if smoked else ""})
    entries = [_gate_entry(rows, "cigs_per_day", "ever_smoked"), _gate_entry(rows, "cigs_per_day", "health")]
    cards = _build(rows, group_missingness=entries)
    by_health = next(c for c in cards if c["columns"] == ("cigs_per_day", "health"))
    assert by_health["hidden"].startswith("Mostly explained by ever_smoked")


def test_codebook_labels_appear_in_titles_and_charts():
    rows = _retirement_rows("smoker")
    labels = {"smoker": {"Y": "Current smoker"}, "diabetes": {"Y": "Has diabetes"}}
    cards = _build(rows, grouping_columns=["smoker", "diabetes"], value_labels=labels)
    (card,) = [c for c in cards if c["kind"] == "Higher likelihood"]
    assert "(Current smoker)" in card["title"] and "(Has diabetes)" in card["title"]
    assert any(d["label"] in ("Y · Current smoker", "Y · Has diabetes") for d in card["chart"]["data"])


def test_codebook_codes_match_without_leading_zeros():
    rows = [{"edu": "1" if i < 45 else "2"} for i in range(50)]
    (card,) = _build(rows, grouping_columns=["edu"], value_labels={"edu": {"01": "No schooling"}})
    assert card["title"] == "90% of edu is a single value: 1 (No schooling)"


def test_group_that_fills_most_of_an_age_band_is_not_compared_with_itself():
    # Regression (review): the expected rate came from every row in the age
    # band, the group's own included, so a group that is 90% of a band was
    # measured against itself and looked "explained by age".
    # Old band: 180 retired (50% diabetic), 20 not retired (10%) -- a real 5x
    # difference at the same age. Young band: 20 retired, 180 not, 5% each.
    rows = []
    for age, n_retired, r_rate, o_rate in ((25, 20, 0.05, 0.05), (70, 180, 0.5, 0.1)):
        for i in range(200):
            retired = i < n_retired
            pool, idx, rate = (n_retired, i, r_rate) if retired else (200 - n_retired, i - n_retired, o_rate)
            rows.append({"age": str(age), "retired": "Y" if retired else "N",
                         "diabetes": "Y" if idx < round(pool * rate) else "N"})
    cards = _build(rows, continuous_columns=["age"], grouping_columns=["retired", "diabetes"], adjust_for=["age"])
    likelihood = [c for c in cards if c["kind"] == "Higher likelihood"]
    assert likelihood and all(c["hidden"] is None for c in likelihood)


def test_small_strata_noise_does_not_stop_an_age_driven_group_difference_being_explained():
    # Regression (review): plain eta squared is ~(k-1)/(n-1) from noise
    # alone, which in 10 small age bands sat above the 0.06 floor (0.07
    # here), so this never counted as explained. Three job groups whose
    # mix shifts with age; blood pressure depends on age only.
    rnd = random.Random(3)
    rows = []
    for i in range(300):
        age = 20 + i % 60
        job = "a" if rnd.random() < 1 - (age - 20) / 60 else ("b" if rnd.random() < 0.5 else "c")
        rows.append({"age": str(age), "job": job, "bp": f"{100 + age + rnd.gauss(0, 15):.1f}"})
    assoc = [{"Columns": "job → bp", "Measure": "η²", "Effect": 0.23, "Strength": "strong", "N": 300}]
    (card,) = _build(rows, continuous_columns=["age", "bp"], grouping_columns=["job"], associations=assoc,
                     adjust_for=["age"])
    assert card["hidden"].startswith("Mostly explained by age")


def test_a_rare_outcome_is_not_determined_by_every_column():
    # Regression (review): with a 4% outcome, always guessing "No" is right
    # 96% of the time, which passed the old 95% "determines" test.
    rows = []
    for i in range(500):
        history = i % 5 == 0  # 20% have a family history
        disease = (i % 25 == 0) if history else (i % 100 == 1)  # 20% vs ~1%
        rows.append({"heart_disease_family_history": "Y" if history else "N", "heart_disease": "Y" if disease else "N"})
    # The names nest ("heart_disease" is inside the other), so only the data
    # check stands between this real finding and "true by definition".
    cards = _build(rows, grouping_columns=["heart_disease_family_history", "heart_disease"])
    likelihood = [c for c in cards if c["kind"] == "Higher likelihood"]
    assert likelihood and all(c["hidden"] is None for c in likelihood)


def test_a_column_that_really_determines_another_is_held_back():
    rows = [{"state_code": s, "state_region": r} for s, r in (("01", "South"), ("06", "West"), ("36", "NE"))
            for _ in range(40)]
    rows += [{"state_code": "48", "state_region": "South"}] * 40
    lifts = find_likelihood_gaps(rows, ["state_code", "state_region"])
    assert lifts  # the strong association exists ...
    cards = _build(rows, grouping_columns=["state_code", "state_region"])
    likelihood = [c for c in cards if c["kind"] == "Higher likelihood"]
    # ... and is held back: state determines region by construction.
    assert likelihood and all("determines" in c["hidden"] for c in likelihood)


def test_numeric_split_missing_pattern_is_retested_against_the_skip_gate():
    # Regression (review): "missing more often for age < 45" was never
    # re-tested. Young people never smoked more often, so they skip the
    # cigarettes question more; within each ever_smoked answer it's equal.
    rows = []
    for i in range(400):
        age = 20 + i % 50
        smoked = (i % 10 < 2) if age < 45 else (i % 10 < 7)
        rows.append({"age": str(age), "ever_smoked": "1" if smoked else "2", "cigs": "5" if smoked else ""})
    young = [r for r in rows if int(r["age"]) < 45]
    old = [r for r in rows if int(r["age"]) >= 45]
    rate = lambda rs: sum(r["cigs"] == "" for r in rs) / len(rs)  # noqa: E731
    split = {"column": "cigs", "group_column": "age", "kind": "numeric_split", "gap": rate(young) - rate(old),
             "highest": {"group": "age < 45", "rate": rate(young), "n": len(young)},
             "lowest": {"group": "age ≥ 45", "rate": rate(old), "n": len(old)},
             "groups": [{"group": "age < 45", "rate": rate(young), "n": len(young)},
                        {"group": "age ≥ 45", "rate": rate(old), "n": len(old)}], "cut": 45.0}
    cards = _build(rows, continuous_columns=["age"], group_missingness=[_gate_entry(rows, "cigs", "ever_smoked"), split])
    by_age = next(c for c in cards if c["columns"] == ("cigs", "age"))
    assert by_age["hidden"].startswith("Mostly explained by ever_smoked")


def test_measurement_words_alone_dont_make_a_column_derived():
    # Regression (review): "rate" counted as a derived-measure word, so
    # heart_rate <-> max_heart_rate was hidden as "calculated from the other";
    # and two counts that are merely ordered (ER visits <= clinic visits)
    # read as "part of the other".
    rnd = random.Random(4)
    rows = []
    for _ in range(100):
        rest = rnd.randint(50, 90)
        clinic = rnd.randint(2, 20)
        rows.append({"heart_rate": str(rest), "max_heart_rate": str(rest + 60 + rnd.randint(-25, 25)),
                     "er_visits": str(rnd.randint(0, clinic // 2)), "clinic_visits": str(clinic)})
    assoc = [
        {"Columns": "heart_rate ↔ max_heart_rate", "Measure": "Spearman ρ", "Effect": 0.7, "Strength": "strong", "N": 100},
        {"Columns": "er_visits ↔ clinic_visits", "Measure": "Spearman ρ", "Effect": 0.5, "Strength": "moderate", "N": 100},
    ]
    cards = _build(rows, continuous_columns=["heart_rate", "max_heart_rate", "er_visits", "clinic_visits"],
                   associations=assoc)
    assert len(cards) == 2 and all(c["hidden"] is None for c in cards)


def test_partly_explained_pattern_says_so():
    # Within each age, smokers have 2x the rate (excess 1.0 -> lift ~1.5
    # on the overall scale); smokers are also mostly old, which roughly
    # doubles the crude gap. Age explains part, not most, of it.
    rows = []
    for age, n_smoke, base in ((25, 20, 0.05), (70, 160, 0.2)):
        for i in range(200):
            smoker = i < n_smoke
            pool, idx = (n_smoke, i) if smoker else (200 - n_smoke, i - n_smoke)
            rows.append({"age": str(age), "smoker": "Y" if smoker else "N",
                         "copd": "Y" if idx < round(pool * base * (2.5 if smoker else 1)) else "N"})
    cards = _build(rows, continuous_columns=["age"], grouping_columns=["smoker", "copd"], adjust_for=["age"])
    card = next(c for c in cards if c["kind"] == "Higher likelihood")
    assert card["hidden"] is None and "Partly explained by age" in card["detail"]


def test_superscript_digits_in_values_or_codebook_do_not_crash():
    rows = [{"unit": "m²" if i < 45 else "m"} for i in range(50)]
    (card,) = _build(rows, grouping_columns=["unit"], value_labels={"unit": {"²": "squared"}})
    assert "m²" in card["title"]


def test_held_back_card_is_listed_when_its_shown_counterpart_is_cut_by_the_caps():
    # Regression: a held-back card was dropped whenever the other direction
    # of its pair was visible -- even if that card then lost out to the
    # per-kind cap, so the pattern vanished from both lists.
    from dataforensics.key_insights import _card, select_cards

    cards = [_card("Higher likelihood", f"strong {k}", "", "", 0.9, (f"a{k}", f"b{k}"), None) for k in range(4)]
    cards.append(_card("Higher likelihood", "weak shown", "", "", 0.4, ("x", "y"), None))
    cards.append(_card("Higher likelihood", "held", "", "", 0.6, ("y", "x"), None, "Mostly explained by age: …"))
    titles = [c["title"] for c in select_cards(cards)]
    assert "weak shown" not in titles  # cut by the 4-per-kind cap ...
    assert "held" in titles  # ... so the held-back direction is still listed


def test_held_back_card_is_not_listed_when_its_counterpart_is_shown():
    from dataforensics.key_insights import _card, select_cards

    shown = _card("Higher likelihood", "shown", "", "", 0.9, ("x", "y"), None)
    held = _card("Higher likelihood", "held", "", "", 0.6, ("y", "x"), None, "Mostly explained by age: …")
    assert [c["title"] for c in select_cards([shown, held])] == ["shown"]


def test_small_noisy_group_difference_is_not_blamed_on_age():
    # Regression (review): with ~100 rows and 10 groups, the bias-corrected
    # effect is ~0, and "0 falls to 0" counted as explained by age.
    rnd = random.Random(5)
    rows = [{"age": str(rnd.randint(20, 80)), "site": f"s{i % 10}", "bp": f"{120 + rnd.gauss(0, 10):.1f}"}
            for i in range(110)]
    assoc = [{"Columns": "site → bp", "Measure": "η²", "Effect": 0.08, "Strength": "medium", "N": 110}]
    (card,) = _build(rows, continuous_columns=["age", "bp"], grouping_columns=["site"], associations=assoc,
                     adjust_for=["age"])
    assert card["hidden"] is None


def test_age_verdict_needs_a_real_reduction():
    assert _age_verdict(0.0, 0.0, 0.06) != "explained"
    assert _age_verdict(0.04, 0.03, 0.06) != "explained"  # started below the floor, lost only a quarter
    assert _age_verdict(0.04, 0.005, 0.06) == "explained"
