import json
import math


DEFAULT_CRITERION_KEY = "minimum_issuer_consensus"
IMPORTANCE_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def ensure_assessment_criteria_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assessment_criteria (
            criterion_key TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            weight REAL NOT NULL,
            importance TEXT NOT NULL,
            parameters_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO assessment_criteria (
            criterion_key, enabled, weight, importance, parameters_json
        ) VALUES (?, 1, 6.0, 'high', ?)
        """,
        (
            DEFAULT_CRITERION_KEY,
            json.dumps({"min_issuer_count": 3}, sort_keys=True),
        ),
    )


def importance_rank(value):
    return IMPORTANCE_ORDER.get(value, len(IMPORTANCE_ORDER))


def assess_signal_rows(conn, rows):
    if not _table_exists(conn, "assessment_criteria"):
        return [], ["assessment_criteria table unavailable"]

    criteria_rows = conn.execute(
        """
        SELECT criterion_key, weight, importance, parameters_json
        FROM assessment_criteria
        WHERE enabled = 1
        ORDER BY criterion_key
        """
    ).fetchall()
    criteria, warnings = _validated_criteria(criteria_rows)
    if warnings:
        if not criteria:
            warnings.append("no enabled valid criteria")
        return [], warnings
    if not criteria:
        return [], ["no enabled valid criteria"]

    assessed = []
    for row in rows:
        matched = [
            criterion
            for criterion in criteria
            if _CRITERION_EVALUATORS[criterion["criterion_key"]](
                row, criterion["parameters"]
            )
        ]
        if not matched:
            continue
        assessed_row = dict(row)
        assessed_row["matched_criteria"] = [
            criterion["criterion_key"] for criterion in matched
        ]
        assessed_row["assessment_weight"] = sum(
            criterion["weight"] for criterion in matched
        )
        assessed_row["assessment_importance"] = min(
            (criterion["importance"] for criterion in matched),
            key=importance_rank,
        )
        assessed.append(assessed_row)
    return assessed, warnings


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _validated_criteria(rows):
    valid = []
    warnings = []
    for row in rows:
        criterion_key = row["criterion_key"]
        if criterion_key not in _CRITERION_EVALUATORS:
            warnings.append(f"unknown evaluator: {criterion_key}")
            continue

        weight = row["weight"]
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight < 0
        ):
            warnings.append(f"invalid weight: {criterion_key}")
            continue

        importance = row["importance"]
        if importance not in IMPORTANCE_ORDER:
            warnings.append(f"invalid importance: {criterion_key}")
            continue

        try:
            parameters = json.loads(row["parameters_json"])
        except (TypeError, ValueError):
            warnings.append(f"invalid parameters JSON: {criterion_key}")
            continue
        if not isinstance(parameters, dict) or not _valid_parameters(
            criterion_key, parameters
        ):
            warnings.append(f"invalid parameters: {criterion_key}")
            continue

        valid.append(
            {
                "criterion_key": criterion_key,
                "weight": float(weight),
                "importance": importance,
                "parameters": parameters,
            }
        )
    return valid, warnings


def _valid_parameters(criterion_key, parameters):
    if criterion_key == DEFAULT_CRITERION_KEY:
        threshold = parameters.get("min_issuer_count")
        return (
            not isinstance(threshold, bool)
            and isinstance(threshold, int)
            and threshold >= 1
        )
    return False


def _minimum_issuer_consensus(row, parameters):
    signal_type = str(row.get("signal_type") or "")
    issuers = _json_list(row.get("issuers"))
    return signal_type.startswith("consensus_") and len(issuers) >= parameters[
        "min_issuer_count"
    ]


def _json_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


_CRITERION_EVALUATORS = {
    DEFAULT_CRITERION_KEY: _minimum_issuer_consensus,
}
