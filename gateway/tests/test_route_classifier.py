from app.services.route_classifier import ClassifierDecision, ClassifierThresholdPolicy


def test_threshold_policy_accepts_high_confidence_file_route():
    policy = ClassifierThresholdPolicy(high_confidence=0.8, medium_confidence=0.6)
    decision = ClassifierDecision(
        route="pdf_qa",
        turn_mode="file_only",
        source_scope="pdf",
        confidence=0.91,
        reason_codes=["CLASSIFIER_FILE_QA"],
    )

    assert policy.should_apply(decision=decision, conflicts_with_rule=False) is True


def test_threshold_policy_rejects_mid_conflict_file_route():
    policy = ClassifierThresholdPolicy(high_confidence=0.8, medium_confidence=0.6)
    decision = ClassifierDecision(
        route="pdf_qa",
        turn_mode="file_only",
        source_scope="pdf",
        confidence=0.7,
        reason_codes=["CLASSIFIER_FILE_QA"],
    )

    assert policy.should_apply(decision=decision, conflicts_with_rule=True) is False


def test_threshold_policy_accepts_mid_confidence_kb_route_without_conflict():
    policy = ClassifierThresholdPolicy(high_confidence=0.8, medium_confidence=0.6)
    decision = ClassifierDecision(
        route="kb_qa",
        turn_mode="kb_only",
        source_scope="kb",
        confidence=0.7,
        reason_codes=["CLASSIFIER_KB_QA"],
    )

    assert policy.should_apply(decision=decision, conflicts_with_rule=False) is True
