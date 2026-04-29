from __future__ import annotations

from app.modules.graph_kb.value_parsers import parse_capacity, parse_conductivity, parse_density, parse_retention


def test_parse_density_g_cm3():
    parsed = parse_density("3.19 g/cm³ at 250 MPa loading")

    assert parsed.value == 3.19
    assert parsed.unit == "g/cm3"
    assert parsed.confidence >= 0.8


def test_parse_capacity_with_rate_prefix():
    parsed = parse_capacity("0.5C_initial_141.2 mA h g⁻¹")

    assert parsed.value == 141.2
    assert parsed.unit == "mAh/g"
    assert parsed.context["rate"] == "0.5C"


def test_parse_retention_cycles():
    parsed = parse_retention("98.5% capacity retention after 500 cycles at 0.2 A g⁻¹")

    assert parsed.value == 98.5
    assert parsed.context["cycles"] == 500


def test_parse_placeholder_has_low_confidence():
    parsed = parse_capacity("discharge_capacity1_10.1021/jp1005692")

    assert parsed.value is None
    assert parsed.confidence == 0


def test_parse_conductivity_s_cm():
    parsed = parse_conductivity("ionic conductivity of 1.2e-3 S/cm")

    assert parsed.value == 1.2e-3
    assert parsed.unit == "S/cm"


def test_parse_density_space_separated_g_cm_minus_three():
    parsed = parse_density("2.41 g cm-3")

    assert parsed.value == 2.41
    assert parsed.unit == "g/cm3"
    assert parsed.confidence >= 0.8


def test_parse_high_capacity_without_numeric_value_has_warning():
    parsed = parse_capacity("high capacity")

    assert parsed.value is None
    assert parsed.confidence == 0
    assert "unparsed" in parsed.warnings
